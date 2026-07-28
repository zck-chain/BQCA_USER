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
# from app.renderer.html_generator import build_result_html
# from app.storage.gcs import upload_html, generate_query_id
from app.feishu.event import extract_question, get_message_id, get_chat_id, get_sender_id
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

    # Extract event payload (could be in top-level body or nested under body["event"])
    event_payload = body.get("event") if isinstance(body.get("event"), dict) else body
    if not event_payload:
        event_payload = body

    # Handle Feishu Card Action Event (One-click quick query)
    action_data = event_payload.get("action") or body.get("action")
    if action_data and isinstance(action_data, dict):
        action_val = action_data.get("value", {})
        if action_val.get("action") == "quick_query":
            next_query = action_val.get("query")
            # Extract chat_id from payload top-level or context
            chat_id = event_payload.get("open_chat_id") or event_payload.get("context", {}).get("open_chat_id")
            # Extract open_id from payload top-level, user, or operator
            open_id = (
                event_payload.get("open_id") or 
                event_payload.get("user", {}).get("open_id") or 
                event_payload.get("operator", {}).get("open_id")
            )
            
            if chat_id:
                target_id = chat_id
                
                # Check stored chat room type (P2P vs Group) to prevent cross-channel hijackings
                stored_chat_type = get_chat_type(chat_id)
                if stored_chat_type == "p2p":
                    if open_id:
                        target_id = open_id
                elif stored_chat_type == "group":
                    target_id = chat_id
                else:
                    # Fallback if SQLite record is absent (database cleared)
                    if open_id and _get_conversation(open_id) and not _get_conversation(chat_id):
                        target_id = open_id
                    
                logger.info("Feishu Card Action click: %r in chat %s, target_id %s", next_query, chat_id, target_id)
                asyncio.create_task(_process_query(next_query, target_id))
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
    chat_type = event.get("message", {}).get("chat_type", "")
    if chat_id and chat_type:
        save_chat_type(chat_id, chat_type)

    if chat_type == "p2p":
        target_id = get_sender_id(event) or chat_id
    else:
        target_id = chat_id

    asyncio.create_task(_process_query(question, target_id))

    return {"status": "ok"}


def extract_thoughts_and_summary(raw_text: str) -> tuple[list[str], str]:
    """Split the raw BQCA summary text into a list of English thought paragraphs
    and the final Chinese report based on explicit section headers (watershed slicing),
    falling back to post-split density scanning if headers are absent.
    
    Includes an intelligent check: if the extracted pre-split "thoughts" section actually
    contains human-written Chinese content (Chinese characters present with count >= 10),
    it is correctly recognized as the introductory report paragraph and merged back
    into the Chinese report rather than being grouped as thought/noise.
    """
    if not raw_text:
        return [], ""
        
    # 1. Try to find explicit BQCA report section headers
    markers = ["【逻辑解释】", "【业务洞察】", "### 【逻辑解释】", "### 【业务洞察】", "###【逻辑解释】", "###【业务洞察】"]
    first_header_idx = -1
    for marker in markers:
        idx = raw_text.find(marker)
        if idx != -1:
            if first_header_idx == -1 or idx < first_header_idx:
                first_header_idx = idx
                
    # If a header is found, split exactly before that header
    if first_header_idx != -1:
        split_idx = first_header_idx
    else:
        # 2. Fallback to post-split density scanner if no headers are present
        chinese_indices = [m.start() for m in re.finditer(r'[\u4e00-\u9fff]', raw_text)]
        if not chinese_indices:
            return [], raw_text
            
        split_idx = 0
        for idx in chinese_indices:
            sub = raw_text[idx:]
            if not sub:
                continue
            chinese_count = len(re.findall(r'[\u4e00-\u9fff]', sub))
            density = chinese_count / len(sub)
            if density >= 0.30:
                split_idx = idx
                break
        else:
            split_idx = chinese_indices[0]
        
    # Clean up split: backtrack past leading markdown, spaces, or headings
    while split_idx > 0 and raw_text[split_idx-1] in ['#', ' ', '\n', '\r', '*', '-', '【', '`']:
        split_idx -= 1
        
    thoughts_text = raw_text[:split_idx].strip()
    report_text = raw_text[split_idx:].strip()
    
    # 3. Double-filter verify the "thoughts" block
    # Check if the thoughts block is actually part of the Chinese intro report
    chinese_char_count = len(re.findall(r'[\u4e00-\u9fff]', thoughts_text))
    
    # If it has more than 10 Chinese characters, it is human introductory text, not SQL draft or English noise.
    # Exclude lines that are purely SQL formulas (we check if it contains common Chinese report starters)
    if chinese_char_count >= 10:
        # Prepend the human-written intro text back to the report text
        report_text = f"{thoughts_text}\n\n{report_text}".strip()
        thoughts_text = ""
        
    # Convert thoughts block into paragraphs list
    thoughts = [p.strip() for p in thoughts_text.split("\n") if p.strip()]
    return thoughts, report_text


def clean_technical_lines(text: str) -> str:
    """Filter out technical database formulas, raw relational planning outputs
    (like PROJECT, FILTER, JOIN, GROUP BY), and internal SQL syntax markers
    from the user-facing logical explanation, ensuring it remains 100% human-readable.
    """
    if not text:
        return ""
        
    lines = text.split("\n")
    cleaned_lines = []
    
    i = 0
    n = len(lines)
    
    def is_technical_line(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
            
        # 1. Relational planner and SQL execution keywords
        upper_words = ["PROJECT", "FILTER", "SELECT", "JOIN", "GROUP BY", "WITH", "EVALUATE", "HAVING", "LIMIT", "ORDER BY", "FROM"]
        for w in upper_words:
            if s.startswith(w) or s == w:
                return True
                
        # 2. SQL formula and database function blocks
        sql_funcs = ["COUNT(", "ROUND(", "SUM(", "AVG(", "COALESCE(", "CASE WHEN", "CASE ", "WHEN ", "THEN ", "ELSE ", "END "]
        for f in sql_funcs:
            if f in s:
                return True
                
        # 3. BigQuery project schema references
        if "webeye-internal-test" in s or "thelook" in s or "order_items" in s or "inventory_items" in s:
            return True
            
        # 4. Pure SQL artifacts
        if s.startswith("AS ") or s.endswith(" AS") or s == "AS" or " AS " in s:
            return True
            
        # 5. Non-Chinese database query code structures
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in s)
        if not has_chinese:
            code_symbols = ["_", ".", "(", ")", "=", "*", ">", "<", "`", "AS"]
            if any(sym in s for sym in code_symbols):
                if "_" in s or "." in s or "(" in s:
                    return True
                    
        # 6. Chinese SQL translation and step-by-step description markers
        tech_words = [
            "筛选出", "进行分组", "指标聚合", "时间区间", "字段", "数据表", "多表关联",
            "进行过滤", "表进行", "逻辑解释", "分析步骤", "计算步骤", "查询逻辑", "排序",
            "聚合计算", "筛选条件", "按年龄段", "时间筛选", "订单创建时间"
        ]
        for tw in tech_words:
            if tw in s:
                return True
                
        return False


    while i < n:
        line = lines[i]
        s = line.strip()
        
        # Detect technical headers like "查询逻辑：" or "计算公式：" that precede technical blocks
        is_tech_header = False
        tech_headers = ["查询逻辑：", "查询逻辑", "计算公式：", "计算公式", "计算步骤：", "分析步骤：", "分析步骤"]
        if s in tech_headers or any(s.startswith(th) for th in tech_headers):
            next_idx = i + 1
            while next_idx < n and not lines[next_idx].strip():
                next_idx += 1
            if next_idx < n and is_technical_line(lines[next_idx]):
                is_tech_header = True
                
        if is_tech_header:
            i += 1
            # Skip subsequent technical lines as well
            while i < n:
                if not lines[i].strip():
                    i += 1
                    continue
                if is_technical_line(lines[i]):
                    i += 1
                else:
                    break
            continue
            
        if is_technical_line(line):
            i += 1
            continue
            
        cleaned_lines.append(line)
        i += 1
        
    return "\n".join(cleaned_lines).strip()


def format_summary_sections(text: str) -> str:
    """Format the BQCA summary sections to look uniform and highly professional,
    unifying everything under '业务决策洞察' and removing purely technical '逻辑解释'.
    """
    if not text:
        return ""
        
    # Standardize all unbracketed headings to bracketed headings for consistent parsing
    text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?逻辑解释(?:】|\])?", "【逻辑解释】", text)
    text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?业务决策洞察(?:】|\])?", "【业务决策洞察】", text)
    text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?业务洞察(?:】|\])?", "【业务决策洞察】", text)
    
    # Initialize sections
    intro_part = ""
    logic_part = ""
    insight_part = ""
    
    # Locate section markers
    idx_logic = text.find("【逻辑解释】")
    idx_insight = text.find("【业务决策洞察】")
    
    if idx_logic != -1 and idx_insight != -1:
        if idx_logic < idx_insight:
            # Logic comes before Insight (Standard order)
            intro_part = text[:idx_logic].strip()
            logic_part = text[idx_logic + len("【逻辑解释】"):idx_insight].strip()
            insight_part = text[idx_insight + len("【业务决策洞察】"):].strip()
        else:
            # Insight comes before Logic (Inverted order)
            intro_part = text[:idx_insight].strip()
            insight_part = text[idx_insight + len("【业务决策洞察】"):idx_logic].strip()
            logic_part = text[idx_logic + len("【逻辑解释】"):].strip()
    elif idx_logic != -1:
        # Only Logic is present
        intro_part = text[:idx_logic].strip()
        logic_part = text[idx_logic + len("【逻辑解释】"):].strip()
    elif idx_insight != -1:
        # Only Insight is present
        intro_part = text[:idx_insight].strip()
        insight_part = text[idx_insight + len("【业务决策洞察】"):].strip()
    else:
        # Neither is present, treat the entire block as raw summary text
        insight_part = text

    # Clean sections
    cleaned_insight = clean_technical_lines(insight_part).strip()
    cleaned_logic = clean_technical_lines(logic_part).strip()
    
    # Standardize placeholder filtering (e.g. if LLM returned empty bullets, dots or stars)
    for placeholder in ["•", "*", "-", ".", ""]:
        if cleaned_insight == placeholder:
            cleaned_insight = ""
        if cleaned_logic == placeholder:
            cleaned_logic = ""

    # Clean up LaTeX artifacts in all sections (Feishu cards do not render LaTeX!)
    def clean_latex(s: str) -> str:
        if not s:
            return ""
        # Replace mathematical LaTeX symbols with elegant unicode equivalents
        s = s.replace(r"\ge", " ≥ ").replace(r"\\ge", " ≥ ")
        s = s.replace(r"\le", " ≤ ").replace(r"\\le", " ≤ ")
        s = s.replace(r"\times", " × ").replace(r"\\times", " × ")
        s = s.replace(r"\approx", " ≈ ").replace(r"\\approx", " ≈ ")
        s = s.replace(r"\%", "%").replace(r"\\%", "%")
        s = s.replace(r"\$", "$").replace(r"\\$", "$")
        # Strip remaining dollar signs used for mathematical rendering (e.g. $ \ge 20 $ or $\ge 20$)
        s = re.sub(r"\$\s*([≥≤≈a-zA-Z0-9%\s]+)\s*\$", r"\1", s)
        # Strip stray dollar signs or backslashes
        s = s.replace("$", "").replace("\\", "")
        # Standardize multiple horizontal spaces
        s = re.sub(r"[ \t]+", " ", s)
        # Collapse multiple empty lines
        s = re.sub(r"\n\s*\n+", "\n\n", s)
        # Fix layout of bullets that were mathematically unescaped
        s = s.replace(" 件 。", "件。").replace(" % ) 。", "%)。").replace(" % ", "%")
        return s.strip()

    cleaned_insight = clean_latex(cleaned_insight)
    cleaned_logic = clean_latex(cleaned_logic)
    intro_part = clean_latex(intro_part)

    # Assemble final output with unified heading AT THE VERY TOP
    formatted = "**🎯 业务决策洞察：**\n"
    
    # Process friendly opener intro
    if intro_part:
        cleaned_intro = clean_technical_lines(intro_part).strip()
        # Filter out trailing punctuation or transitions if they end abruptly
        if cleaned_intro and (cleaned_intro.endswith("如下：") or cleaned_intro.endswith("如下")):
            cleaned_intro = cleaned_intro.rstrip("：").rstrip(":")[:-2] + "已生成并为您展示在下方列表。"
        elif cleaned_intro and cleaned_intro.endswith("结果与"):
            cleaned_intro = cleaned_intro[:-3] + "已生成并为您展示在下方列表。"
        if cleaned_intro:
            formatted += f"{cleaned_intro}\n\n"
            
    # Process business decision insights section
    if cleaned_insight:
        formatted += f"{cleaned_insight}"
    else:
        # If no actual insight was generated (or was skipped by BQCA due to heavy row lists),
        # output a premium, high-fidelity business summary instead of leaking technical SQL steps!
        formatted += (
            "📊 数据已成功为您提取并生成上方报表。本次查询包含的数据样本较多、交叉维度较广，"
            "为了协助您提炼更精准的运营决策动作，建议点击下方【快捷追问深度分析】按钮，进行更精细、更针对性的商业维度下钻分析。"
        )
        
    return formatted.strip()



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

        # Ensure result.recommended_questions has fallback smart e-commerce questions if BQCA returned empty
        if not result.recommended_questions:
            result.recommended_questions = [
                "各订单状态的占比情况",
                "分析近一年销售额 Top 5 的国家",
                "查询退货数最高的前 5 类商品"
            ]

        # Format Chinese summary headings nicely
        formatted_summary = format_summary_sections(summary)

        # Generate and upload HTML interactive report is disabled per user request
        result_url = None

        if not result.rows and not result.vega_config:
            await send_premium_result_card(chat_id, question, result, formatted_summary or "未查询到相关数据，请换个说法试试。", result_url=result_url)
            return

        await send_premium_result_card(chat_id, question, result, formatted_summary or "查询完成。", result_url=result_url)

    except Exception as e:
        logger.error("Query processing failed: %s", e, exc_info=True)
        try:
            await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
        except Exception:
            pass
