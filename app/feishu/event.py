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


def extract_app_id(body: dict) -> str | None:
    """Extract Feishu app_id from webhook payload header or event."""
    header = body.get("header", {})
    if isinstance(header, dict) and header.get("app_id"):
        return header.get("app_id")
    event = body.get("event", {})
    if isinstance(event, dict) and event.get("app_id"):
        return event.get("app_id")
    return body.get("app_id")


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


def is_bot_mentioned(event: dict) -> bool:
    """Check if the bot is explicitly mentioned in a group chat.

    Returns False if there are no mentions or if the mentions only consist of @_all (mention all).
    """
    message = event.get("message", {})
    chat_type = message.get("chat_type", "")

    # In 1-on-1 (p2p) chat, all messages are directed to the bot
    if chat_type == "p2p":
        return True

    mentions = message.get("mentions", [])
    if not mentions:
        return False

    # Check if there is any mention that is NOT @_all
    for mention in mentions:
        key = mention.get("key", "")
        open_id = mention.get("id", {}).get("open_id", "")
        name = mention.get("name", "")

        # Ignore @_all / @所有人 / open_id == "all"
        if key == "@_all" or open_id == "all" or name in ("所有人", "All", "all"):
            continue

        # Found a specific bot/user mention
        return True

    return False


def parse_card_action(body: dict, get_chat_type_fn, get_conversation_fn) -> tuple[str | None, str | None]:
    """Parse Feishu card click event (e.g. quick_query button click).

    Returns (next_query, target_id) or (None, None).
    """
    event_payload = body.get("event") if isinstance(body.get("event"), dict) else body
    if not event_payload:
        event_payload = body

    action_data = event_payload.get("action") or body.get("action")
    if action_data and isinstance(action_data, dict):
        action_val = action_data.get("value", {})
        if action_val.get("action") == "quick_query":
            next_query = action_val.get("query")
            chat_id = event_payload.get("open_chat_id") or event_payload.get("context", {}).get("open_chat_id")
            open_id = (
                event_payload.get("open_id") or 
                event_payload.get("user", {}).get("open_id") or 
                event_payload.get("operator", {}).get("open_id")
            )
            
            if chat_id:
                target_id = chat_id
                stored_chat_type = get_chat_type_fn(chat_id)
                if stored_chat_type == "p2p":
                    if open_id:
                        target_id = open_id
                elif stored_chat_type == "group":
                    target_id = chat_id
                else:
                    if open_id and get_conversation_fn(open_id) and not get_conversation_fn(chat_id):
                        target_id = open_id
                return next_query, target_id
    return None, None

