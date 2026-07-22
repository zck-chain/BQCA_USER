from google.cloud import bigquery

from app.config import settings


_cached_schema: dict[str, list[str]] | None = None


def fetch_schema() -> dict[str, list[str]]:
   """Fetch table schema from BigQuery INFORMATION_SCHEMA, cache in memory."""
   global _cached_schema
   if _cached_schema is not None:
       return _cached_schema

   client = bigquery.Client(project=settings.BQ_PROJECT)
   query = f"""
       SELECT table_name, column_name, data_type
       FROM `{settings.BQ_PROJECT}.{settings.BQ_DATASET}.INFORMATION_SCHEMA.COLUMNS`
       ORDER BY table_name, ordinal_position
   """
   rows = client.query(query).result()
   schema: dict[str, list[str]] = {}
   for row in rows:
       table = row.table_name
       col_def = f"{row.column_name} {row.data_type}"
       schema.setdefault(table, []).append(col_def)

   _cached_schema = schema
   return schema


def format_schema_for_prompt(dataset: str, schema: dict[str, list[str]]) -> str:
   """Format schema as text for prompt injection."""
   lines = []
   for table, columns in schema.items():
       cols_str = ", ".join(columns)
       lines.append(f"- {dataset}.{table}({cols_str})")
   return "\n".join(lines)


def get_schema_text() -> str:
   """Get formatted schema text for prompt use."""
   schema = fetch_schema()
   return format_schema_for_prompt(f"{settings.BQ_PROJECT}.{settings.BQ_DATASET}", schema)
