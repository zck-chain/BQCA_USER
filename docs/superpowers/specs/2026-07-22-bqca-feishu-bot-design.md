# BQCA 飞书智能查询助手 — 设计规格文档

## 产品定位

让业务人员通过飞书机器人，用自然语言查询 BigQuery 电商数据，获得可视化结果。先内部验证，成熟后考虑对外。

核心价值：**零 SQL 门槛 + 即时可视化 + 飞书内闭环**

---

## 现有资源

| 资源 | 详情 |
|------|------|
| GCP 项目 | `webeye-internal-test` |
| Service Account | `bqca-runner@webeye-internal-test.iam.gserviceaccount.com`（BQ Admin、Gemini Data Analytics Admin、GCS ObjectAdmin 等） |
| 已启用 API | BigQuery、Vertex AI、Gemini Data Analytics、Cloud Run、Cloud Storage |
| BQCA Agent | `ecommerce-analyst-cn`（电商分析师，含 thelook_bq 10 表 + firebas_bq 2 表） |
| GCS 桶 | `bqca-results`（公开读，asia-east1） |
| Cloud Run | `bqca-bot`，asia-east1，URL: `https://bqca-bot-839062387451.asia-east1.run.app` |
| 飞书应用 | App ID: 已配置，已订阅 `im.message.receive_v1` |
| API Key | 已配置（admin 权限） |

---

## 架构与数据流

核心思路：**问题直接交给 BQCA Conversational Analytics API 处理**，不自己造轮子做 NL→SQL。BQCA Agent 内部完成自然语言理解、SQL 生成、BigQuery 查询执行、结果分析全流程。

```
飞书用户 @机器人 "上个月哪个品类卖得最好？"
    │
    ▼
[飞书 Event Webhook]
    │
    ▼
[Cloud Run: FastAPI 服务]
    │
    ├─ 1. 飞书事件模块：验签 + 提取问题文本 + 即时回复"正在查询..."
    │
    ├─ 2. 权限模块：根据 API Key / 飞书用户 ID 查权限配置
    │     └─ 构建 systemInstruction 注入访问规则（软约束）
    │
    ├─ 3. BQCA 查询：问题 + 权限规则 → CA API → Agent 处理全流程
    │     └─ Agent 内部：NL→SQL→BQ 查询→结果分析→图表生成
    │
    ├─ 4. 权限后置检查：校验 SQL 是否越权 + 过滤结果中敏感列（硬约束）
    │
    ├─ 5. HTML 渲染：问题 + 数据 + 图表 → Vega-Lite 嵌入的 HTML 页面
    │
    ├─ 6. 存储：HTML → GCS 公开桶 → 返回 URL
    │
    └─ 7. 回复飞书：摘要 + "查看详情"卡片链接
          └─ 幂等处理：基于 message_id 去重，防飞书重试
```

认证：所有 GCP 调用用 `bqca-runner` SA，飞书用户零权限要求。

---

## 关键技术决策

### 为什么用 CA API 而不是自建 SQL 引擎

| 对比项 | 自建引擎（旧方案） | CA API Direct（当前方案） |
|--------|-------------------|--------------------------|
| NL→SQL | 自己调 Gemini 生成，需维护 prompt + schema | BQCA Agent 内部处理 |
| 查询执行 | 自己调 BigQuery SDK | BQCA Agent 内部执行 |
| 图表生成 | 自己调 Gemini 生成 ECharts | BQCA 返回 Vega-Lite 配置 |
| 对话上下文 | 无 | CA API 原生支持多轮对话 |
| Schema 管理 | 启动时拉取缓存 | BQCA Agent 管理 |
| 维护成本 | 高（3 次 Gemini 调用 + BQ 执行 + 安全检查） | 低（1 次 CA API 调用） |

### CA API 调用方式

当前使用 `conversation_reference` 模式（有状态，Agent 处理一切）：
- `ChatRequest.conversation_reference.conversation` → 已创建的会话
- `ChatRequest.conversation_reference.data_agent_context.data_agent` → Agent 路径

权限控制增强后计划切换到 `client_managed_resource_context` 模式：
- 可同时指定 `agent_id` + `inline_context`（含动态 `system_instruction`）
- 无需 CA API 管理会话持久化，由应用层控制

---

## 模块职责与边界

```
app/
├── main.py              # FastAPI 入口：/health, /api/query, /webhook/event
├── config.py            # 配置管理（环境变量）
├── permissions.py       # 权限控制（Profile 配置、systemInstruction 构建、SQL 检查、列过滤）
├── bqca/
│   ├── __init__.py
│   └── client.py        # CA API 调用：chat(), create_conversation(), ChatResult
├── renderer/
│   ├── __init__.py
│   └── html_generator.py # HTML 渲染：Vega-Lite 图表 + 数据表格 + 摘要
├── storage/
│   ├── __init__.py
│   └── gcs.py           # GCS 上传 HTML，返回公开 URL
└── feishu/
    ├── __init__.py
    ├── event.py         # 接收事件、提取问题、获取 message_id/chat_id
    └── message.py       # 发送文本消息、结果卡片
```

**各模块职责：**
- `bqca/client.py`：封装 CA API 调用，返回结构化的 `ChatResult`（summary + sql + fields + rows + vega_config）
- `permissions.py`：API Key/飞书用户 → PermissionProfile 映射，三层权限执行
- `renderer/html_generator.py`：将 ChatResult 渲染为包含 Vega-Lite 嵌入的 HTML 页面
- `feishu/`：飞书事件接收和消息回复

---

## 权限控制设计

### 三层执行模型

| 层级 | 机制 | 说明 |
|------|------|------|
| 软约束 | systemInstruction 注入 | 在 CA API 请求中注入访问规则，引导 Agent 不越权 |
| 硬约束 1 | SQL 后置检查 | 检查 BQCA 生成的 SQL 是否引用了被禁止的表 |
| 硬约束 2 | 结果列过滤 | 从返回数据中移除敏感列（email、phone 等） |

### 权限配置

```python
# 权限 Profile 定义
PROFILES = {
    "admin": PermissionProfile(agent_id="ecommerce-analyst-cn"),  # 全权限
    "sales": PermissionProfile(
        agent_id="ecommerce-analyst-cn",
        allowed_tables=["orders", "products", "order_items"],
        column_restrictions={"users": ["id", "first_name", "last_name"]},
    ),
    "marketing": PermissionProfile(
        agent_id="ecommerce-analyst-cn",
        column_restrictions={"users": ["id", "first_name", ...]},  # 不含 email
    ),
}

# API Key → Profile 映射
API_KEY_MAP = {"<YOUR_API_KEY>": "admin"}

# 飞书用户 → Profile 映射
FEISHU_USER_MAP = {}
```

### 为什么不在 BigQuery 层做权限

所有请求通过 `bqca-runner` SA 访问 BigQuery（该 SA 有 BQ Admin），BigQuery 的 IAM 策略对它不生效。权限控制必须在应用层实现。

---

## 配置项（环境变量）

| 变量 | 说明 | 示例 |
|------|------|------|
| `GCP_PROJECT` | GCP 项目 ID | `webeye-internal-test` |
| `CA_AGENT_ID` | BQCA Agent ID | `ecommerce-analyst-cn` |
| `CA_LOCATION` | CA API 区域 | `global` |
| `GCS_BUCKET` | HTML 存储桶名 | `bqca-results` |
| `API_KEY` | 管理 API Key | `UC-...` |
| `FEISHU_APP_ID` | 飞书应用 ID | 已配置 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | （已配置） |
| `FEISHU_VERIFICATION_TOKEN` | 事件验签 token | （已配置） |
| `FEISHU_ENCRYPT_KEY` | 事件加密 key | 空（未启用） |

---

## API 端点

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 无 | 健康检查 |
| `/api/query` | POST | API Key（Header `X-API-Key` 或 Query `key`） | 核心查询接口，返回 summary/sql/fields/rows/chart/html_url |
| `/webhook/event` | POST | 飞书验签 | 飞书事件回调，异步处理 |

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| BQCA API 调用失败 | 回复"查询处理失败，请稍后再试或换种说法" |
| SQL 越权（引用被禁止的表） | 回复"您没有权限查看这些数据" |
| 结果包含敏感列 | 自动过滤后返回 |
| 飞书事件重复推送 | 基于 message_id 去重 |
| 无 API Key 或 Key 无效 | 返回 401 |

---

## GCP 资源与部署

**已有资源：**
1. GCS 公开桶 `bqca-results`（allUsers 可读）
2. Cloud Run 服务 `bqca-bot`，绑定 `bqca-runner` SA

**部署命令：**
```bash
gcloud run deploy bqca-bot \
  --source . \
  --project=webeye-internal-test \
  --region=asia-east1 \
  --service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com \
  --set-env-vars="GCP_PROJECT=webeye-internal-test,CA_AGENT_ID=ecommerce-analyst-cn,GCS_BUCKET=bqca-results,FEISHU_APP_ID=${FEISHU_APP_ID},FEISHU_APP_SECRET=${FEISHU_APP_SECRET},FEISHU_VERIFICATION_TOKEN=${FEISHU_VERIFICATION_TOKEN},FEISHU_ENCRYPT_KEY=,API_KEY=${API_KEY}" \
  --allow-unauthenticated \
  --platform=managed
```

---

## 版本规划

**V1（已完成）：** 核心问答链路 — 飞书提问 → CA API → HTML 可视化 → 返回摘要+链接

**V2（当前）：** 权限体系 + 对话式追问 + 定时报告推送 + 数据异常告警 + 多数据集

**V3：** 高级权限（行级策略）+ 查询审计日志 + 多租户隔离
