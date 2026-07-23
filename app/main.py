import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.security import APIKeyHeader, APIKeyQuery

from app.config import settings
from app.bqca.client import chat, create_conversation, KEY_TO_SA
from app.renderer.html_generator import build_result_html
from app.storage.gcs import upload_html, generate_query_id
from app.feishu.event import extract_question, get_message_id, get_chat_id
from app.feishu.message import send_text_message, send_result_card

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_processed_messages: set[str] = set()

# chat_id / API session → (conversation_name, last_active_timestamp)
_session_store: dict[str, tuple[str, float]] = {}
SESSION_TTL = 1800  # 30 minutes

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_query = APIKeyQuery(name="key", auto_error=False)


def _cleanup_sessions() -> None:
    """Remove expired sessions from the store."""
    now = time.time()
    expired = [k for k, (_, ts) in _session_store.items() if now - ts > SESSION_TTL]
    for k in expired:
        logger.info("Session expired: %s", k)
        del _session_store[k]


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


async def verify_api_key(
    header_key: str = Depends(_api_key_header),
    query_key: str = Depends(_api_key_query),
):
    """Verify API key if API_KEY is configured. Always pass through key for SA impersonation."""
    key = header_key or query_key or ""

    if not settings.API_KEY:
        # No global API_KEY set — still accept keys from KEY_TO_SA for SA impersonation
        return key if key in KEY_TO_SA else True

    if key != settings.API_KEY and key not in KEY_TO_SA:
        raise HTTPException(status_code=401, detail="unauthorized")
    return key or True


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
async def api_query(request: Request, _auth=Depends(verify_api_key)):
    """
    Core query API.
    Header: X-API-Key (required if API_KEY configured)
    Body: {"question": "...", "conversation_id": null (optional, auto-managed if omitted)}
    Returns: {"summary", "sql", "fields", "rows", "chart", "html_url", "conversation_id"}
    """
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    # Resolve conversation: explicit > session-stored > new
    conversation_name = body.get("conversation_id")
    if not conversation_name:
        # Use client IP + API key as a lightweight session key
        session_key = f"api:{request.client.host}:{_auth if isinstance(_auth, str) else settings.API_KEY}"
        conversation_name = _get_conversation(session_key)

    try:
        result = await asyncio.to_thread(chat, question, conversation_name, _auth if isinstance(_auth, str) else None)

        # Save conversation for future follow-ups
        session_key = f"api:{request.client.host}:{_auth if isinstance(_auth, str) else settings.API_KEY}"
        _save_conversation(session_key, result.conversation_name)

        html_url = None
        if result.rows or result.vega_config:
            html = build_result_html(question, result)
            query_id = generate_query_id()
            html_url = await upload_html(query_id, html)

        return {
            "summary": result.summary,
            "sql": result.sql,
            "fields": result.fields,
            "rows": result.rows[:50],
            "chart": bool(result.vega_config),
            "html_url": html_url,
            "conversation_id": result.conversation_name,
        }
    except Exception as e:
        logger.error("API query failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

        html = build_result_html(question, result)
        query_id = generate_query_id()
        url = await upload_html(query_id, html)
        logger.info("Result URL: %s", url)

        await send_result_card(chat_id, result.summary or "查询完成，点击查看详情。", url)

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
        except Exception:
            pass
