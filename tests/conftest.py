import pytest
from app.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        GCP_PROJECT="test-project",
        CA_AGENT_ID="test-agent",
        GCS_BUCKET="test-bucket",
        FEISHU_APP_ID="test_app_id",
        FEISHU_APP_SECRET="test_secret",
        FEISHU_VERIFICATION_TOKEN="test_token",
    )
