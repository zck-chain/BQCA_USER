import asyncio
import json
import logging
import re
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
from app.feishu.message import send_text_message, send_result_card, send_premium_result_card
from app.storage.sqlite import (
    init_db,
    is_message_processed,
    add_processed_message,
    get_chat_conversation,
    save_chat_conversation,
    get_role_session,
    save_role_session,
    clear_role_conversation,
    cleanup_expired_sessions,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_TTL = 1800  # 30 minutes

DEMO_ROLES = {"运营经理", "一线客服"}
ROLE_ALIASES = {
    "经理": "运营经理",
    "管理员": "运营经理",
    "客服": "一线客服",
    "物流": "一线客服",
}


def _get_conversation(session_key: str) -> str | None:
    """Get an active conversation name for a session key, or None if expired."""
    return get_chat_conversation(session_key, SESSION_TTL)


def _save_conversation(session_key: str, conversation_name: str) -> None:
    """Save or refresh a session mapping."""
    save_chat_conversation(session_key, conversation_name)


def _resolve_demo_role(role: str | None) -> str | None:
    if not role:
        return None
    normalized = ROLE_ALIASES.get(role.strip(), role.strip())
    return normalized if normalized in DEMO_ROLES else None


def _get_feishu_role(session_id: str) -> str | None:
    role, _ = get_role_session(session_id, SESSION_TTL)
    return role


def _save_feishu_role(session_id: str, role: str) -> None:
    save_role_session(session_id, role)


def _get_feishu_conversation(session_id: str) -> str | None:
    _, conversation_name = get_role_session(session_id, SESSION_TTL)
    return conversation_name


def _save_feishu_conversation(session_id: str, conversation_name: str) -> None:
    # Fetch existing role to preserve it while updating conversation_name
    role = _get_feishu_role(session_id)
    if role:
        save_role_session(session_id, role, conversation_name)


def _service_account_for_role(role: str) -> str | None:
    if role == "运营经理":
        return None
    return settings.BQCA_SUPPORT_SERVICE_ACCOUNT


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise SQLite tables on startup
    init_db()

    # Periodic session cleanup
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(300)
            cleanup_expired_sessions(SESSION_TTL)

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

    # If the role is switched, clear the current conversation to avoid cross-talk
    if requested_role is not None and requested_role != saved_role:
        clear_role_conversation(session_id)

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

    # Handle Feishu Card Action Event (One-click quick query)
    if "action" in body and "open_chat_id" in body:
        action_val = body["action"].get("value", {})
        if action_val.get("action") == "quick_query":
            next_query = action_val.get("query")
            chat_id = body.get("open_chat_id")
            logger.info("Feishu Card Action click: %r in chat %s", next_query, chat_id)
            asyncio.create_task(_process_query(next_query, chat_id))
            return {}

    # Feishu URL verification
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Handle message event
    event = body.get("event", {})

    msg_id = get_message_id(event)
    if is_message_processed(msg_id):
        return {"status": "ok"}
    add_processed_message(msg_id)

    logger.info("Feishu event: %s", json.dumps(event, ensure_ascii=False)[:500])
    question = extract_question(event)
    if not question:
        return {"status": "ok"}

    chat_id = get_chat_id(event)
    asyncio.create_task(_process_query(question, chat_id))

    return {"status": "ok"}


def extract_thoughts_and_summary(raw_text: str) -> tuple[list[str], str]:
    """Split the raw BQCA summary text into a list of English thought paragraphs
    and the final Chinese report based on Chinese character density.
    This prevents English reasoning from leaking into the final report while
    fully populating the collapsible thinking panel.
    """
    if not raw_text:
        return [], ""
    
    paragraphs = raw_text.split("\n")
    thoughts = []
    chinese_paragraphs = []
    
    found_chinese_report = False
    
    for p in paragraphs:
        stripped = p.strip()
        if not stripped:
            continue
            
        # Calculate Chinese character density
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', stripped))
        ratio = chinese_chars / len(stripped) if len(stripped) > 0 else 0
        
        # If a paragraph has high Chinese density (> 20%), it starts the Chinese report
        if ratio > 0.20:
            found_chinese_report = True
            
        if found_chinese_report:
            chinese_paragraphs.append(p)
        else:
            thoughts.append(p)
            
    return thoughts, "\n\n".join(chinese_paragraphs).strip()


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

        # Split BQCA text into English thinking lines and Chinese summary
        thoughts, summary = extract_thoughts_and_summary(result.summary)
        if thoughts:
            result.thinking_process.extend(thoughts)

        if not result.rows and not result.vega_config:
            await send_premium_result_card(chat_id, question, result, summary or "未查询到相关数据，请换个说法试试。")
            return

        # TODO: HTML 生成暂时注释，直接发送文本结果
        # html = build_result_html(question, result)
        # query_id = generate_query_id()
        # url = await upload_html(query_id, html)
        # logger.info("Result URL: %s", url)
        # await send_result_card(chat_id, result.summary or "查询完成，点击查看详情。", url)

        await send_premium_result_card(chat_id, question, result, summary or "查询完成。")

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
        except Exception:
            pass
