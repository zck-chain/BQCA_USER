import pytest
from app.config import Settings


@pytest.fixture
def test_settings():
   return Settings(
       BQ_PROJECT="test-project",
       BQ_DATASET="test_dataset",
       GCS_BUCKET="test-bucket",
       FEISHU_APP_ID="test_app_id",
       FEISHU_APP_SECRET="test_secret",
       FEISHU_VERIFICATION_TOKEN="test_token",
       GEMINI_MODEL="gemini-2.0-flash",
       MAX_RESULT_ROWS=100,
   )
