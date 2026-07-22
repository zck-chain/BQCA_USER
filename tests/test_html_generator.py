import pytest
from app.bqca.client import ChatResult
from app.renderer.html_generator import build_result_html


def test_build_result_html_with_data():
    result = ChatResult(
        summary="共有10个订单",
        sql="SELECT * FROM orders LIMIT 10",
        fields=["订单ID", "金额"],
        rows=[{"订单ID": "1", "金额": "100"}, {"订单ID": "2", "金额": "200"}],
        vega_config=None,
    )
    html = build_result_html("最近10个订单", result)
    assert "最近10个订单" in html
    assert "订单ID" in html
    assert "共有10个订单" in html
    assert "SELECT" in html


def test_build_result_html_with_chart():
    vega = {"mark": "bar", "data": {"values": [{"x": "A", "y": 1}]}, "encoding": {}}
    result = ChatResult(vega_config=vega)
    html = build_result_html("图表测试", result)
    assert "vegaEmbed" in html
    assert '"mark": "bar"' in html


def test_build_result_html_empty():
    result = ChatResult()
    html = build_result_html("测试", result)
    assert "测试" in html
