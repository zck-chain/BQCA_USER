# Conversational Analytics API 调用说明

## API 概述

BQCA 使用 Google Cloud Conversational Analytics API（geminidataanalytics），核心流程：

1. 创建会话（Conversation）
2. 在会话中发送消息（Chat）
3. 解析流式响应，提取数据和图表

## 关键资源路径

```
项目: projects/{PROJECT_ID}/locations/{LOCATION}
Agent: projects/{PROJECT_ID}/locations/{LOCATION}/dataAgents/{AGENT_ID}
会话: projects/{PROJECT_ID}/locations/{LOCATION}/conversations/{CONVERSATION_ID}
```

## Python SDK 用法

### 安装

```bash
pip install google-cloud-geminidataanalytics
```

### 基本调用

```python
from google.cloud import geminidataanalytics
from google.protobuf.json_format import MessageToDict

# 创建客户端
client = geminidataanalytics.DataChatServiceClient()

# 创建会话
conversation = geminidataanalytics.Conversation()
conversation.agents = [agent_path]
convo = client.create_conversation(
    request=geminidataanalytics.CreateConversationRequest(
        parent=parent_path,
        conversation=conversation,
    )
)

# 发送消息
user_msg = geminidataanalytics.Message(user_message={"text": "你的问题"})
convo_ref = geminidataanalytics.ConversationReference()
convo_ref.conversation = convo.name
convo_ref.data_agent_context.data_agent = agent_path

req = geminidataanalytics.ChatRequest(
    parent=parent_path,
    messages=[user_msg],
    conversation_reference=convo_ref,
)

# 解析流式响应
for message in client.chat(request=req):
    sm_dict = MessageToDict(message.system_message._pb)

    # 文本摘要
    if "text" in sm_dict:
        parts = sm_dict["text"].get("parts", [])

    # SQL + 数据行
    if "data" in sm_dict:
        sql = sm_dict["data"].get("generatedSql", "")
        result = sm_dict["data"].get("result", {})
        fields = [f["name"] for f in result.get("schema", {}).get("fields", [])]
        rows = result.get("data", [])

    # 图表配置
    if "chart" in sm_dict:
        vega_config = sm_dict["chart"]["result"].get("vegaConfig")
```

## Impersonation 模式

当需要用不同 Service Account 身份查询时，使用 impersonated_credentials：

```python
import google.auth
from google.auth import impersonated_credentials

source_creds, _ = google.auth.default()
imp_creds = impersonated_credentials.Credentials(
    source_credentials=source_creds,
    target_principal="target-sa@project.iam.gserviceaccount.com",
    target_scopes=[
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/generative-language",
    ],
)
client = geminidataanalytics.DataChatServiceClient(credentials=imp_creds)
```

## 响应过滤

BQCA 返回的文本中包含内部状态信息（如 "Analyzing context"、"Running a query"），
这些不应展示给用户。需要用正则过滤：

```python
NOISE_PATTERNS = [
    r"^Analyzing context",
    r"^Retrieved context",
    r"^Thinking",
    r"^Running a query",
    r"^Executing:",
    r"^Navigating",
]
```

## 多轮对话

同一会话内发送多条消息即可实现多轮对话：

```python
# 第一轮
result1 = client.ask("查看订单数量", conversation_name=None)
# result1.conversation_name 保存下来

# 第二轮（在同一会话中追问）
result2 = client.ask("前5名呢？", conversation_name=result1.conversation_name)
```

## 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| 403 Permission 'cloudaicompanion.topics.create' denied | 缺少 cloudaicompanion.user 角色 | gcloud 项目级加角色 |
| 403 User does not have permission to chat | 缺少 Agent 级 dataAgentUser | REST API 设 Agent IAM |
| 403 Access Denied: Table xxx | 缺少数据集读权限 | 给 SA 加 READER |
| 502/503 Getting metadata from plugin failed | impersonation 权限问题 | 检查 serviceAccountTokenCreator |
