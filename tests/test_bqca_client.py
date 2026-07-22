import pytest
from unittest.mock import patch, MagicMock
from app.bqca.client import create_conversation, ChatResult


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
