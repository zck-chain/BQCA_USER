import pytest
from unittest.mock import patch, MagicMock
from app.renderer.html_generator import generate_html_and_summary


@pytest.mark.asyncio
async def test_generate_returns_html_and_summary():
  mock_response = MagicMock()
  mock_response.text = "---HTML---\n<html><body>chart</body></html>\n---SUMMARY---\n销售额最高的品类是电子"

  with patch("app.renderer.html_generator._call_gemini", return_value=mock_response):
      html, summary = await generate_html_and_summary(
          "哪个品类卖得最好",
          [{"category": "Electronics", "total": 5000}],
          ["category", "total"],
      )

  assert "<html>" in html
  assert "电子" in summary or "品类" in summary


@pytest.mark.asyncio
async def test_generate_fallback_on_exception():
  with patch("app.renderer.html_generator._call_gemini", side_effect=Exception("API error")):
      html, summary = await generate_html_and_summary(
          "test", [{"a": 1}], ["a"]
      )

  assert "<html>" in html
  assert "查看详情" in summary
