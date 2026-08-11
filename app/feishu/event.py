import hmac
import json
import logging
import re
import time

from app.config import settings

logger = logging.getLogger(__name__)


def _expected_token(app_id: str | None = None) -> str:
    """Return the verification token configured for the bot that owns `app_id`.

    The game bot and the ecommerce bot use separate Feishu apps and therefore
    separate verification tokens. Returns "" when no token is configured for the
    resolved app (callers treat empty as "auth disabled").
    """
    if app_id and settings.GAME_FEISHU_APP_ID and app_id == settings.GAME_FEISHU_APP_ID:
        return settings.GAME_FEISHU_VERIFICATION_TOKEN or ""
    return settings.FEISHU_VERIFICATION_TOKEN or ""


def extract_event_token(body: dict) -> str:
    """Extract the verification token from a Feishu event payload.

    Feishu v2 (schema 2.0) events carry it in `header.token`; legacy v1 events
    and url_verification carry it at the top level as `token`.
    """
    if not isinstance(body, dict):
        return ""
    header = body.get("header")
    if isinstance(header, dict) and header.get("token"):
        return header.get("token") or ""
    return body.get("token") or ""


def verify_token(token: str, app_id: str | None = None) -> bool:
    """Verify a Feishu event verification token against the configured bot token.

    Returns True when the expected token is not configured (auth disabled, e.g.
    local development) OR when the provided token matches (constant-time).
    """
    expected = _expected_token(app_id)
    if not expected:
        return True
    if not token:
        return False
    return hmac.compare_digest(token, expected)


def verify_event(body: dict, app_id: str | None = None) -> bool:
    """Convenience: verify the token carried on a parsed Feishu event body."""
    return verify_token(extract_event_token(body), app_id=app_id)


def extract_app_id(body: dict) -> str | None:
    """Extract Feishu app_id from webhook payload header or event."""
    header = body.get("header", {})
    if isinstance(header, dict) and header.get("app_id"):
        return header.get("app_id")
    event = body.get("event", {})
    if isinstance(event, dict) and event.get("app_id"):
        return event.get("app_id")
    return body.get("app_id")


def get_event_id(body: dict) -> str:
    """Get the Feishu v2 event ID used for callback deduplication."""
    header = body.get("header", {})
    return header.get("event_id", "") if isinstance(header, dict) else ""


def is_event_expired(body: dict, max_age_seconds: float, now: float | None = None) -> bool:
    """Return whether a Feishu message callback is older than the accepted window."""
    event = body.get("event", {})
    message = event.get("message", {}) if isinstance(event, dict) else {}
    header = body.get("header", {})
    raw_timestamp = message.get("create_time")
    if raw_timestamp is None and isinstance(header, dict):
        raw_timestamp = header.get("create_time")
    if raw_timestamp is None:
        return False

    try:
        timestamp = float(raw_timestamp)
        while timestamp > 100_000_000_000:
            timestamp /= 1000
    except (TypeError, ValueError):
        return False

    return (time.time() if now is None else now) - timestamp > max_age_seconds


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
