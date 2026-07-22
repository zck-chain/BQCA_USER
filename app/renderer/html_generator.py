import json
import vertexai
from vertexai.generative_models import GenerativeModel

from app.config import settings


HTML_SYSTEM_PROMPT = """你是一个数据可视化专家。根据用户问题和查询结果，生成：
1. 一个完整的 HTML 页面（包含 ECharts 图表），用于可视化展示
2. 一段中文摘要（2-3 句话），概括数据要点

输出格式（严格遵守）：
---HTML---
（完整的 HTML 代码，包含 ECharts CDN 引用，可独立运行）
---SUMMARY---
（中文摘要）

要求：
- HTML 使用 ECharts (https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js)
- 页面响应式，移动端可用
- 图表类型根据问题自动选择最合适的
- 数据表格也要包含在 HTML 中
"""


def _call_gemini(prompt: str) -> object:
  vertexai.init(project=settings.BQ_PROJECT)
  model = GenerativeModel(settings.GEMINI_MODEL)
  response = model.generate_content(prompt)
  return response


def _fallback_html(rows: list[dict], columns: list[str]) -> str:
  """Fallback: plain data table HTML, ensures link always works."""
  header = "".join(f"<th>{c}</th>" for c in columns)
  body_rows = ""
  for row in rows[:100]:
      cells = "".join(f"<td>{row.get(c, '')}</td>" for c in columns)
      body_rows += f"<tr>{cells}</tr>"
  return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:sans-serif;padding:16px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f5f5f5}}</style>
</head><body><table><thead><tr>{header}</tr></thead><tbody>{body_rows}</tbody></table></body></html>"""


async def generate_html_and_summary(
  question: str, rows: list[dict], columns: list[str]
) -> tuple[str, str]:
  """Call Gemini to generate HTML visualization and Chinese summary. Falls back to plain table on failure."""
  data_json = json.dumps(rows[:200], ensure_ascii=False, default=str)

  prompt = f"""{HTML_SYSTEM_PROMPT}

用户问题：{question}

列名：{', '.join(columns)}

查询结果（JSON）：
{data_json}"""

  try:
      response = _call_gemini(prompt)
      text = response.text.strip()

      html = ""
      summary = "查询完成，请查看详情。"

      if "---HTML---" in text and "---SUMMARY---" in text:
          parts = text.split("---SUMMARY---")
          html_part = parts[0].replace("---HTML---", "").strip()
          summary = parts[1].strip()
          if html_part.startswith("```html"):
              html_part = html_part[7:]
          if html_part.startswith("```"):
              html_part = html_part[3:]
          if html_part.endswith("```"):
              html_part = html_part[:-3]
          html = html_part.strip()

      if not html:
          html = _fallback_html(rows, columns)

      return html, summary
  except Exception:
      return _fallback_html(rows, columns), "查询完成，请查看详情。"
