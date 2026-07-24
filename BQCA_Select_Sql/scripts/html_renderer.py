#!/usr/bin/env python3
"""
BQCA Select SQL - HTML 结果页生成器

将 ChatResult 渲染为独立 HTML 页面，包含表格、图表和 SQL。

用法:
    from html_renderer import build_result_html
    html = build_result_html("你的问题", result)
"""

import json
import sys
import os

# 允许独立运行时导入同目录模块
sys.path.insert(0, os.path.dirname(__file__))

from bqca_query import ChatResult

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
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f'{header}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _build_chart_html(vega_config: dict | None) -> str:
    if not vega_config:
        return ""
    spec_json = json.dumps(vega_config, ensure_ascii=False, default=str)
    return (
        '<div id="chart" style="width:100%;min-height:300px;"></div>\n'
        f'<script>\nvegaEmbed(\'#chart\', {spec_json}, '
        '{renderer: \'svg\'}).catch(console.error);\n</script>'
    )


def build_result_html(question: str, result: ChatResult) -> str:
    """将 ChatResult 渲染为完整 HTML 页面。"""
    chart_section = _build_chart_html(result.vega_config)
    table_section = _build_table_html(result.fields, result.rows)

    summary_html = ""
    if result.summary:
        summary_html = f'<div class="summary-card">{result.summary}</div>'

    sql_html = ""
    if result.sql:
        escaped_sql = result.sql.replace("<", "&lt;").replace(">", "&gt;")
        sql_html = (
            '<details class="sql-details"><summary>查看 SQL</summary>\n'
            f'<pre><code>{escaped_sql}</code></pre></details>'
        )

    sections = []
    if summary_html:
        sections.append(summary_html)
    if chart_section:
        sections.append(f'<div class="chart-section">{chart_section}</div>')
    if table_section:
        sections.append(table_section)
    if sql_html:
        sections.append(sql_html)
    if not sections:
        sections.append('<div class="summary-card">未查询到相关数据，请换个说法试试。</div>')

    content = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{question}</title>
<script src="{VEGA_CDN}"></script>
<script src="{VEGA_LITE_JS}"></script>
<script src="{VEGA_EMBED_JS}"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;padding:20px;max-width:960px;margin:0 auto;background:#f8f9fa;color:#1a1a1a;line-height:1.6}}
h1{{font-size:20px;font-weight:600;margin-bottom:16px;color:#1a1a1a;padding-bottom:12px;border-bottom:1px solid #e8e8e8}}
.summary-card{{background:#fff;padding:16px 20px;border-radius:10px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);line-height:1.8;font-size:15px}}
.chart-section{{background:#fff;padding:20px;border-radius:10px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.table-wrap{{background:#fff;border-radius:10px;padding:16px;overflow-x:auto;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th{{background:#f0f2f5;font-weight:600;text-align:left;padding:10px 14px;border-bottom:2px solid #e0e0e0;white-space:nowrap}}
td{{padding:10px 14px;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
tr:hover td{{background:#fafbfc}}
.sql-details{{margin:8px 0;font-size:13px;color:#666}}
.sql-details summary{{cursor:pointer;padding:8px 0;color:#888;font-size:13px}}
.sql-details pre{{background:#fff;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px;margin-top:8px;border:1px solid #eee}}
</style>
</head><body>
<h1>{question}</h1>
{content}
</body></html>"""


if __name__ == "__main__":
    # 简单测试：从 JSON 文件构建结果
    import argparse
    parser = argparse.ArgumentParser(description="BQCA HTML 结果渲染器")
    parser.add_argument("--input", "-i", help="ChatResult JSON 文件路径")
    parser.add_argument("--question", "-q", default="查询结果")
    parser.add_argument("--output", "-o", help="输出 HTML 文件路径（默认 stdout）")
    args = parser.parse_args()

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
        result = ChatResult(
            summary=data.get("summary", ""),
            sql=data.get("sql", ""),
            fields=data.get("fields", []),
            rows=data.get("rows", []),
            vega_config=data.get("vega_config"),
        )
    else:
        # 示例数据
        result = ChatResult(
            summary="测试结果",
            sql="SELECT 1",
            fields=["col_a", "col_b"],
            rows=[{"col_a": "1", "col_b": "hello"}, {"col_a": "2", "col_b": "world"}],
        )

    html = build_result_html(args.question, result)
    if args.output:
        with open(args.output, "w") as f:
            f.write(html)
        print(f"HTML written to {args.output}")
    else:
        print(html)
