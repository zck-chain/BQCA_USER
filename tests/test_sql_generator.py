import pytest
from unittest.mock import patch, MagicMock
from app.engine.sql_generator import generate_sql


@pytest.mark.asyncio
async def test_generate_sql_returns_sql():
  mock_response = MagicMock()
  mock_response.text = "SELECT * FROM orders LIMIT 10"

  with patch("app.engine.sql_generator._call_gemini", return_value=mock_response) as mock_call:
      result = await generate_sql("查看最近的订单", "test_dataset.orders(id INT, status STRING)")

  assert "SELECT" in result
  mock_call.assert_called_once()


@pytest.mark.asyncio
async def test_generate_sql_prompt_contains_schema():
  mock_response = MagicMock()
  mock_response.text = "SELECT * FROM orders LIMIT 10"

  with patch("app.engine.sql_generator._call_gemini", return_value=mock_response) as mock_call:
      await generate_sql("查看最近的订单", "test_dataset.orders(id INT)")

  call_args = mock_call.call_args
  prompt_text = call_args[0][0]
  assert "test_dataset.orders" in prompt_text
  assert "id INT" in prompt_text


@pytest.mark.asyncio
async def test_generate_sql_strips_markdown():
  mock_response = MagicMock()
  mock_response.text = "```sql\nSELECT * FROM orders LIMIT 10\n```"

  with patch("app.engine.sql_generator._call_gemini", return_value=mock_response):
      result = await generate_sql("test", "schema")

  assert not result.startswith("```")
  assert "SELECT" in result
