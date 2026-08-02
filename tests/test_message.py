import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.feishu.message import send_text_message, send_result_card, vega_to_vchart


@pytest.mark.asyncio
async def test_send_text_message_calls_api():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "tenant_access_token": "fake_token"}

    with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
         patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await send_text_message("oc_test", "正在查询...")

        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_result_card_contains_link():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "tenant_access_token": "fake_token"}

    with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
         patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await send_result_card("oc_test", "摘要内容", "https://example.com/result.html")

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert "https://example.com/result.html" in str(body)


def test_composite_vega_spec_uses_png_fallback():
    spec = {
        "hconcat": [
            {
                "mark": "bar",
                "data": {"values": [{"category": "A", "count": 10}]},
                "encoding": {
                    "x": {"field": "category"},
                    "y": {"field": "count"},
                },
            },
            {
                "mark": "line",
                "data": {"values": [{"date": "2026-01-01", "rate": 0.5}]},
                "encoding": {
                    "x": {"field": "date"},
                    "y": {"field": "rate"},
                },
            },
        ],
    }

    assert vega_to_vchart(spec) is None


def test_feishu_adapter_format_summary_with_map_keys():
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()

    # 1. Test standard map keys
    raw_text = """
    LOGIC_EXPLANATION: 这是数据提取逻辑，使用了 thelook 数据库进行 GROUP BY。

    BUSINESS_INSIGHTS:
    1. [周中爆发与回落现象]：
       - 现象：9月27日出现峰值。
       - 建议：加强推广。
    """
    res = adapter.format_summary(raw_text)
    assert "**🎯 业务决策洞察：**" in res
    assert "1. [周中爆发与回落现象]：" in res
    assert "这是数据提取逻辑" not in res

    # 3. Test that common Chinese terms are NOT stripped from business insights
    raw_sensitive = """
    BUSINESS_INSIGHTS:
    1. [品牌表现统计]：根据我们对销售额的统计，7 For All Mankind 表现优异。
    2. [决策主要依据]：本次决策以库存字段为依据进行分组 and 分析。
    """
    res_sensitive = adapter.format_summary(raw_sensitive)
    assert "根据我们对销售额的统计" in res_sensitive
    assert "本次决策以库存字段为依据" in res_sensitive


def test_format_summary_with_dirty_brackets():
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()
    raw_text = """
    BUSINESS_INSIGHTS:
    )
    1. [品牌表现统计]：根据我们对销售额的统计，7 For All Mankind 表现优异。
    """
    res = adapter.format_summary(raw_text)
    assert "**🎯 业务决策洞察：**" in res
    assert "1. [品牌表现统计]：根据我们对销售额的统计" in res


def test_format_summary_nested_bullets():
    import textwrap
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()
    raw_text = textwrap.dedent("""\
    BUSINESS_INSIGHTS:
    2. [高库存压力与“库龄老化”危机并存]：
    - 现象：所有 Top 5 品牌的平均库存库龄均已突破 1100 天 (约合 3 年以上)。
    - 建议：强烈建议运营 and 仓储部门联合启动“清仓回笼”策略：
      - 组合折扣：对库龄超 1000 天的高价牛仔。
      - 跨渠道特卖：将这些陈年库存批量打包至奥特莱斯。""")
    res = adapter.format_summary(raw_text)
    assert "2. [高库存压力与“库龄老化”危机并存]：" in res
    assert "- 现象：所有 Top 5" in res
    assert "- 建议：强烈建议" in res
    assert "  - 组合折扣：" in res


def test_format_summary_inline_merged_bullets():
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()
    raw_text = """
    BUSINESS_INSIGHTS:
    1. 洞察要点一：🔍 现象： Swim (泳装) 品类在中低价位（20-50）段存在显著退货偏高问题。
    💡 建议：中低价位泳装退货率高，通常与尺码不合、材质弹性不佳或版型描述偏差有关。
    """
    res = adapter.format_summary(raw_text)
    assert "1. 洞察要点一：🔍 现象：" in res
    assert "💡 建议：" in res


def test_format_summary_drops_analysis_preamble_before_structured_insights():
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()
    raw_text = """
    BUSINESS_INSIGHTS:
    第四季度服饰商品退货率异常组合分析

    为了深度拆解服饰商品退货表现，我们选取了 2025 年第四季度这一完整季度。
    为识别出具有统计学意义和优化价值的异常组合，我们设定了以下过滤规则：
    1. 样本量足够：售出件数不得低于 10 件。
    2. 退货率显著偏高：高出季度整体水平 10 个百分点以上。
    通过四维交叉透视，共筛选出 16 个严重超出平均水平的异常组合。

    1. 特定履约仓与贴身衣物类目存在潜在质量或运营隐患
    - 现象：Comfort Choice 品牌的退货率高达 36.36%。
    - 建议：立即审计仓储、包装流程及发货时效。
    2. 高敏感品类在中低价格带极易因期望不符退货
    - 现象：Mommy Paradise 品牌的退货率高达 36.36%。
    - 建议：改善商品详情页的尺码与面料说明。
    """

    res = adapter.format_summary(raw_text)

    assert "1. 特定履约仓" in res
    assert "2. 高敏感品类" in res
    assert "第四季度服饰商品退货率异常组合分析" not in res
    assert "为了深度拆解" not in res
    assert "样本量足够" not in res
    assert "通过四维交叉透视" not in res


def test_format_summary_uses_explicit_business_insight_boundaries():
    from app.adapters.feishu import FeishuAdapter
    adapter = FeishuAdapter()
    raw_text = """
    LOGIC_EXPLANATION: 使用第四季度完整数据，并过滤低样本组合。
    BUSINESS_INSIGHTS_BEGIN
    1. 退货异常集中
    - 现象：三个组合的退货率超过 30%。
    - 建议：优先复核对应仓库和商品页面。
    BUSINESS_INSIGHTS_END
    推荐追问：是否需要继续查看详细订单？
    """

    res = adapter.format_summary(raw_text)

    assert "1. 退货异常集中" in res
    assert "使用第四季度完整数据" not in res
    assert "BUSINESS_INSIGHTS_BEGIN" not in res
    assert "BUSINESS_INSIGHTS_END" not in res
    assert "推荐追问" not in res
