from abc import ABC, abstractmethod


class BaseCardAdapter(ABC):
    """Abstract Base Adapter for message and card rendering across office platforms

    (Feishu, DingTalk, WeCom, Slack, etc.).
    """

    @abstractmethod
    def format_summary(self, text: str) -> str:
        """Format and clean summary text (remove technical noise, convert math formulas)."""
        pass

    @abstractmethod
    async def send_text_message(self, target_id: str, text: str) -> dict:
        """Send plain text message to a chat room or open ID."""
        pass

    @abstractmethod
    async def send_result_card(
        self,
        target_id: str,
        question: str,
        summary: str,
        sql: str | None = None,
        fields: list[str] | None = None,
        rows: list[dict] | None = None,
        recommended_questions: list[str] | None = None,
        result_url: str | None = None,
    ) -> dict:
        """Send rich interactive card with formatted summary, SQL, data table, and follow-ups."""
        pass
