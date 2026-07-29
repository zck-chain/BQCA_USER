import json
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

def _resolve_bot_credentials(app_id: str | None = None) -> tuple[str, str]:
    if app_id and settings.GAME_FEISHU_APP_ID and app_id == settings.GAME_FEISHU_APP_ID:
        return settings.GAME_FEISHU_APP_ID, settings.GAME_FEISHU_APP_SECRET
    return settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET


async def _get_tenant_token(app_id: str | None = None, app_secret: str | None = None) -> str:
    """Get Feishu tenant_access_token for specific bot credentials."""
    target_id, target_secret = app_id, app_secret
    if not target_id or not target_secret:
        target_id, target_secret = _resolve_bot_credentials(app_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": target_id,
                "app_secret": target_secret,
            },
        )
        data = resp.json()
        return data.get("tenant_access_token", "")


async def send_text_message(chat_id: str, text: str, app_id: str | None = None) -> dict:
  """Send plain text message."""
  token = await _get_tenant_token(app_id=app_id)
  receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
  payload = {
      "receive_id": chat_id,
      "msg_type": "text",
      "content": json.dumps({"text": text}),
  }
  logger.info("Sending Feishu message to %s (type=%s), text length=%d", chat_id, receive_id_type, len(text))
  async with httpx.AsyncClient(timeout=20.0) as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": receive_id_type},
          headers={"Authorization": f"Bearer {token}"},
          json=payload,
      )
      resp_json = resp.json()
      if resp.status_code != 200 or resp_json.get("code") != 0:
          logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
      else:
          logger.info("Feishu message sent successfully.")
      return resp_json


async def send_result_card(chat_id: str, summary: str, result_url: str, app_id: str | None = None) -> dict:
  """Send result card with summary and detail link."""
  token = await _get_tenant_token(app_id=app_id)
  receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
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
          params={"receive_id_type": receive_id_type},
          headers={"Authorization": f"Bearer {token}"},
          json={
              "receive_id": chat_id,
              "msg_type": "interactive",
              "content": json.dumps(card_content),
          },
      )
      return resp.json()


async def send_premium_result_card(chat_id: str, question: str, result, cleaned_summary: str, result_url: str | None = None, app_id: str | None = None) -> dict:
    """Send high-quality interactive message card (lark v2 card)."""
    token = await _get_tenant_token(app_id=app_id)

    elements = []

    # 1. Natural Language Question Block
    elements.append({
        "tag": "markdown",
        "content": f"**🔍 提问问题：**\n{question}"
    })

    # 2. Collapsible Thinking Process Block (Temporarily commented out per user request for a cleaner, premium UI)
    # if result.thinking_process:
    #     thoughts_text = "\n".join([f"> {t}" for t in result.thinking_process if t.strip()])
    #     if thoughts_text:
    #         elements.append({
    #             "tag": "collapsible_panel",
    #             "expanded": False,
    #             "header": {
    #               "title": {"tag": "plain_text", "content": "⚙️ 显示思考过程 (Thinking Process)"}
    #             },
    #             "elements": [
    #               {
    #                 "tag": "markdown",
    #                 "content": thoughts_text
    #               }
    #             ]
    #         })

    # 3. Collapsible Generated SQL Block
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

    # 4. Dynamic Data Table Block
    if result.rows and result.fields:
        main_rows = result.rows[:10]
        remaining_rows = result.rows[10:]

        table_md = "| " + " | ".join(result.fields) + " |\n"
        table_md += "| " + " | ".join(["---"] * len(result.fields)) + " |\n"
        for row in main_rows:
            table_md += "| " + " | ".join(str(row.get(f, "-")) for f in result.fields) + " |\n"

        elements.append({
            "tag": "markdown",
            "content": f"**📊 数据查询结果展示：**\n\n{table_md}"
        })

        if remaining_rows:
            rem_table_md = "| " + " | ".join(result.fields) + " |\n"
            rem_table_md += "| " + " | ".join(["---"] * len(result.fields)) + " |\n"
            for row in remaining_rows[:30]:
                rem_table_md += "| " + " | ".join(str(row.get(f, "-")) for f in result.fields) + " |\n"
            
            rem_count = len(remaining_rows)
            if rem_count > 30:
                rem_table_md += f"\n*⚠️ 仅展示前 30 行余量数据。*"

            elements.append({
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                  "title": {"tag": "plain_text", "content": f"🔽 展开查看其余 {rem_count} 行数据"}
                },
                "elements": [
                  {
                    "tag": "markdown",
                    "content": rem_table_md
                  }
                ]
            })

    # 5. Cleaned Summary (Business Insight / Analysis) - Moved to the end of informative content
    elements.append({
        "tag": "markdown",
        "content": cleaned_summary or "查询成功，请见下方明细。"
    })

    # 5.5. Chart and Interactive Page Action Buttons (Temporarily commented out per user's strict 5-section layout requirement)
    # if result_url:
    #     if result.vega_config:
    #         elements.append({
    #             "tag": "markdown",
    #             "content": "**📈 可视化图表已生成：**\n系统已为您绘制好专业的交互式数据分析图表（支持缩放/悬浮）。"
    #         })
    #         elements.append({
    #             "tag": "button",
    #             "text": {"tag": "plain_text", "content": "📊 点击查看交互式分析图表"},
    #             "url": result_url,
    #             "type": "primary"
    #         })
    #     else:
    #         elements.append({
    #             "tag": "button",
    #             "text": {"tag": "plain_text", "content": "🔍 查看完整网页版数据报表"},
    #             "url": result_url,
    #             "type": "default"
    #         })

    # 6. Interactive Actions / Recommended Questions Block
    if result.recommended_questions:
        elements.append({
            "tag": "markdown",
            "content": "**🚀 快捷追问深度分析：**"
        })
        for q in result.recommended_questions[:3]:
            display_title = q[:18] + "..." if len(q) > 18 else q
            elements.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"💬 {display_title}"},
                "type": "default",
                "value": {
                    "action": "quick_query",
                    "query": q
                }
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

    receive_id_type = "open_id" if chat_id.startswith("ou_") else "chat_id"
    logger.info("Sending premium interactive card to %s (type=%s), elements_count=%d", chat_id, receive_id_type, len(elements))
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        resp_json = resp.json()
        if resp.status_code != 200 or resp_json.get("code") != 0:
            logger.error("Feishu API error! Status: %d, Response: %s", resp.status_code, json.dumps(resp_json, ensure_ascii=False))
        else:
            logger.info("Feishu premium interactive card sent successfully.")
        return resp_json
