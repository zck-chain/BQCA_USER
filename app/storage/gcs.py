import uuid
from google.cloud import storage

from app.config import settings


async def upload_html(query_id: str, html_content: str) -> str:
  """Upload HTML to GCS public bucket, return public URL."""
  client = storage.Client(project=settings.GCP_PROJECT)
  bucket = client.bucket(settings.GCS_BUCKET)
  blob_name = f"results/{query_id}.html"
  blob = bucket.blob(blob_name)
  blob.upload_from_string(html_content, content_type="text/html")
  return blob.public_url


def generate_query_id() -> str:
  """Generate unique query ID for file naming and dedup."""
  return uuid.uuid4().hex[:12]
