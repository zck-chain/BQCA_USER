import json
from app.bqca.client import ChatResult

VEGA_CDN = "https://cdn.jsdelivr.net/npm/vega@5"
VEGA_LITE_JS = "https://cdn.jsdelivr.net/npm/vega-lite@5"
VEGA_EMBED_JS = "https://cdn.jsdelivr.net/npm/vega-embed@6"


def _build_table_html(fields: list[str], rows: list[dict]) -> str:
    if not fields or not rows:
        return ""
    header = "".join(f"<th>{f}</th>" for f in fields)
    body = ""
    for row in rows[:200]:
        cells = "".join(f"<td>{row.get(f, '')}</td>" for f in fields)
        body += f"<tr>{cells}</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _build_chart_html(vega_config: dict | None) -> str:
    if not vega_config:
        return ""
    spec_json = json.dumps(vega_config, ensure_ascii=False, default=str)
    return f"""<div id="chart" style="width:100%;"></div>
<script>
vegaEmbed('#chart', {spec_json}, {{renderer: 'svg'}}).catch(console.error);
</script>"""


def build_result_html(question: str, result: ChatResult) -> str:
    """Build a complete HTML page from BQCA ChatResult."""
    chart_section = _build_chart_html(result.vega_config)
    table_section = _build_table_html(result.fields, result.rows)
    summary_html = f'<p class="summary">{result.summary}</p>' if result.summary else ""
    sql_html = f"<details><summary>SQL</summary><pre><code>{result.sql}</code></pre></details>" if result.sql else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BQCA - {question}</title>
<script src="{VEGA_CDN}"></script>
<script src="{VEGA_LITE_JS}"></script>
<script src="{VEGA_EMBED_JS}"></script>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:20px;max-width:960px;margin:0 auto;color:#333}}
h1{{font-size:18px;margin-bottom:8px}}
.summary{{background:#f0f4ff;padding:12px 16px;border-radius:8px;margin:12px 0;line-height:1.6}}
.table-wrap{{overflow-x:auto;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #e0e0e0;padding:8px 12px;text-align:left;white-space:nowrap}}
th{{background:#f5f5f5;font-weight:600}}
details{{margin:8px 0;font-size:13px;color:#666}}
details pre{{background:#f8f8f8;padding:12px;border-radius:6px;overflow-x:auto}}
</style>
</head><body>
<h1>{question}</h1>
{summary_html}
{chart_section}
{table_section}
{sql_html}
</body></html>"""
