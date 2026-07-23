# BQCA 飞书智能查询助手 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现飞书机器人 → BQCA CA API → HTML 可视化 → 返回摘要+链接的完整问答链路

**架构：** Cloud Run 上的 FastAPI 服务，接收飞书事件 webhook，调用 BQCA Conversational Analytics API（Direct API 单代理模式），BQCA Agent 内部完成 NL→SQL→查询→分析全流程，服务端仅负责渲染 HTML 和存储。

**技术栈：** Python 3.11, FastAPI, google-cloud-geminidataanalytics, google-cloud-storage, httpx（飞书 API 调用）

**参考文档：**
- CA API 集成模式：https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/integration-patterns?hl=zh-cn
- 快速入门：https://github.com/looker-open-source/ca-api-quickstarts
- Golden Demo：https://github.com/looker-open-source/ca-demos-and-tools/tree/main/ca-api-golden-demo

---

## 任务 1：项目骨架与配置

**文件：**
- 创建：`app/__init__.py`
- 创建：`app/config.py`
- 创建：`requirements.txt`
- 创建：`.env.example`
- 创建：`app/main.py`
- 测试：`tests/conftest.py`

- [x] **步骤 1：创建 `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
google-cloud-geminidataanalytics==0.13.1
google-cloud-storage==2.18.0
httpx==0.27.2
pydantic-settings==2.5.2
python-dotenv==1.0.1
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [x] **步骤 2：创建 `app/config.py`**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GCP_PROJECT: str = "webeye-internal-test"
    CA_AGENT_ID: str = "ecommerce-analyst-cn"
    CA_LOCATION: str = "global"
    GCS_BUCKET: str = "bqca-results"
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY: str = ""
    API_KEY: str = ""
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

- [x] **步骤 3：创建最小 `app/main.py`**（/health 端点）

- [x] **步骤 4：验证配置加载和 health 端点**

---

## 任务 2：BQCA 客户端模块

**文件：**
- 创建：`app/bqca/__init__.py`
- 创建：`app/bqca/client.py`
- 测试：`tests/test_bqca_client.py`

**核心逻辑：** 封装 CA API 的 `DataChatServiceClient.chat()` 调用，返回结构化的 `ChatResult`。

- [x] **步骤 1：创建 `app/bqca/client.py`**

关键实现点：
- `create_conversation()` — 调 CA API 创建会话，返回会话资源名
- `chat(question, conversation_name)` — 调 CA API 发送问题，流式解析响应
- `ChatResult` 数据类 — summary + sql + fields + rows + vega_config
- 使用 `ConversationReference` + `DataAgentContext` 指定 Agent
- 用 `MessageToDict` 将 protobuf 响应转为 dict，提取 text/data/chart

```python
# CA API 调用核心逻辑
chat_client = geminidataanalytics.DataChatServiceClient()
convo_ref = geminidataanalytics.ConversationReference()
convo_ref.conversation = conversation_name
convo_ref.data_agent_context.data_agent = _agent_path()

req = geminidataanalytics.ChatRequest(
    parent=_parent_path(),
    messages=[user_msg],
    conversation_reference=convo_ref,
)

for message in chat_client.chat(request=req):
    sm_dict = MessageToDict(message.system_message._pb)
    # 解析 text, data, chart 三种消息类型
```

- [x] **步骤 2：验证 CA API 调用成功，返回正确的 ChatResult**

---

## 任务 3：飞书事件接收与消息发送

**文件：**
- 创建：`app/feishu/__init__.py`
- 创建：`app/feishu/event.py`
- 创建：`app/feishu/message.py`

- [x] **步骤 1：编写 `app/feishu/event.py`**

- `extract_question(event)` — 从飞书消息事件提取文本，去掉 @机器人
- `get_message_id(event)` — 获取消息 ID（用于去重）
- `get_chat_id(event)` — 获取会话 ID（用于回复）

- [x] **步骤 2：编写 `app/feishu/message.py`**

- `_get_tenant_token()` — 获取飞书 tenant_access_token
- `send_text_message(chat_id, text)` — 发送纯文本
- `send_result_card(chat_id, summary, result_url)` — 发送结果卡片（摘要 + 查看详情按钮）

- [x] **步骤 3：验证飞书事件回调正常接收和处理**

---

## 任务 4：HTML 渲染与 GCS 存储

**文件：**
- 创建：`app/renderer/__init__.py`
- 创建：`app/renderer/html_generator.py`
- 创建：`app/storage/__init__.py`
- 创建：`app/storage/gcs.py`

- [x] **步骤 1：编写 `app/renderer/html_generator.py`**

- `build_result_html(question, result)` — 将 ChatResult 渲染为完整 HTML
- 使用 Vega-Lite embed 渲染图表（BQCA 返回 Vega 配置，非 ECharts）
- 包含数据表格 + 摘要 + SQL 折叠详情
- 响应式布局，移动端可用

- [x] **步骤 2：编写 `app/storage/gcs.py`**

- `upload_html(query_id, html_content)` — 上传 HTML 到 GCS 公开桶
- `generate_query_id()` — 生成唯一查询 ID

- [x] **步骤 3：验证 HTML 生成和 GCS 上传**

---

## 任务 5：FastAPI 路由整合

**文件：**
- 修改：`app/main.py`

- [x] **步骤 1：实现完整路由**

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 无 | 健康检查 |
| `/api/query` | POST | API Key | 核心查询接口 |
| `/webhook/event` | POST | 飞书验签 | 飞书事件回调 |

- [x] **步骤 2：实现 API Key 认证中间件**

- Header `X-API-Key` 或 Query 参数 `key`
- 未配置 `API_KEY` 环境变量时跳过验证
- 仅保护 `/api/query`，`/webhook/event` 和 `/health` 不需要

- [x] **步骤 3：实现飞书异步处理链路**

```python
# 飞书事件处理流程
async def _process_query(question: str, chat_id: str):
    send_text_message("正在查询...")     # 即时反馈
    result = chat(question)              # CA API 查询
    html = build_result_html(result)     # 渲染 HTML
    url = upload_html(query_id, html)    # 上传 GCS
    send_result_card(summary, url)       # 回复卡片
```

- [x] **步骤 4：部署到 Cloud Run 并在飞书中端到端验证**

---

## 任务 6：对话式追问支持

**文件：**
- 修改：`app/bqca/client.py`
- 修改：`app/main.py`

- [ ] **步骤 1：实现会话管理**

- `/api/query` 支持 `conversation_id` 参数
- 飞书消息按 chat_id 维护会话
- 会话超时自动清理（30 分钟无活动）

- [ ] **步骤 2：在飞书中支持追问**

用户在同一个群内连续提问，自动关联到同一会话上下文。

---

## 任务 7：多 Agent 支持

**文件：**
- 修改：`app/bqca/client.py`
- 修改：`app/config.py`

- [ ] **步骤 1：支持通过参数选择不同 Agent**

当前已配置的 BQCA Agent：

| Agent ID | 名称 | 数据源 |
|----------|------|--------|
| `ecommerce-analyst-cn` | 电商分析师 | thelook_bq + firebas_bq |
| `agent_5a77361e-3039-41b5-9925-55588ef09837` | The Look Ecommerce | 同上 |
| `agent_57e8e1b2-3311-44b7-a0dc-42450a2462d4` | Agent for BigQuery Agent Analytics Data | agent_events_v2 |
| `agent_7a5d530d-379c-4f2a-8d47-a28d4aab2f12` | TPC-DS Retail Insights | tpc_ds_1g |
| `agent_86f6f746-efa5-4336-85e6-eb5af7d636b4` | 农夫山泉_测试环境 | nfsq_test |

- [ ] **步骤 2：`chat()` 函数支持动态传入 agent_id**

---

## 任务 8：定时报告推送与数据异常告警

**文件：**
- 新增：`app/scheduler/` 模块

- [ ] **步骤 1：实现定时查询调度**

- 支持配置定时任务（cron 表达式）
- 到时间自动调 CA API 查询并推送飞书消息

- [ ] **步骤 2：实现数据异常告警**

- 配置阈值规则
- 查询结果超阈值时主动推送告警到飞书

---

## 任务 9：部署与文档更新

- [ ] **步骤 1：更新 Cloud Run 部署**

- [ ] **步骤 2：更新设计文档和实现计划，确保与代码一致**
