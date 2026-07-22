import logging
from dataclasses import dataclass, field

from google.cloud import geminidataanalytics
from google.protobuf.json_format import MessageToDict

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """Structured result from a BQCA chat call."""
    summary: str = ""
    sql: str = ""
    fields: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    vega_config: dict | None = None


def _agent_path() -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.CA_LOCATION}/dataAgents/{settings.CA_AGENT_ID}"


def _parent_path() -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.CA_LOCATION}"


def create_conversation() -> str:
    """Create a new CA API conversation and return its resource name."""
    client = geminidataanalytics.DataChatServiceClient()
    conversation = geminidataanalytics.Conversation()
    conversation.agents = [_agent_path()]
    req = geminidataanalytics.CreateConversationRequest(
        parent=_parent_path(),
        conversation=conversation,
    )
    convo = client.create_conversation(request=req)
    logger.info("Created conversation: %s", convo.name)
    return convo.name


def chat(question: str, conversation_name: str | None = None) -> ChatResult:
    """
    Send a question to the BQCA agent via the Conversational Analytics API.
    If conversation_name is None, a new conversation is created (single-turn).
    Returns a ChatResult with summary, SQL, data rows, and optional chart.
    """
    chat_client = geminidataanalytics.DataChatServiceClient()

    if conversation_name is None:
        conversation_name = create_conversation()

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
    text_parts: list[str] = []

    for message in chat_client.chat(request=req):
        sm_dict = MessageToDict(message.system_message._pb)

        if "text" in sm_dict:
            parts = sm_dict["text"].get("parts", [])
            text_parts.extend(parts)

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

    logger.info("BQCA chat done: %d rows, sql=%s, chart=%s",
                 len(result.rows), bool(result.sql), bool(result.vega_config))
    return result
