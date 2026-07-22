import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import settings
from app.bqca.client import chat, create_conversation
from app.renderer.html_generator import build_result_html
from app.storage.gcs import upload_html, generate_query_id
from app.feishu.event import extract_question, get_message_id, get_chat_id
from app.feishu.message import send_text_message, send_result_card

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_processed_messages: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="BQCA Feishu Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/query")
async def api_query(request: Request):
    """
    Core query API.
    Body: {"question": "...", "conversation_id": null (optional)}
    Returns: {"summary", "sql", "fields", "rows", "chart", "html_url"}
    """
    if settings.API_KEY:
        key = request.headers.get("X-API-Key", "") or request.query_params.get("key", "")
        if key != settings.API_KEY:
            return {"error": "unauthorized"}

    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return {"error": "question is required"}

    try:
        result = await asyncio.to_thread(chat, question, body.get("conversation_id"))

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
        }
    except Exception as e:
        logger.error("API query failed: %s", e, exc_info=True)
        return {"error": str(e)}


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

    question = extract_question(event)
    if not question:
        return {"status": "ok"}

    chat_id = get_chat_id(event)
    asyncio.create_task(_process_query(question, chat_id))

    return {"status": "ok"}


async def _process_query(question: str, chat_id: str):
    """Feishu handler: query -> reply in chat."""
    try:
        await send_text_message(chat_id, "正在查询，请稍候...")

        result = await asyncio.to_thread(chat, question)
        logger.info("BQCA result: %d rows, sql=%s, chart=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config))

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
