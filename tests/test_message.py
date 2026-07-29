import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.feishu.message import send_text_message, send_result_card


@pytest.mark.asyncio
async def test_send_text_message_calls_api():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "tenant_access_token": "fake_token"}

    with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
         patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await send_text_message("oc_test", "正在查询...")

        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_result_card_contains_link():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "tenant_access_token": "fake_token"}

    with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
         patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await send_result_card("oc_test", "摘要内容", "https://example.com/result.html")

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert "https://example.com/result.html" in str(body)

