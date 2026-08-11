import asyncio
from dataclasses import dataclass
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException

from app.config import settings
from app.bqca.client import chat, extract_html_from_summary
from app.storage.gcs import upload_html, generate_query_id
from app.adapters import get_card_adapter
from app.adapters.feishu import extract_thoughts_and_summary
from app.feishu.event import (
    extract_app_id,
    extract_question,
    get_chat_id,
    get_event_id,
    get_message_id,
    get_sender_id,
    is_bot_mentioned,
    is_event_expired,
    parse_card_action,
)
from app.feishu.message import send_text_message, send_result_card, send_premium_result_card
from app.storage.sqlite import (
    init_db,
    claim_message_processing,
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
FEISHU_EVENT_MAX_AGE_SECONDS = 600  # Demo guard against delayed callback retries

# Throttle interval for streaming insight PATCHes (Feishu rate-limits whole-card PATCH).
# SUMMARY events are block-level and can arrive in bursts; ~800ms coalesces them while
# still feeling near-real-time. The last pending flush always fires (trailing edge).
SUMMARY_PATCH_THROTTLE_SECONDS = 0.8

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


def _scoped_conversation_key(session_key: str, domain: str) -> str:
    return f"{domain}:{session_key}"


def _resolve_demo_role(role: str | None) -> str | None:
    if not role:
        return None
    normalized = ROLE_ALIASES.get(role.strip(), role.strip())
    return normalized if normalized in DEMO_ROLES else None


def _get_feishu_role(session_id: str) -> str | None:
    role, _, _ = get_role_session(session_id, SESSION_TTL)
    return role


def _save_feishu_role(session_id: str, role: str) -> None:
    save_role_session(session_id, role)


def _get_feishu_conversation(session_id: str) -> tuple[str | None, str | None]:
    """Returns (conversation_name, last_domain)."""
    _, conversation_name, last_domain = get_role_session(session_id, SESSION_TTL)
    return conversation_name, last_domain


def _save_feishu_conversation(session_id: str, conversation_name: str, last_domain: str | None = None) -> None:
    # Fetch existing role to preserve it while updating conversation_name
    role = _get_feishu_role(session_id)
    if role:
        save_role_session(session_id, role, conversation_name, last_domain)


def _service_account_for_role(role: str) -> str | None:
    if role == "运营经理":
        return None
    return settings.BQCA_SUPPORT_SERVICE_ACCOUNT


@dataclass(frozen=True)
class BQCAAgentConfig:
    agent_id: str
    location: str
    display_name: str
    domain: str


def get_agent_config(domain: str | None = None, app_id: str | None = None) -> BQCAAgentConfig:
    d = (domain or "").lower()
    if d == "game" or (app_id and settings.GAME_FEISHU_APP_ID and app_id == settings.GAME_FEISHU_APP_ID):
        return BQCAAgentConfig(
            agent_id=settings.GAME_CA_AGENT_ID or "game-analyst-cn",
            location=settings.GAME_CA_LOCATION or "global",
            display_name="Flood-It! 游戏数据洞察专家",
            domain="game",
        )
    return BQCAAgentConfig(
        agent_id=settings.CA_AGENT_ID,
        location=settings.CA_LOCATION,
        display_name="电商数据 Agent",
        domain="ecommerce",
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
    Body: {"question", "role", "session_id", "conversation_id", "domain"}
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
        # Compliance with SKILL.md: default to '运营经理' for first queries when no role is given
        role = "运营经理"

    # If the role is switched, clear the current conversation to avoid cross-talk
    if requested_role is not None and requested_role != saved_role:
        clear_role_conversation(session_id)

    _save_feishu_role(session_id, role)
    conversation_name, last_domain = _get_feishu_conversation(session_id)

    requested_domain = (body.get("domain") or body.get("app") or "").lower()
    domain = "game" if requested_domain == "game" else "ecommerce"
    if last_domain and last_domain != domain:
        # Cross-talk Prevention: if the queried domain changes (e.g. game -> ecommerce), clear conversation context
        clear_role_conversation(session_id)
        conversation_name = None

    if not question:
        if requested_role is not None:
            return {
                "message": f"已切换为{role}",
                "session_id": session_id,
                "conversation_id": conversation_name,
                "role": role,
            }
        raise HTTPException(status_code=400, detail="question is required")

    agent_cfg = get_agent_config(domain)
    target_service_account = _service_account_for_role(role)

    try:
        result = await asyncio.to_thread(
            chat,
            question,
            conversation_name,
            target_service_account=target_service_account,
            agent_id=agent_cfg.agent_id,
            location=agent_cfg.location,
        )
        _save_feishu_conversation(session_id, result.conversation_name, last_domain=domain)

        return {
            "summary": result.summary,
            "sql": result.sql,
            "fields": result.fields,
            "rows": result.rows[:50],
            "chart": bool(result.vega_config),
            "vega_config": result.vega_config,
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

    # Feishu URL verification
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    event = body.get("event", {})
    event_id = get_event_id(body)
    msg_id = get_message_id(event)
    if is_event_expired(body, FEISHU_EVENT_MAX_AGE_SECONDS):
        logger.warning("Skipping expired Feishu event: event_id=%s msg_id=%s", event_id, msg_id)
        return {"status": "ok"}

    if not claim_message_processing(msg_id, event_id):
        logger.info("Skipping duplicate Feishu event: event_id=%s msg_id=%s", event_id, msg_id)
        return {"status": "ok"}

    # Handle Feishu Card Action Event (One-click quick query)
    next_query, target_id = parse_card_action(body, get_chat_type, _get_conversation)
    if next_query and target_id:
        logger.info("Feishu Card Action click: %r target_id %s, app_id %s", next_query, target_id, app_id)
        asyncio.create_task(_process_query(next_query, target_id, app_id=app_id))
        return {}

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


class _ThrottledSummaryPatch:
    """Coalescing trailing-edge throttle for streaming partial-summary PATCH calls.

    Feishu rate-limits whole-card PATCH. SUMMARY events are block-level and can arrive
    in bursts; this ensures at most one in-flight PATCH per throttle window while always
    flushing the latest accumulated text on the trailing edge.
    """

    def __init__(self, adapter, message_id: str, question: str, app_id: str | None,
                 interval: float = SUMMARY_PATCH_THROTTLE_SECONDS):
        self._adapter = adapter
        self._message_id = message_id
        self._question = question
        self._app_id = app_id
        self._interval = interval
        self._thoughts: list[str] = []
        self._partial_summary: str = ""
        self._stage: str = ""
        self._task: asyncio.Task | None = None

    def update(self, *, thoughts: list[str] | None = None, partial_summary: str | None = None,
               stage: str | None = None) -> None:
        if thoughts is not None:
            self._thoughts = thoughts
        if partial_summary is not None:
            self._partial_summary = partial_summary
        if stage is not None:
            self._stage = stage

    async def _flush(self) -> None:
        await asyncio.sleep(self._interval)
        self._task = None
        try:
            await self._adapter.patch_partial_summary(
                self._message_id,
                self._question,
                self._thoughts,
                self._partial_summary,
                self._stage,
                app_id=self._app_id,
            )
        except Exception as e:
            logger.warning("Throttled partial summary PATCH failed: %s", e)

    def schedule(self) -> None:
        """Schedule a trailing-edge flush; coalesces repeated calls within the window."""
        if self._task is None:
            self._task = asyncio.create_task(self._flush())

    async def flush_now(self) -> None:
        """Cancel any pending trailing flush and PATCH the latest state immediately."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            await self._adapter.patch_partial_summary(
                self._message_id,
                self._question,
                self._thoughts,
                self._partial_summary,
                self._stage,
                app_id=self._app_id,
            )
        except Exception as e:
            logger.warning("Immediate partial summary PATCH failed: %s", e)

    def cancel(self) -> None:
        """Drop any pending trailing flush without PATCHing (used before final card)."""
        if self._task is not None:
            self._task.cancel()
            self._task = None


async def _process_query(question: str, chat_id: str, platform: str = "feishu", app_id: str | None = None):
    """Query BQCA with real-time stream events and 3-stage in-place PATCH card updates."""
    adapter = get_card_adapter(platform)
    agent_cfg = get_agent_config(app_id=app_id)
    try:
        # Stage 1: Send initial loading card in 0.5s and get message_id for in-place PATCH
        message_id = await adapter.send_initial_card(chat_id, question, app_id=app_id)

        session_key = _scoped_conversation_key(chat_id, agent_cfg.domain)
        conversation_name = _get_conversation(session_key)

        from app.bqca.client import chat_stream_events, BQCAEventType

        final_result = None
        sql_updated = False
        data_updated = False

        async for event in chat_stream_events(
            question,
            conversation_name=conversation_name,
            agent_id=agent_cfg.agent_id,
            location=agent_cfg.location,
        ):
            if event.result and event.result.conversation_name:
                _save_conversation(session_key, event.result.conversation_name)

            if event.event_type == BQCAEventType.SQL and not sql_updated and message_id:
                sql_updated = True
                await adapter.patch_progress_card(
                    message_id,
                    question,
                    "⚡ 已生成 BigQuery SQL，正在查询数据库...",
                    sql=event.data,
                    app_id=app_id,
                )

            elif event.event_type == BQCAEventType.DATA and not data_updated and message_id:
                data_updated = True
                row_count = len(event.data) if isinstance(event.data, list) else 0
                await adapter.patch_progress_card(
                    message_id,
                    question,
                    f"📊 数据库查询成功（已获取 {row_count} 行数据），正在生成商业洞察与图表...",
                    sql=event.result.sql if event.result else None,
                    app_id=app_id,
                )

            elif event.event_type == BQCAEventType.FINAL:
                final_result = event.result

        if not final_result:
            logger.error("No final BQCA result returned for question: %s", question)
            if message_id:
                await adapter.patch_progress_card(
                    message_id,
                    question,
                    "⚠️ 查询处理超时或服务繁忙，请稍后再试或换种说法。",
                    app_id=app_id,
                )
            return

        result = final_result

        # Save final conversation name
        _save_conversation(session_key, result.conversation_name)

        logger.info("BQCA final result: %d rows, sql=%s, chart=%s, convo=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config),
                     result.conversation_name[-20:] if result.conversation_name else "none")

        if "LOGIC_EXPLANATION" in result.summary or "BUSINESS_INSIGHTS" in result.summary:
            summary = result.summary
        else:
            extra_thoughts, summary = extract_thoughts_and_summary(result.summary)
            if extra_thoughts:
                result.thinking_process.extend(extra_thoughts)

        if not result.recommended_questions:
            if agent_cfg.domain == "game":
                result.recommended_questions = [
                    "分析近期 DAU 与玩家活跃趋势",
                    "查看玩家留存率变化",
                    "查询失败次数最高的关卡",
                ]
            else:
                result.recommended_questions = [
                    "各订单状态的占比情况",
                    "分析近一年销售额 Top 5 的国家",
                    "查询退货数最高的前 5 类商品",
                ]

        html_code, clean_summary = extract_html_from_summary(summary)
        result_url = None
        if html_code:
            try:
                query_id = generate_query_id()
                result_url = await upload_html(query_id, html_code)
                logger.info("Uploaded native BQCA HTML report to GCS: %s", result_url)
            except Exception as gcs_err:
                logger.warning("Failed to upload HTML report to GCS: %s", gcs_err)

        formatted_summary = adapter.format_summary(clean_summary)

        # Stage 3: In-place PATCH final result card with VChart and Action Buttons
        if message_id:
            await adapter.patch_final_card(
                message_id=message_id,
                question=question,
                summary=formatted_summary or ("未查询到相关数据，请换个说法试试。" if not result.rows and not result.vega_config else "查询完成。"),
                sql=result.sql,
                fields=result.fields,
                rows=result.rows,
                recommended_questions=result.recommended_questions,
                result_url=result_url,
                vega_config=result.vega_config,
                app_id=app_id,
            )
        else:
            await adapter.send_result_card(
                target_id=chat_id,
                question=question,
                summary=formatted_summary or ("未查询到相关数据，请换个说法试试。" if not result.rows and not result.vega_config else "查询完成。"),
                sql=result.sql,
                fields=result.fields,
                rows=result.rows,
                recommended_questions=result.recommended_questions,
                result_url=result_url,
                vega_config=result.vega_config,
                app_id=app_id,
            )

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await adapter.send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。", app_id=app_id)
        except Exception:
            pass
