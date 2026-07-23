import logging
import re
from dataclasses import dataclass, field

import google.auth
from google.auth import impersonated_credentials
from google.cloud import geminidataanalytics
from google.protobuf.json_format import MessageToDict

from app.config import settings

logger = logging.getLogger(__name__)

# API Key → Service Account email mapping
# When a key maps to a different SA, we impersonate that SA
KEY_TO_SA: dict[str, str] = {
    "BQ-RnzRKoqvsvQ8flP8vLZi-THbjE4ct9N0dnIzyQ": "bqca-restricted@webeye-internal-test.iam.gserviceaccount.com",
}

# Patterns that indicate BQCA internal status, not user-facing text
_NOISE_PATTERNS = [
    re.compile(r"^Analyzing context", re.IGNORECASE),
    re.compile(r"^Retrieved context", re.IGNORECASE),
    re.compile(r"^Thinking", re.IGNORECASE),
    re.compile(r"^Processing", re.IGNORECASE),
    re.compile(r"^Generating", re.IGNORECASE),
    re.compile(r"^Querying", re.IGNORECASE),
]


@dataclass
class ChatResult:
    """Structured result from a BQCA chat call."""
    conversation_name: str = ""
    summary: str = ""
    sql: str = ""
    fields: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    vega_config: dict | None = None


def _agent_path() -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.CA_LOCATION}/dataAgents/{settings.CA_AGENT_ID}"


def _parent_path() -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.CA_LOCATION}"


def _get_credentials(target_sa: str | None = None):
    """
    Get credentials for calling CA API.
    If target_sa is specified and different from the default SA,
    impersonate that SA using the default credentials.
    """
    source_creds, project = google.auth.default()

    if target_sa is None:
        return None  # Use default (bqca-runner)

    # Check if target is the same as the current identity
    # If so, just use default credentials
    signer_email = getattr(source_creds, 'signer_email', None) or \
                   getattr(source_creds, '_signer', None) and \
                   getattr(source_creds._signer, 'email', None)
    if signer_email == target_sa:
        return None  # Same SA, use default

    logger.info("Impersonating SA: %s", target_sa)
    target_scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/generative-language",
    ]
    imp_creds = impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=target_sa,
        target_scopes=target_scopes,
    )
    return imp_creds


def _get_client(credentials=None) -> geminidataanalytics.DataChatServiceClient:
    """Create a DataChatServiceClient, optionally with specific credentials."""
    if credentials is None:
        return geminidataanalytics.DataChatServiceClient()
    return geminidataanalytics.DataChatServiceClient(credentials=credentials)


def create_conversation(credentials=None) -> str:
    """Create a new CA API conversation and return its resource name."""
    client = _get_client(credentials)
    conversation = geminidataanalytics.Conversation()
    conversation.agents = [_agent_path()]
    req = geminidataanalytics.CreateConversationRequest(
        parent=_parent_path(),
        conversation=conversation,
    )
    convo = client.create_conversation(request=req)
    logger.info("Created conversation: %s", convo.name)
    return convo.name


def _is_noise(text: str) -> bool:
    """Check if a text part is BQCA internal status, not user-facing."""
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _NOISE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def chat(question: str, conversation_name: str | None = None,
         api_key: str | None = None) -> ChatResult:
    """
    Send a question to the BQCA agent via the Conversational Analytics API.
    If conversation_name is None, a new conversation is created (single-turn).
    If api_key is provided, the corresponding SA is impersonated for the call.
    Returns a ChatResult with summary, SQL, data rows, and optional chart.
    """
    # Resolve which SA to impersonate based on API key
    target_sa = KEY_TO_SA.get(api_key) if api_key else None
    credentials = _get_credentials(target_sa)

    chat_client = _get_client(credentials)

    if conversation_name is None:
        conversation_name = create_conversation(credentials)

    user_msg = geminidataanalytics.Message(user_message={"text": question})
    convo_ref = geminidataanalytics.ConversationReference()
    convo_ref.conversation = conversation_name
    convo_ref.data_agent_context.data_agent = _agent_path()

    req = geminidataanalytics.ChatRequest(
        parent=_parent_path(),
        messages=[user_msg],
        conversation_reference=convo_ref,
    )

    result = ChatResult()
    result.conversation_name = conversation_name
    text_parts: list[str] = []

    for message in chat_client.chat(request=req):
        sm_dict = MessageToDict(message.system_message._pb)

        if "text" in sm_dict:
            parts = sm_dict["text"].get("parts", [])
            for part in parts:
                if not _is_noise(part):
                    text_parts.append(part)

        if "data" in sm_dict:
            data = sm_dict["data"]
            if "generatedSql" in data:
                result.sql = data["generatedSql"]
            if "result" in data:
                r = data["result"]
                result.fields = [f["name"] for f in r.get("schema", {}).get("fields", [])]
                result.rows = r.get("data", [])

        if "chart" in sm_dict:
            chart = sm_dict["chart"]
            if "result" in chart:
                result.vega_config = chart["result"].get("vegaConfig")

    if text_parts:
        result.summary = " ".join(text_parts)

    logger.info("BQCA chat done: %d rows, sql=%s, chart=%s, sa=%s",
                 len(result.rows), bool(result.sql), bool(result.vega_config),
                 target_sa or "default")
    return result
