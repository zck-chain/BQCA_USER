import pytest
from unittest.mock import patch, MagicMock
from app.bqca.client import create_conversation, chat, chat_stream_events, ChatResult, BQCAEventType


def test_create_conversation():
    mock_client = MagicMock()
    mock_convo = MagicMock()
    mock_convo.name = "projects/test/locations/global/conversations/abc"
    mock_client.create_conversation.return_value = mock_convo

    with patch("app.bqca.client.geminidataanalytics.DataChatServiceClient", return_value=mock_client):
        name = create_conversation()
    assert name == "projects/test/locations/global/conversations/abc"


def test_chat_result_dataclass():
    result = ChatResult(summary="test", sql="SELECT 1", fields=["a"], rows=[{"a": 1}])
    assert result.summary == "test"
    assert result.sql == "SELECT 1"
    assert result.fields == ["a"]
    assert result.rows == [{"a": 1}]
    assert result.vega_config is None


def test_chat_only_uses_final_response_text_and_preserves_part_boundaries():
    mock_client = MagicMock()
    mock_client.chat.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    stream_messages = [
        {
            "text": {
                "textType": "THOUGHT",
                "parts": ["我正在分析查询口径"],
            }
        },
        {
            "text": {
                "textType": "PROGRESS",
                "parts": ["正在查询 BigQuery"],
            }
        },
        {
            "text": {
                "parts": ["正在整理查询结果"],
            }
        },
        {
            "text": {
                "textType": "FINAL_RESPONSE",
                "parts": [
                    "BUSINESS_INSIGHTS:\n",
                    "1. 销售表现稳定\n- 现象：销售额保持增长。\n- 建议：维持当前策略。",
                ],
            }
        },
    ]

    with patch("app.bqca.client._get_credentials", return_value=None), \
         patch("app.bqca.client._get_client", return_value=mock_client), \
         patch("app.bqca.client.MessageToDict", side_effect=stream_messages):
        result = chat(
            "分析销售情况",
            conversation_name="projects/test/locations/global/conversations/abc",
        )

    assert result.summary == (
        "BUSINESS_INSIGHTS:\n"
        "1. 销售表现稳定\n- 现象：销售额保持增长。\n- 建议：维持当前策略。"
    )
    assert result.thinking_process == ["我正在分析查询口径"]


@pytest.mark.asyncio
async def test_chat_stream_emits_summary_blocks_incrementally():
    mock_client = MagicMock()
    # Four gRPC stream messages: thought, then two FINAL_RESPONSE blocks, then end.
    mock_client.chat.return_value = [MagicMock(), MagicMock(), MagicMock()]
    stream_messages = [
        {"text": {"textType": "THOUGHT", "parts": ["分析中"]}},
        {"text": {"textType": "FINAL_RESPONSE", "parts": ["第一段洞察\n"]}},
        {"text": {"textType": "FINAL_RESPONSE", "parts": ["第二段洞察"]}},
    ]

    async def _fake_session(*args, **kwargs):
        return "projects/test/locations/global/conversations/abc"

    with patch("app.bqca.client._get_credentials", return_value=None), \
         patch("app.bqca.client._get_client", return_value=mock_client), \
         patch("app.bqca.client.conversation_pool.get_session", side_effect=_fake_session), \
         patch("app.bqca.client.MessageToDict", side_effect=stream_messages):
        events = []
        async for event in chat_stream_events(
            "分析销售情况",
            conversation_name="projects/test/locations/global/conversations/abc",
        ):
            events.append(event)

    types = [e.event_type for e in events]
    assert BQCAEventType.THOUGHT in types
    # Two SUMMARY events, one per FINAL_RESPONSE block — emitted before FINAL.
    summary_events = [e for e in events if e.event_type == BQCAEventType.SUMMARY]
    assert len(summary_events) == 2
    assert summary_events[0].data == "第一段洞察"
    assert summary_events[1].data == "第一段洞察\n第二段洞察"
    # FINAL carries the complete, de-duplicated summary.
    final = [e for e in events if e.event_type == BQCAEventType.FINAL][-1]
    assert final.result.summary == "第一段洞察\n第二段洞察"
    assert types[-1] == BQCAEventType.FINAL
