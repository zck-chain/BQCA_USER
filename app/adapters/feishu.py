import json
import logging
import re
import httpx

from app.adapters.base import BaseCardAdapter
from app.feishu.message import (
    send_text_message as feishu_send_text_message,
    send_premium_result_card as feishu_send_premium_result_card,
)

logger = logging.getLogger(__name__)


def extract_thoughts_and_summary(raw_text: str) -> tuple[list[str], str]:
    """Split raw BQCA summary into English thoughts and Chinese report."""
    if not raw_text:
        return [], ""
    markers = ["【逻辑解释】", "【业务洞察】", "### 【逻辑解释】", "### 【业务洞察】", "###【逻辑解释】", "###【业务洞察】"]
    first_header_idx = -1
    for marker in markers:
        idx = raw_text.find(marker)
        if idx != -1:
            if first_header_idx == -1 or idx < first_header_idx:
                first_header_idx = idx

    if first_header_idx != -1:
        split_idx = first_header_idx
    else:
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

    while split_idx > 0 and raw_text[split_idx-1] in ['#', ' ', '\n', '\r', '*', '-', '【', '`']:
        split_idx -= 1

    thoughts_text = raw_text[:split_idx].strip()
    report_text = raw_text[split_idx:].strip()

    chinese_char_count = len(re.findall(r'[\u4e00-\u9fff]', thoughts_text))
    if chinese_char_count >= 10:
        report_text = f"{thoughts_text}\n\n{report_text}".strip()
        thoughts_text = ""

    thoughts = [p.strip() for p in thoughts_text.split("\n") if p.strip()]
    return thoughts, report_text


def clean_technical_lines(text: str) -> str:
    """Filter out purely technical SQL generation noise lines from summary."""
    if not text:
        return ""
        
    lines = text.split("\n")
    cleaned_lines = []
    
    def is_technical_line(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        tech_indicators = [
            "SQL", "GROUP BY", "SELECT", "INNER JOIN", "LEFT JOIN", 
            "WHERE", "ORDER BY", "LIMIT", "去重订单总数", "分组",
            "字段", "的记录", "表中的", "为依据", "的统计", "的检索"
        ]
        return any(ind in s for ind in tech_indicators)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        
        if re.match(r"^\d+\.\s*\*\*(?:模式|技术|逻辑|筛选|检索|计算|关联|表)\w*\*\*", stripped):
            i += 1
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


def clean_latex(s: str) -> str:
    """Clean LaTeX symbols in summary for Feishu Lark card rendering."""
    if not s:
        return ""
    s = s.replace(r"\ge", " ≥ ").replace(r"\\ge", " ≥ ")
    s = s.replace(r"\le", " ≤ ").replace(r"\\le", " ≤ ")
    s = s.replace(r"\times", " × ").replace(r"\\times", " × ")
    s = s.replace(r"\approx", " ≈ ").replace(r"\\approx", " ≈ ")
    s = s.replace(r"\%", "%").replace(r"\\%", "%")
    s = s.replace(r"\$", "$").replace(r"\\$", "$")
    s = re.sub(r"\$\s*([≥≤≈a-zA-Z0-9%\s]+)\s*\$", r"\1", s)
    s = s.replace("$", "").replace("\\", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    s = s.replace(" 件 。", "件。").replace(" % ) 。", "%)。").replace(" % ", "%")
    return s.strip()


class FeishuAdapter(BaseCardAdapter):
    """Feishu / Lark Office Card Adapter implementation."""

    def format_summary(self, text: str) -> str:
        """Format BQCA summary sections specifically for Feishu Markdown card output."""
        if not text:
            return ""

        text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?逻辑解释(?:】|\])?", "【逻辑解释】", text)
        text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?业务决策洞察(?:】|\])?", "【业务决策洞察】", text)
        text = re.sub(r"(?:###|##|#)?\s*(?:【|\[)?业务洞察(?:】|\])?", "【业务决策洞察】", text)

        intro_part = ""
        logic_part = ""
        insight_part = ""

        idx_logic = text.find("【逻辑解释】")
        idx_insight = text.find("【业务决策洞察】")

        if idx_logic != -1 and idx_insight != -1:
            if idx_logic < idx_insight:
                intro_part = text[:idx_logic].strip()
                logic_part = text[idx_logic + len("【逻辑解释】"):idx_insight].strip()
                insight_part = text[idx_insight + len("【业务决策洞察】"):].strip()
            else:
                intro_part = text[:idx_insight].strip()
                insight_part = text[idx_insight + len("【业务决策洞察】"):idx_logic].strip()
                logic_part = text[idx_logic + len("【逻辑解释】"):].strip()
        elif idx_logic != -1:
            intro_part = text[:idx_logic].strip()
            logic_part = text[idx_logic + len("【逻辑解释】"):].strip()
        elif idx_insight != -1:
            intro_part = text[:idx_insight].strip()
            insight_part = text[idx_insight + len("【业务决策洞察】"):].strip()
        else:
            insight_part = text

        cleaned_insight = clean_technical_lines(insight_part).strip()
        cleaned_logic = clean_technical_lines(logic_part).strip()

        for placeholder in ["•", "*", "-", ".", ""]:
            if cleaned_insight == placeholder:
                cleaned_insight = ""
            if cleaned_logic == placeholder:
                cleaned_logic = ""

        cleaned_insight = clean_latex(cleaned_insight)
        cleaned_logic = clean_latex(cleaned_logic)
        intro_part = clean_latex(intro_part)

        formatted = ""
        if intro_part and "⚠️" in intro_part:
            formatted += f"{intro_part}\n\n"

        if cleaned_insight:
            formatted += f"**🎯 业务决策洞察：**\n{cleaned_insight}"
        elif cleaned_logic:
            formatted += f"**🎯 业务决策洞察：**\n{cleaned_logic}"
        elif intro_part:
            formatted += f"**🎯 业务决策洞察：**\n{intro_part}"

        return formatted.strip()

    async def send_text_message(self, target_id: str, text: str, app_id: str | None = None) -> dict:
        """Send text message to Feishu chat."""
        return await feishu_send_text_message(target_id, text, app_id=app_id)

    async def send_result_card(
        self,
        target_id: str,
        question: str,
        summary: str,
        sql: str | None = None,
        fields: list[str] | None = None,
        rows: list[dict] | None = None,
        recommended_questions: list[str] | None = None,
        result_url: str | None = None,
        app_id: str | None = None,
    ) -> dict:
        """Send rich Feishu Lark Card v2."""
        class MockResult:
            def __init__(self):
                self.sql = sql or ""
                self.fields = fields or []
                self.rows = rows or []
                self.recommended_questions = recommended_questions or []

        res = MockResult()
        return await feishu_send_premium_result_card(
            chat_id=target_id,
            question=question,
            result=res,
            cleaned_summary=summary,
            result_url=result_url,
            app_id=app_id,
        )
