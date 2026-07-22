import json
import httpx

from app.config import settings

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
  async with httpx.AsyncClient() as client:
      resp = await client.post(
          "https://open.feishu.cn/open-apis/im/v1/messages",
          params={"receive_id_type": "chat_id"},
          headers={"Authorization": f"Bearer {token}"},
          json={
              "receive_id": chat_id,
              "msg_type": "text",
              "content": json.dumps({"text": text}),
          },
      )
      return resp.json()


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
