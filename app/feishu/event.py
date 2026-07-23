import json
import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)


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

  # Strip all Feishu mention patterns:
  # @_user_1 @_user_2 etc. (old format)
  # @_all (mention all)
  # @_user (bare mention)
  text = re.sub(r"@_user\S*\s*", "", text).strip()
  # Also strip plain @botname patterns (some clients send the display name)
  # Only strip if it's the very beginning of the text
  text = re.sub(r"^\s*@[^@\s]+\s+", "", text).strip()

  logger.info("Extracted question from Feishu: raw=%r, clean=%r",
              data.get("text", ""), text)
  return text


def get_message_id(event: dict) -> str:
  """Get message ID for dedup."""
  return event.get("message", {}).get("message_id", "")


def get_chat_id(event: dict) -> str:
  """Get chat ID for replying."""
  return event.get("message", {}).get("chat_id", "")


def get_sender_id(event: dict) -> str:
  """Get sender open_id for session/permission mapping."""
  return event.get("sender", {}).get("sender_id", {}).get("open_id", "")
