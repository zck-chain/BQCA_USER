import logging
import re
from dataclasses import dataclass, field

import google.auth
from google.auth import impersonated_credentials
from google.cloud import geminidataanalytics
from google.protobuf.json_format import MessageToDict

from app.config import settings

logger = logging.getLogger(__name__)

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
    thinking_process: list[str] = field(default_factory=list)
    recommended_questions: list[str] = field(default_factory=list)


def _agent_path(agent_id: str | None = None, location: str | None = None) -> str:
    agent = agent_id or settings.CA_AGENT_ID
    loc = location or settings.CA_LOCATION
    return f"projects/{settings.GCP_PROJECT}/locations/{loc}/dataAgents/{agent}"


def _parent_path(location: str | None = None) -> str:
    loc = location or settings.CA_LOCATION
    return f"projects/{settings.GCP_PROJECT}/locations/{loc}"


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


def create_conversation(credentials=None, agent_id: str | None = None, location: str | None = None) -> str:
    """Create a new CA API conversation and return its resource name."""
    client = _get_client(credentials)
    conversation = geminidataanalytics.Conversation()
    conversation.agents = [_agent_path(agent_id, location)]
    req = geminidataanalytics.CreateConversationRequest(
        parent=_parent_path(location),
        conversation=conversation,
    )
    convo = client.create_conversation(request=req)
    logger.info("Created conversation: %s for agent: %s (loc=%s)", convo.name, agent_id or settings.CA_AGENT_ID, location or settings.CA_LOCATION)
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
         target_service_account: str | None = None,
         agent_id: str | None = None,
         location: str | None = None) -> ChatResult:
    """
    Send a question to the BQCA agent via the Conversational Analytics API.
    If conversation_name is None, a new conversation is created (single-turn).
    If target_service_account is provided, impersonate it for the call.
    If agent_id or location is provided, route the call to the specified BQCA data agent and GCP region.
    Returns a ChatResult with summary, SQL, data rows, and optional chart.
    """
    credentials = _get_credentials(target_service_account)

    chat_client = _get_client(credentials)

    if conversation_name is None:
        conversation_name = create_conversation(credentials, agent_id=agent_id, location=location)

    user_msg = geminidataanalytics.Message(user_message={"text": question})
    convo_ref = geminidataanalytics.ConversationReference()
    convo_ref.conversation = conversation_name
    convo_ref.data_agent_context.data_agent = _agent_path(agent_id, location)

    req = geminidataanalytics.ChatRequest(
        parent=_parent_path(location),
        messages=[user_msg],
        conversation_reference=convo_ref,
    )

    result = ChatResult()
    result.conversation_name = conversation_name
    text_parts: list[str] = []

    for message in chat_client.chat(request=req):
        sm_dict = MessageToDict(message.system_message._pb)

        if "text" in sm_dict:
            text_obj = sm_dict["text"]
            text_type = text_obj.get("textType", "")
            parts = text_obj.get("parts", [])
            
            if text_type == "FOLLOWUP_QUESTIONS":
                for part in parts:
                    q = part.strip()
                    if q and q not in result.recommended_questions:
                        result.recommended_questions.append(q)
            elif text_type == "THOUGHT":
                for part in parts:
                    cleaned_thought = part.strip()
                    if cleaned_thought and cleaned_thought not in result.thinking_process:
                        result.thinking_process.append(cleaned_thought)
            else:
                for part in parts:
                    if _is_noise(part):
                        cleaned_thought = part.strip()
                        if cleaned_thought and cleaned_thought not in result.thinking_process:
                            result.thinking_process.append(cleaned_thought)
                    else:
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

        if "exampleQueries" in sm_dict:
            eqs = sm_dict["exampleQueries"].get("exampleQueries", [])
            for eq in eqs:
                q = eq.get("naturalLanguageQuestion")
                if q and q not in result.recommended_questions:
                    result.recommended_questions.append(q)

    if text_parts:
        result.summary = " ".join(text_parts)

    logger.info("BQCA chat done: %d rows, sql=%s, chart=%s, sa=%s",
                 len(result.rows), bool(result.sql), bool(result.vega_config),
                 target_service_account or "default")
    return result


def extract_html_from_summary(summary: str) -> tuple[str | None, str]:
    """Extract ```html ... ``` code block generated natively by BQCA.

    Returns (html_code, clean_summary_without_html_block).
    """
    if not summary:
        return None, ""
    pattern = re.compile(r"```html\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(summary)
    if match:
        html_code = match.group(1).strip()
        clean_summary = pattern.sub("", summary).strip()
        return html_code, clean_summary
    return None, summary

