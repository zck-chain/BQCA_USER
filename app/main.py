import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException

from app.config import settings
from app.bqca.client import chat
# TODO: HTML 生成功能暂时注释，后续改为让 BQCA 直接生成前端代码
# from app.renderer.html_generator import build_result_html
# from app.storage.gcs import upload_html, generate_query_id
from app.feishu.event import extract_question, get_message_id, get_chat_id
from app.feishu.message import send_text_message, send_result_card

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_processed_messages: set[str] = set()

# chat_id / API session → (conversation_name, last_active_timestamp)
_session_store: dict[str, tuple[str, float]] = {}
# Demo session ID → (selected role, last_active_timestamp)
_feishu_role_sessions: dict[str, tuple[str, float]] = {}
# Demo session ID → BQCA conversation name for the selected role
_feishu_conversations: dict[str, str] = {}
SESSION_TTL = 1800  # 30 minutes

DEMO_ROLES = {"运营经理", "一线客服"}
ROLE_ALIASES = {
    "经理": "运营经理",
    "管理员": "运营经理",
    "客服": "一线客服",
    "物流": "一线客服",
}

def _cleanup_sessions() -> None:
    """Remove expired sessions from the store."""
    now = time.time()
    expired = [k for k, (_, ts) in _session_store.items() if now - ts > SESSION_TTL]
    for k in expired:
        logger.info("Session expired: %s", k)
        del _session_store[k]

    expired_role_sessions = [
        key for key, (_, ts) in _feishu_role_sessions.items() if now - ts > SESSION_TTL
    ]
    for key in expired_role_sessions:
        del _feishu_role_sessions[key]
        _feishu_conversations.pop(key, None)


def _get_conversation(session_key: str) -> str | None:
    """Get an active conversation name for a session key, or None if expired."""
    entry = _session_store.get(session_key)
    if entry is None:
        return None
    convo_name, ts = entry
    if time.time() - ts > SESSION_TTL:
        del _session_store[session_key]
        return None
    return convo_name


def _save_conversation(session_key: str, conversation_name: str) -> None:
    """Save or refresh a session mapping."""
    _session_store[session_key] = (conversation_name, time.time())


def _resolve_demo_role(role: str | None) -> str | None:
    if not role:
        return None
    normalized = ROLE_ALIASES.get(role.strip(), role.strip())
    return normalized if normalized in DEMO_ROLES else None


def _get_feishu_role(session_id: str) -> str | None:
    entry = _feishu_role_sessions.get(session_id)
    if entry is None:
        return None
    role, timestamp = entry
    if time.time() - timestamp > SESSION_TTL:
        del _feishu_role_sessions[session_id]
        return None
    return role


def _save_feishu_role(session_id: str, role: str) -> None:
    _feishu_role_sessions[session_id] = (role, time.time())


def _get_feishu_conversation(session_id: str) -> str | None:
    return _feishu_conversations.get(session_id)


def _save_feishu_conversation(session_id: str, conversation_name: str) -> None:
    _feishu_conversations[session_id] = conversation_name


def _service_account_for_role(role: str) -> str | None:
    if role == "运营经理":
        return None
    return settings.BQCA_SUPPORT_SERVICE_ACCOUNT


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Periodic session cleanup
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(300)
            _cleanup_sessions()

    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="BQCA Feishu Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/query")
async def api_query(request: Request):
    """
    Role-based query API for the Demo skill.
    Body: {"question", "role", "session_id", "conversation_id"}
    """
    body = await request.json()
    question = body.get("question", "").strip()
    session_id = body.get("session_id") or uuid.uuid4().hex
    requested_role = _resolve_demo_role(body.get("role"))

    if body.get("role") and requested_role is None:
        raise HTTPException(status_code=400, detail="unsupported role")

    saved_role = _get_feishu_role(session_id)
    role = requested_role or saved_role
    if role is None:
        raise HTTPException(status_code=400, detail="role is required for a new session")

    if requested_role is not None and requested_role != saved_role:
        _feishu_conversations.pop(session_id, None)

    _save_feishu_role(session_id, role)
    conversation_name = _get_feishu_conversation(session_id)
    if not question:
        if requested_role is not None:
            return {
                "message": f"已切换为{role}",
                "session_id": session_id,
                "conversation_id": conversation_name,
                "role": role,
            }
        raise HTTPException(status_code=400, detail="question is required")

    try:
        result = await asyncio.to_thread(
            chat,
            question,
            conversation_name,
            target_service_account=_service_account_for_role(role),
        )
        _save_feishu_conversation(session_id, result.conversation_name)

        return {
            "summary": result.summary,
            "sql": result.sql,
            "fields": result.fields,
            "rows": result.rows[:50],
            "chart": bool(result.vega_config),
            "html_url": None,  # TODO: 恢复 html_url
            "session_id": session_id,
            "conversation_id": result.conversation_name,
            "role": role,
        }
    except Exception as e:
        logger.error("API query failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="query failed")


@app.post("/webhook/event")
async def webhook_event(request: Request):
    body = await request.json()

    # Feishu URL verification
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Handle message event
    event = body.get("event", {})

    msg_id = get_message_id(event)
    if msg_id in _processed_messages:
        return {"status": "ok"}
    _processed_messages.add(msg_id)

    logger.info("Feishu event: %s", json.dumps(event, ensure_ascii=False)[:500])
    question = extract_question(event)
    if not question:
        return {"status": "ok"}

    chat_id = get_chat_id(event)
    asyncio.create_task(_process_query(question, chat_id))

    return {"status": "ok"}


async def _process_query(question: str, chat_id: str):
    """Feishu handler: query -> reply in chat, with session-based follow-up."""
    try:
        await send_text_message(chat_id, "正在查询，请稍候...")

        # Reuse conversation for the same Feishu chat
        conversation_name = _get_conversation(chat_id)
        result = await asyncio.to_thread(chat, question, conversation_name)

        # Save conversation for follow-up questions
        _save_conversation(chat_id, result.conversation_name)

        logger.info("BQCA result: %d rows, sql=%s, chart=%s, convo=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config),
                     result.conversation_name[-20:] if result.conversation_name else "none")

        if not result.rows and not result.vega_config:
            await send_text_message(chat_id, result.summary or "未查询到相关数据，请换个说法试试。")
            return

        # TODO: HTML 生成暂时注释，直接发送文本结果
        # html = build_result_html(question, result)
        # query_id = generate_query_id()
        # url = await upload_html(query_id, html)
        # logger.info("Result URL: %s", url)
        # await send_result_card(chat_id, result.summary or "查询完成，点击查看详情。", url)

        await send_text_message(chat_id, result.summary or "查询完成。")

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
        except Exception:
            pass
