import pytest
from unittest.mock import patch, MagicMock
from app.storage.gcs import upload_html


@pytest.mark.asyncio
async def test_upload_html_returns_url():
  mock_bucket = MagicMock()
  mock_blob = MagicMock()
  mock_blob.public_url = "https://storage.googleapis.com/test-bucket/results/test-id.html"
  mock_bucket.blob.return_value = mock_blob

  mock_client = MagicMock()
  mock_client.bucket.return_value = mock_bucket

  with patch("app.storage.gcs.storage.Client", return_value=mock_client):
      url = await upload_html("test-id", "<html>hello</html>")

  assert "test-bucket" in url
  assert "test-id" in url
  mock_blob.upload_from_string.assert_called_once_with("<html>hello</html>", content_type="text/html")
