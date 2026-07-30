import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException

from app.config import settings
from app.bqca.client import chat, extract_html_from_summary
from app.storage.gcs import upload_html, generate_query_id
from app.adapters import get_card_adapter
from app.adapters.feishu import extract_thoughts_and_summary
from app.feishu.event import extract_question, get_message_id, get_chat_id, get_sender_id, parse_card_action, extract_app_id, is_bot_mentioned
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
    save_chat_type,
    get_chat_type,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_TTL = 86400 * 30  # 30 days

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


@dataclass(frozen=True)
class BQCAAgentConfig:
    agent_id: str
    location: str
    display_name: str


def get_agent_config(domain: str | None = None, app_id: str | None = None) -> BQCAAgentConfig:
    d = (domain or "").lower()
    if d == "game" or (app_id and settings.GAME_FEISHU_APP_ID and app_id == settings.GAME_FEISHU_APP_ID):
        return BQCAAgentConfig(
            agent_id=settings.GAME_CA_AGENT_ID or "game-analyst-cn",
            location=settings.GAME_CA_LOCATION or "global",
            display_name="Flood-It! 游戏数据洞察专家",
        )
    return BQCAAgentConfig(
        agent_id=settings.CA_AGENT_ID,
        location=settings.CA_LOCATION,
        display_name="电商数据 Agent",
    )



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

    domain = (body.get("domain") or body.get("app") or "").lower()
    agent_cfg = get_agent_config(domain)

    try:
        result = await asyncio.to_thread(
            chat,
            question,
            conversation_name,
            target_service_account=_service_account_for_role(role),
            agent_id=agent_cfg.agent_id,
            location=agent_cfg.location,
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
    app_id = extract_app_id(body)

    # Handle Feishu Card Action Event (One-click quick query)
    next_query, target_id = parse_card_action(body, get_chat_type, _get_conversation)
    if next_query and target_id:
        logger.info("Feishu Card Action click: %r target_id %s, app_id %s", next_query, target_id, app_id)
        asyncio.create_task(_process_query(next_query, target_id, app_id=app_id))
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

    # Ignore group messages where the bot is not explicitly mentioned (e.g. @_all)
    if not is_bot_mentioned(event):
        logger.info("Skipping event: bot not mentioned or @_all only")
        return {"status": "ok"}

    question = extract_question(event)
    if not question:
        return {"status": "ok"}

    chat_id = get_chat_id(event)
    chat_type = event.get("message", {}).get("chat_type", "")
    if chat_id and chat_type:
        save_chat_type(chat_id, chat_type)

    if chat_type == "p2p":
        target_id = get_sender_id(event) or chat_id
    else:
        target_id = chat_id

    asyncio.create_task(_process_query(question, target_id, app_id=app_id))

    return {"status": "ok"}


async def _process_query(question: str, chat_id: str, platform: str = "feishu", app_id: str | None = None):
    """Query BQCA and reply using platform Card Adapter (Feishu, DingTalk, WeCom, etc.)."""
    adapter = get_card_adapter(platform)
    agent_cfg = get_agent_config(app_id=app_id)
    try:
        await adapter.send_text_message(chat_id, "正在查询，请稍候...", app_id=app_id)

        # Reuse conversation for the same chat room
        conversation_name = _get_conversation(chat_id)
        result = await asyncio.to_thread(
            chat,
            question,
            conversation_name,
            agent_id=agent_cfg.agent_id,
            location=agent_cfg.location,
        )

        # Save conversation for follow-up questions
        _save_conversation(chat_id, result.conversation_name)

        logger.info("BQCA result: %d rows, sql=%s, chart=%s, convo=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config),
                     result.conversation_name[-20:] if result.conversation_name else "none")

        # Split BQCA text into English thinking lines and Chinese summary
        thoughts, summary = extract_thoughts_and_summary(result.summary)
        if thoughts:
            result.thinking_process.extend(thoughts)

        # Fallback recommended questions if BQCA returned empty
        if not result.recommended_questions:
            result.recommended_questions = [
                "各订单状态的占比情况",
                "分析近一年销售额 Top 5 的国家",
                "查询退货数最高的前 5 类商品"
            ]

        # Extract native BQCA HTML code block if present and upload to GCS
        html_code, clean_summary = extract_html_from_summary(summary)
        result_url = None
        if html_code:
            try:
                query_id = generate_query_id()
                result_url = await upload_html(query_id, html_code)
                logger.info("Uploaded native BQCA HTML report to GCS: %s", result_url)
            except Exception as gcs_err:
                logger.warning("Failed to upload HTML report to GCS: %s", gcs_err)

        # Format summary via Card Adapter
        formatted_summary = adapter.format_summary(clean_summary)

        await adapter.send_result_card(
            target_id=chat_id,
            question=question,
            summary=formatted_summary or ("未查询到相关数据，请换个说法试试。" if not result.rows and not result.vega_config else "查询完成。"),
            sql=result.sql,
            fields=result.fields,
            rows=result.rows,
            recommended_questions=result.recommended_questions,
            result_url=result_url,
            app_id=app_id,
        )

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await adapter.send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。", app_id=app_id)
        except Exception:
            pass
