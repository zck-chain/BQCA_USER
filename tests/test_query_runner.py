import pytest
from unittest.mock import patch, MagicMock
from app.engine.query_runner import run_query


def _make_mock_result(rows_data, columns):
   """Build a mock query result with schema and row.items()."""
   mock_result = MagicMock()
   mock_field = MagicMock()
   mock_field.name = columns[0]
   mock_result.schema = [MagicMock(name=c) for c in columns]
   for i, c in enumerate(columns):
       mock_result.schema[i].name = c
   mock_rows = []
   for row_data in rows_data:
       mock_row = MagicMock()
       mock_row.items.return_value = [(c, row_data.get(c)) for c in columns]
       mock_rows.append(mock_row)
   mock_result.__iter__ = lambda self: iter(mock_rows)
   return mock_result, mock_rows


@pytest.mark.asyncio
async def test_run_query_returns_rows():
   mock_result, mock_rows = _make_mock_result(
       [{"name": "Alice", "age": 30}], ["name", "age"]
   )
   mock_job = MagicMock()
   mock_job.result.return_value = mock_result
   mock_client = MagicMock()
   mock_client.query.return_value = mock_job

   with patch("app.engine.query_runner.bigquery.Client", return_value=mock_client), \
        patch("app.engine.query_runner.bigquery.QueryJobConfig", return_value=MagicMock()):
       rows, columns = await run_query("SELECT name, age FROM users LIMIT 10")

   assert len(rows) == 1
   assert "name" in columns


@pytest.mark.asyncio
async def test_run_query_truncates_rows():
   mock_result, _ = _make_mock_result(
       [{"a": i} for i in range(1500)], ["a"]
   )
   mock_job = MagicMock()
   mock_job.result.return_value = mock_result
   mock_client = MagicMock()
   mock_client.query.return_value = mock_job

   with patch("app.engine.query_runner.bigquery.Client", return_value=mock_client), \
        patch("app.engine.query_runner.bigquery.QueryJobConfig", return_value=MagicMock()), \
        patch("app.engine.query_runner.settings.MAX_RESULT_ROWS", 1000):
       rows, columns = await run_query("SELECT * FROM t")

   assert len(rows) <= 1000
