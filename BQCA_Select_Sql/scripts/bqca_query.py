#!/usr/bin/env python3
"""
BQCA Select SQL - 核心查询模块

通过自然语言向 BQCA Agent 提问，返回结构化查询结果。
支持 API Key → Service Account 权限隔离。

用法:
    from bqca_query import BQCAClient, ChatResult

    client = BQCAClient(project="your-project", agent_id="your-agent")
    result = client.ask("查看订单数量前5的商品类别", api_key="YOUR_KEY")
    print(result.summary, result.rows)

命令行:
    python bqca_query.py --question "查看订单数量前5的商品类别" --api-key YOUR_KEY
"""

import logging
import os
import re
import argparse
import json
from dataclasses import dataclass, field

import google.auth
from google.auth import impersonated_credentials
from google.cloud import geminidataanalytics
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

# BQCA 内部状态文本模式（非用户可见内容，需过滤）
_NOISE_PATTERNS = [
    re.compile(r"^Analyzing context", re.IGNORECASE),
    re.compile(r"^Retrieved context", re.IGNORECASE),
    re.compile(r"^Thinking", re.IGNORECASE),
    re.compile(r"^Processing", re.IGNORECASE),
    re.compile(r"^Generating", re.IGNORECASE),
    re.compile(r"^Querying", re.IGNORECASE),
    re.compile(r"^Running a query", re.IGNORECASE),
    re.compile(r"^Executing:", re.IGNORECASE),
    re.compile(r"^Navigating", re.IGNORECASE),
    re.compile(r"^Initial ", re.IGNORECASE),
]


@dataclass
class ChatResult:
    """BQCA 查询结果"""
    conversation_name: str = ""
    summary: str = ""
    sql: str = ""
    fields: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    vega_config: dict | None = None

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "sql": self.sql,
            "fields": self.fields,
            "rows": self.rows[:50],
            "chart": bool(self.vega_config),
            "conversation_id": self.conversation_name,
        }


class BQCAClient:
    """
    BQCA 查询客户端

    Args:
        project: GCP 项目 ID
        agent_id: BQCA Agent ID
        location: Agent 位置，默认 "global"
        key_to_sa: API Key → Service Account 映射字典
    """

    def __init__(
        self,
        project: str | None = None,
        agent_id: str | None = None,
        location: str = "global",
        key_to_sa: dict[str, str] | None = None,
    ):
        self.project = project or os.getenv("GCP_PROJECT", "")
        self.agent_id = agent_id or os.getenv("CA_AGENT_ID", "")
        self.location = location
        self.key_to_sa = key_to_sa or {}

    def _agent_path(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/dataAgents/{self.agent_id}"

    def _parent_path(self) -> str:
        return f"projects/{self.project}/locations/{self.location}"

    def _get_credentials(self, target_sa: str | None = None):
        """获取凭据。如指定 target_sa 且与默认身份不同，则 impersonate。"""
        source_creds, _ = google.auth.default()

        if target_sa is None:
            return None  # 使用默认身份

        signer_email = getattr(source_creds, 'signer_email', None) or \
                       getattr(source_creds, '_signer', None) and \
                       getattr(source_creds._signer, 'email', None)
        if signer_email == target_sa:
            return None

        logger.info("Impersonating SA: %s", target_sa)
        return impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=target_sa,
            target_scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/generative-language",
            ],
        )

    def _get_client(self, credentials=None) -> geminidataanalytics.DataChatServiceClient:
        if credentials is None:
            return geminidataanalytics.DataChatServiceClient()
        return geminidataanalytics.DataChatServiceClient(credentials=credentials)

    def create_conversation(self, credentials=None) -> str:
        """创建新会话，返回会话资源名。"""
        client = self._get_client(credentials)
        conversation = geminidataanalytics.Conversation()
        conversation.agents = [self._agent_path()]
        req = geminidataanalytics.CreateConversationRequest(
            parent=self._parent_path(),
            conversation=conversation,
        )
        convo = client.create_conversation(request=req)
        logger.info("Created conversation: %s", convo.name)
        return convo.name

    @staticmethod
    def _is_noise(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        for pattern in _NOISE_PATTERNS:
            if pattern.match(stripped):
                return True
        return False

    def ask(
        self,
        question: str,
        conversation_name: str | None = None,
        api_key: str | None = None,
    ) -> ChatResult:
        """
        向 BQCA Agent 提问。

        Args:
            question: 自然语言问题
            conversation_name: 会话 ID，None 则新建会话
            api_key: API Key，用于确定 impersonate 的 Service Account

        Returns:
            ChatResult 结构化结果
        """
        target_sa = self.key_to_sa.get(api_key) if api_key else None
        credentials = self._get_credentials(target_sa)
        chat_client = self._get_client(credentials)

        if conversation_name is None:
            conversation_name = self.create_conversation(credentials)

        user_msg = geminidataanalytics.Message(user_message={"text": question})
        convo_ref = geminidataanalytics.ConversationReference()
        convo_ref.conversation = conversation_name
        convo_ref.data_agent_context.data_agent = self._agent_path()

        req = geminidataanalytics.ChatRequest(
            parent=self._parent_path(),
            messages=[user_msg],
            conversation_reference=convo_ref,
        )

        result = ChatResult()
        result.conversation_name = conversation_name
        text_parts: list[str] = []

        for message in chat_client.chat(request=req):
            sm_dict = MessageToDict(message.system_message._pb)

            if "text" in sm_dict:
                parts = sm_dict["text"].get("parts", [])
                for part in parts:
                    if not self._is_noise(part):
                        text_parts.append(part)

            if "data" in sm_dict:
                data = sm_dict["data"]
                if "generatedSql" in data:
                    result.sql = data["generatedSql"]
                if "result" in data:
                    r = data["result"]
                    result.fields = [f["name"] for f in r.get("schema", {}).get("fields", [])]
                    result.rows = r.get("data", [])

            if "chart" in sm_dict:
                chart = sm_dict["chart"]
                if "result" in chart:
                    result.vega_config = chart["result"].get("vegaConfig")

        if text_parts:
            result.summary = " ".join(text_parts)

        logger.info("BQCA chat done: %d rows, sql=%s, chart=%s, sa=%s",
                     len(result.rows), bool(result.sql), bool(result.vega_config),
                     target_sa or "default")
        return result


# === 命令行入口 ===
def main():
    parser = argparse.ArgumentParser(description="BQCA Select SQL - 自然语言查询 BigQuery")
    parser.add_argument("--question", "-q", required=True, help="自然语言问题")
    parser.add_argument("--api-key", "-k", default=None, help="API Key（用于权限隔离）")
    parser.add_argument("--project", "-p", default=None, help="GCP 项目 ID")
    parser.add_argument("--agent-id", "-a", default=None, help="BQCA Agent ID")
    parser.add_argument("--conversation-id", "-c", default=None, help="会话 ID（多轮对话）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    client = BQCAClient(project=args.project, agent_id=args.agent_id)
    result = client.ask(args.question, args.conversation_id, args.api_key)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        if result.summary:
            print(f"\n摘要: {result.summary}\n")
        if result.sql:
            print(f"SQL: {result.sql}\n")
        if result.fields and result.rows:
            print("数据:")
            for row in result.rows[:10]:
                print("  ", {f: row.get(f, "") for f in result.fields})
            if len(result.rows) > 10:
                print(f"  ... 共 {len(result.rows)} 行")
        if result.vega_config:
            print("(包含图表配置)")


if __name__ == "__main__":
    main()
