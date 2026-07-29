from app.adapters.base import BaseCardAdapter
from app.adapters.feishu import FeishuAdapter


_ADAPTERS: dict[str, BaseCardAdapter] = {
    "feishu": FeishuAdapter(),
    "lark": FeishuAdapter(),
}


def get_card_adapter(platform: str = "feishu") -> BaseCardAdapter:
    """Factory method to resolve the card/message adapter for a given platform

    (feishu, dingtalk, wecom, slack, etc.).
    """
    adapter = _ADAPTERS.get(platform.lower())
    if not adapter:
        # Default fallback to FeishuAdapter
        return FeishuAdapter()
    return adapter
