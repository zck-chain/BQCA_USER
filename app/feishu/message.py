import json
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_tenant_token: str | None = None


async def _get_tenant_token() -> str:
  """Get Feishu tenant_access_token."""
  global _tenant_token
  async with httpx.AsyncClient() as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
          json={
              "app_id": settings.FEISHU_APP_ID,
              "app_secret": settings.FEISHU_APP_SECRET,
          },
      )
      data = resp.json()
      _tenant_token = data["tenant_access_token"]
      return _tenant_token


async def send_text_message(chat_id: str, text: str) -> dict:
  """Send plain text message."""
  token = await _get_tenant_token()
  payload = {
      "receive_id": chat_id,
      "msg_type": "text",
      "content": json.dumps({"text": text}),
  }
  logger.info("Sending Feishu message to %s, text length=%d", chat_id, len(text))
  async with httpx.AsyncClient() as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": "chat_id"},
          headers={"Authorization": f"Bearer {token}"},
          json=payload,
      )
      resp_json = resp.json()
      if resp.status_code != 200 or resp_json.get("code") != 0:
          logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
      else:
          logger.info("Feishu message sent successfully.")
      return resp_json


async def send_result_card(chat_id: str, summary: str, result_url: str) -> dict:
  """Send result card with summary and detail link."""
  token = await _get_tenant_token()
  card_content = {
      "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
          {"tag": "action", "actions": [
              {"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"},
               "url": result_url, "type": "primary"}
          ]},
      ]
  }
  async with httpx.AsyncClient() as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": "chat_id"},
          headers={"Authorization": f"Bearer {token}"},
          json={
              "receive_id": chat_id,
              "msg_type": "interactive",
              "content": json.dumps(card_content),
          },
      )
      return resp.json()


async def send_premium_result_card(chat_id: str, question: str, result, cleaned_summary: str) -> dict:
    """Send high-quality interactive message card (lark v2 card) with:
    - Custom indigo AI theme header
    - User's natural language question
    - Collapsible panel for thinking processes (if present)
    - Cleaned Chinese business insights (summary)
    - Collapsible panel for generated SQL code blocks
    - Dynamic Markdown table for row results
    - Interactive action buttons for recommended follow-up questions
    """
    token = await _get_tenant_token()

    elements = []

    # 1. Natural Language Question Block
    elements.append({
        "tag": "markdown",
        "content": f"**🔍 提问问题：**\n{question}"
    })

    # 2. Collapsible Thinking Process Block
    if result.thinking_process:
        thoughts_text = "\n".join([f"> {t}" for t in result.thinking_process if t.strip()])
        if thoughts_text:
            elements.append({
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                  "title": {"tag": "plain_text", "content": "⚙️ 显示思考过程 (Thinking Process)"}
                },
                "elements": [
                  {
                    "tag": "markdown",
                    "content": thoughts_text
                  }
                ]
            })

    # 3. Cleaned Summary (Business Insight / Analysis)
    elements.append({
        "tag": "markdown",
        "content": f"**📝 逻辑分析与业务洞察：**\n{cleaned_summary or '查询成功，请见下方明细。'}"
    })

    # 4. Collapsible Generated SQL Block
    if result.sql:
        elements.append({
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {
              "title": {"tag": "plain_text", "content": "💻 查看自动生成的 SQL 语句"}
            },
            "elements": [
              {
                "tag": "markdown",
                "content": f"```sql\n{result.sql}\n```"
              }
            ]
        })

    # 5. Dynamic Data Table Block
    if result.rows and result.fields:
        table_md = "| " + " | ".join(result.fields) + " |\n"
        table_md += "| " + " | ".join(["---"] * len(result.fields)) + " |\n"
        for row in result.rows[:10]:
            table_md += "| " + " | ".join(str(row.get(f, "-")) for f in result.fields) + " |\n"
        
        row_count = len(result.rows)
        if row_count > 10:
            table_md += f"\n*⚠️ 共 {row_count} 行数据，卡片内仅展示前 10 行。*"

        elements.append({
            "tag": "markdown",
            "content": f"**📊 数据查询结果展示：**\n\n{table_md}"
        })

    # 6. Interactive Actions / Recommended Questions Block
    if result.recommended_questions:
        buttons = []
        for q in result.recommended_questions[:3]:
            display_title = q[:18] + "..." if len(q) > 18 else q
            buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": display_title},
                "type": "default",
                "value": {
                    "action": "quick_query",
                    "query": q
                }
            })
        
        if buttons:
            elements.append({
                "tag": "markdown",
                "content": "**🚀 快捷追问深度分析：**"
            })
            elements.append({
                "tag": "action",
                "actions": buttons
            })

    # Create Lark Card Schema 2.0 Content
    card_content = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True
        },
        "header": {
            "template": "indigo",
            "title": {
                "tag": "plain_text",
                "content": "📊 BQCA 智能数据分析"
            }
        },
        "body": {
            "direction": "vertical",
            "elements": elements
        }
    }

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }

    logger.info("Sending premium interactive card to %s, elements_count=%d", chat_id, len(elements))
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        resp_json = resp.json()
        if resp.status_code != 200 or resp_json.get("code") != 0:
            logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
        else:
            logger.info("Feishu premium interactive card sent successfully.")
        return resp_json
