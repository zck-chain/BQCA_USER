from google.cloud import bigquery

from app.config import settings


async def run_query(sql: str) -> tuple[list[dict], list[str]]:
    """Execute BigQuery SQL, return (rows, columns). Truncate beyond MAX_RESULT_ROWS."""
    client = bigquery.Client(project=settings.BQ_PROJECT)
    job_config = bigquery.QueryJobConfig(job_timeout_ms=30000)
    query_job = client.query(sql, job_config=job_config)
    result = query_job.result()

    columns = [field.name for field in result.schema]
    rows = [dict(row.items()) for row in result]

    if len(rows) > settings.MAX_RESULT_ROWS:
        rows = rows[: settings.MAX_RESULT_ROWS]

    return rows, columns
