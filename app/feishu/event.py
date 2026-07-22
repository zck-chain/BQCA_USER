import json
import re

from app.config import settings


def verify_token(token: str) -> bool:
  """Verify Feishu event token."""
  if not settings.FEISHU_VERIFICATION_TOKEN:
      return False
  return token == settings.FEISHU_VERIFICATION_TOKEN


def extract_question(event: dict) -> str:
  """Extract user question from Feishu message event, strip @bot mentions."""
  content = event.get("message", {}).get("content", "{}")
  msg_type = event.get("message", {}).get("message_type", "")

  if msg_type != "text":
      return ""

  data = json.loads(content)
  text = data.get("text", "")
  # Remove @user mentions
  text = re.sub(r"@_user_\d+\s*", "", text).strip()
  return text


def get_message_id(event: dict) -> str:
  """Get message ID for dedup."""
  return event.get("message", {}).get("message_id", "")


def get_chat_id(event: dict) -> str:
  """Get chat ID for replying."""
  return event.get("message", {}).get("chat_id", "")
