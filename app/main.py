import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse

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


@app.get("/api/stream-dumps")
async def list_stream_dumps():
    """List all stream dump JSON files generated inside the Cloud Run container."""
    scratch_dir = Path(__file__).resolve().parent.parent / "scratch"
    if not scratch_dir.exists():
        return {"files": []}
    files = sorted([f.name for f in scratch_dir.glob("stream_dump_*.json")], reverse=True)
    return {"files": files}


@app.get("/api/stream-dumps/{filename}")
async def get_stream_dump(filename: str):
    """Download a specific stream dump JSON file generated inside the Cloud Run container."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    scratch_dir = Path(__file__).resolve().parent.parent / "scratch"
    file_path = scratch_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stream dump file not found")
    return FileResponse(path=file_path, filename=filename, media_type="application/json")


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
            "first_chunk_latency": result.first_chunk_latency,
            "role": role,
        }
    except Exception as e:
        logger.error("API query failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="query failed")


@app.post("/api/benchmark")
async def api_benchmark(request: Request):
    """
    In-container performance benchmark endpoint (Ecommerce domain).
    Executes 2 distinct layers concurrently in the same Cloud Run environment:
    1. Pure BQCA SDK call (chat).
    2. Real /api/query endpoint handler (api_query).
    """
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required in request body")

    results = {
        "question": question,
        "domain": "ecommerce",
        "environment": "Google Cloud Run Container (Same Intra-VPC Network)",
        "mode": "Concurrent Execution (asyncio.gather)",
    }
    
    async def run_step_1():
        t0 = time.perf_counter()
        try:
            agent_cfg = get_agent_config("ecommerce")
            sdk_res = await asyncio.to_thread(
                chat,
                question,
                None,  # 建立全新的独立对话
                agent_id=agent_cfg.agent_id,
                location=agent_cfg.location,
            )
            t_sdk = time.perf_counter() - t0
            return {
                "name": "1. BQCA SDK 直连 (chat)",
                "duration_seconds": round(t_sdk, 3),
                "duration_ms": round(t_sdk * 1000, 0),
                "rows_count": len(sdk_res.rows),
                "has_sql": bool(sdk_res.sql),
                "success": True,
            }
        except Exception as e:
            return {"name": "1. BQCA SDK 直连 (chat)", "success": False, "error": str(e)}

    async def run_step_2():
        t0 = time.perf_counter()
        try:
            mock_api_req = Request(
                scope={
                    "type": "http",
                    "method": "POST",
                    "path": "/api/query",
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            mock_api_req._json = {
                "question": question,
                "domain": "ecommerce",
                "session_id": f"bm_session_{uuid.uuid4().hex}"
            }
            query_resp = await api_query(mock_api_req)
            t_query = time.perf_counter() - t0
            return {
                "name": "2. /api/query 路由处理函数 (api_query)",
                "duration_seconds": round(t_query, 3),
                "duration_ms": round(t_query * 1000, 0),
                "has_sql": bool(query_resp.get("sql")),
                "has_chart": bool(query_resp.get("vega_config")),
                "success": True,
            }
        except Exception as e:
            return {"name": "2. /api/query 路由处理函数 (api_query)", "success": False, "error": str(e)}

    t_global_start = time.perf_counter()
    # ⚡ 使用 asyncio.gather 实现步骤 1 与 步骤 2 瞬间完全同时并发发起
    res_1, res_2 = await asyncio.gather(run_step_1(), run_step_2())
    total_parallel_time = round(time.perf_counter() - t_global_start, 3)

    results["step_1_sdk_direct"] = res_1
    results["step_2_api_query"] = res_2
    results["total_parallel_wall_time_seconds"] = total_parallel_time

    if res_1.get("success") and res_2.get("success"):
        s1 = res_1["duration_seconds"]
        s2 = res_2["duration_seconds"]
        overhead = round(s2 - s1, 3)
        results["overhead_analysis"] = {
            "sdk_time": f"{s1}s",
            "api_query_time": f"{s2}s",
            "framework_and_code_overhead": f"{overhead}s ({round(overhead * 1000, 0)}ms)",
            "total_parallel_time": f"{total_parallel_time}s",
            "conclusion": "FastAPI 代码层开销极小，表现优秀！" if abs(overhead) < 0.5 else "观察数据格式化或内部 Session 保存开销。",
        }

    return results


@app.post("/webhook/event")
async def webhook_event(request: Request):
    t_webhook_start = time.perf_counter()
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
        t_handshake = time.perf_counter() - t_webhook_start
        logger.info("⏱️ [TELEMETRY-HANDSHAKE] /webhook/event Card Action HTTP 200 Handshake: %.3f s (%.0f ms)", t_handshake, t_handshake * 1000)
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

    t_handshake = time.perf_counter() - t_webhook_start
    logger.info("⏱️ [TELEMETRY-HANDSHAKE] /webhook/event Message Event HTTP 200 Handshake: %.3f s (%.0f ms)", t_handshake, t_handshake * 1000)

    return {"status": "ok"}


async def _process_query(question: str, chat_id: str, platform: str = "feishu", app_id: str | None = None):
    """Query BQCA and reply using platform Card Adapter (Feishu, DingTalk, WeCom, etc.)."""
    t_pipe_start = time.perf_counter()
    adapter = get_card_adapter(platform)
    agent_cfg = get_agent_config(app_id=app_id)
    try:
        await adapter.send_text_message(chat_id, "正在查询，请稍候...", app_id=app_id)
        t_ack = time.perf_counter()
        logger.info("⏱️ [TELEMETRY-ACK] Initial progress message sent to Feishu: +%.3f s (+%.0f ms)", t_ack - t_pipe_start, (t_ack - t_pipe_start) * 1000)

        session_key = _scoped_conversation_key(chat_id, agent_cfg.domain)
        conversation_name = _get_conversation(session_key)

        t_bqca_start = time.perf_counter()
        result = await asyncio.to_thread(
            chat,
            question,
            conversation_name,
            agent_id=agent_cfg.agent_id,
            location=agent_cfg.location,
        )
        t_bqca_end = time.perf_counter()
        logger.info("⏱️ [TELEMETRY-BQCA] BQCA Chat execution finished: +%.3f s (BQCA API duration: %.3f s)", t_bqca_end - t_pipe_start, t_bqca_end - t_bqca_start)

        # Save conversation for follow-up questions
        _save_conversation(session_key, result.conversation_name)

        logger.info("BQCA result: %d rows, sql=%s, chart=%s, convo=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config),
                     result.conversation_name[-20:] if result.conversation_name else "none")

        # Split BQCA text into English thinking lines and Chinese summary (Bypassed if using new structured Map-keys)
        if "LOGIC_EXPLANATION" in result.summary or "BUSINESS_INSIGHTS" in result.summary:
            summary = result.summary
        else:
            thoughts, summary = extract_thoughts_and_summary(result.summary)
            if thoughts:
                result.thinking_process.extend(thoughts)

        # Fallback recommended questions if BQCA returned empty
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

        t_card_start = time.perf_counter()
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
        t_pipe_end = time.perf_counter()

        # 终极高清晰耗时埋点日志输出
        logger.info(
            "\n"
            "======================================================================\n"
            "📊 飞书 Webhook 异步流水线完整耗时报告 (FEISHU TELEMETRY REPORT)\n"
            "======================================================================\n"
            "1. 飞书消息初次 ACK 提示发送耗时 : %.3f s (%.0f ms)\n"
            "2. BQCA 智能体与 BigQuery 查数耗时: %.3f s (%.0f ms)\n"
            "3. 飞书卡片与图表渲染推送总耗时   : %.3f s (%.0f ms)\n"
            "----------------------------------------------------------------------\n"
            "🔥 飞书用户端到端总等待时间     : %.3f s (%.0f ms)\n"
            "======================================================================",
            t_ack - t_pipe_start, (t_ack - t_pipe_start) * 1000,
            t_bqca_end - t_bqca_start, (t_bqca_end - t_bqca_start) * 1000,
            t_pipe_end - t_card_start, (t_pipe_end - t_card_start) * 1000,
            t_pipe_end - t_pipe_start, (t_pipe_end - t_pipe_start) * 1000
        )

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await adapter.send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。", app_id=app_id)
        except Exception:
            pass
