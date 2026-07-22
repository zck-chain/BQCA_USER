import pytest
from app.engine.schema import format_schema_for_prompt


def test_format_schema_basic():
   schema = {
       "orders": ["order_id INT64", "user_id INT64", "status STRING"],
       "order_items": ["id INT64", "order_id INT64", "sale_price FLOAT64"],
   }
   result = format_schema_for_prompt("my_dataset", schema)
   assert "my_dataset.orders" in result
   assert "order_id INT64" in result
   assert "my_dataset.order_items" in result
   assert "sale_price FLOAT64" in result


def test_format_schema_empty():
   result = format_schema_for_prompt("my_dataset", {})
   assert result.strip() == ""
