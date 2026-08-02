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


def get_emoji_for_key(key: str) -> str:
    key_lower = key.lower()
    if any(k in key_lower for k in ["现象", "发现", "问题", "异常", "原因", "故障", "bug", "error"]):
        return "🔍"
    if any(k in key_lower for k in ["建议", "策略", "方案", "改进", "措施", "落地"]):
        return "💡"
    if any(k in key_lower for k in ["数据", "指标", "统计", "结果", "数量", "金额", "数值", "rows"]):
        return "📊"
    if any(k in key_lower for k in ["结论", "总结", "观点", "洞察", "核心", "summary"]):
        return "📌"
    if any(k in key_lower for k in ["折扣", "特卖", "促销", "活动", "优惠", "promo", "discount"]):
        return "🏷️"
    if any(k in key_lower for k in ["渠道", "流量", "来源", "channel"]):
        return "🌐"
    return "📍"


def extract_business_insights(text: str) -> str:
    """Extract only the BUSINESS_INSIGHTS segment, dropping any LOGIC_EXPLANATION / rule headers."""
    if not text:
        return ""

    # 1. Compile regexes for various Business Insights section starts
    insights_patterns = [
        r"BUSINESS_INSIGHTS\b",
        r"BUSINESS_INSIGHT\b",
        r"【\s*业务洞察\s*】",
        r"【\s*核心业务洞察与落地建议\s*】",
        r"###\s*核心业务洞察与落地建议",
        r"###\s*业务决策洞察",
        r"核心业务洞察与落地建议",
        r"业务决策洞察",
        r"业务洞察",
        r"落地建议",
    ]
    
    best_insight_idx = -1
    matched_pattern_len = 0
    
    for pattern in insights_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            idx = match.start()
            if best_insight_idx == -1 or idx < best_insight_idx:
                best_insight_idx = idx
                matched_pattern_len = match.end() - match.start()
                
    # Same for Logic Explanation to find if it comes after Insights
    logic_patterns = [
        r"LOGIC_EXPLANATION\b",
        r"【\s*逻辑解释\s*】",
        r"###\s*【\s*逻辑解释\s*】",
        r"###\s*设计分析概要",
        r"设计分析概要",
        r"逻辑解释",
        r"数据提取逻辑",
    ]
    
    best_logic_idx = -1
    for pattern in logic_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            idx = match.start()
            if best_logic_idx == -1 or idx < best_logic_idx:
                best_logic_idx = idx

    # If Business Insights marker was found
    if best_insight_idx != -1:
        # If LOGIC_EXPLANATION is after insights, slice between them
        if best_logic_idx != -1 and best_logic_idx > best_insight_idx:
            segment = text[best_insight_idx + matched_pattern_len:best_logic_idx].strip()
        else:
            segment = text[best_insight_idx + matched_pattern_len:].strip()
            
        segment = re.sub(r"^[\s\:\：\-\*\#\【\】]+", "", segment).strip()
        return segment

    # 2. Fallback if no explicit Business Insights marker was found:
    best_logic_idx = -1
    matched_logic_len = 0
    for pattern in logic_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            idx = match.start()
            if best_logic_idx == -1 or idx < best_logic_idx:
                best_logic_idx = idx
                matched_logic_len = match.end() - match.start()
                
    if best_logic_idx != -1:
        remaining_text = text[best_logic_idx + matched_logic_len:].strip()
        numbered_match = re.search(r"^\s*1\.\s+", remaining_text, re.MULTILINE)
        if numbered_match:
            analysis_match = re.search(r"(?:现象|建议)", remaining_text)
            if analysis_match:
                sub_str = remaining_text[:analysis_match.start()]
                numbered_items = list(re.finditer(r"(?:^|\n)\s*(\d+\.)\s+", sub_str))
                if numbered_items:
                    start_pos = numbered_items[-1].start()
                    return remaining_text[start_pos:].strip()
            return remaining_text
            
        return remaining_text

    return text.strip()


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
    return text


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
    s = re.sub(r"(?<=[\S])[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    s = s.replace(" 件 。", "件。").replace(" % ) 。", "%)。").replace(" % ", "%")
    return s.strip()


class FeishuAdapter(BaseCardAdapter):
    """Feishu / Lark Office Card Adapter implementation."""

    def format_summary(self, text: str) -> str:
        """Format BQCA summary sections specifically for Feishu Markdown card output."""
        if not text:
            return ""

        text = re.sub(r"(?:###|##|#)?\s*\d*\.?\s*(?:【|\[)?(?:BUSINESS_INSIGHTS|BUSINESS_INSIGHT|业务决策洞察|业务洞察|核心业务洞察与落地建议)(?:】|\])?\s*(：|:)?", "【业务决策洞察】", text, flags=re.IGNORECASE)
        text = re.sub(r"(?:###|##|#)?\s*\d*\.?\s*(?:【|\[)?(?:LOGIC_EXPLANATION|逻辑解释|设计分析概要|数据提取逻辑)(?:】|\])?\s*(：|:)?", "【逻辑解释】", text, flags=re.IGNORECASE)

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
        vega_config: dict | None = None,
        app_id: str | None = None,
    ) -> dict:
        """Send rich Feishu Lark Card v2."""
        class MockResult:
            def __init__(self):
                self.sql = sql or ""
                self.fields = fields or []
                self.rows = rows or []
                self.recommended_questions = recommended_questions or []
                self.vega_config = vega_config

        res = MockResult()
        return await feishu_send_premium_result_card(
            chat_id=target_id,
            question=question,
            result=res,
            cleaned_summary=summary,
            result_url=result_url,
            app_id=app_id,
        )
