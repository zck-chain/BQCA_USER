---
name: bqca-query-client
description: Use when users ask for BQCA business data from ecommerce or game domains, including orders, products, revenue, returns, players, DAU/MAU, retention, payment, levels, devices, or channels.
---

# BQCA 全领域智能数据查询

用于通过 BQCA 服务查询电商与游戏业务数据。执行时优先保证三件事：领域选对、角色权限明确、结果基于结构化数据呈现。

## 服务端接口

- **方法**：`POST`
- **生产地址**：`https://bqca-bot-839062387451.asia-east1.run.app/api/query`
- **请求头**：只传 `Content-Type: application/json`

服务端根据 `domain` 自动选择 BQCA Agent。客户端不得添加、展示或保存 API Key；认证与权限由服务端处理。

## 路由与权限

### 领域 (`domain`)

根据问题语义设置 `domain`：

| domain | 触发场景 |
|---|---|
| `game` | 玩家、DAU/MAU、留存、付费渗透、ARPU/ARPPU、关卡、Flood-It、道具、设备、操作系统、游戏渠道 |
| `ecommerce` | 订单、商品、SKU、销售额、退货、发货、物流、商品状态、电商渠道 |

如果问题同时可能属于两个领域：
- 有历史 `session_id` 时，优先沿用上一轮 `domain`。
- 无历史上下文时，先向用户确认领域，不要猜。

### 角色 (`role`)

服务端支持两个角色：

| 用户表达 | role |
|---|---|
| 运营经理、经理、管理员、高管、游戏策划、数据分析师、分析师 | `运营经理` |
| 一线客服、客服、物流、玩家支持 | `一线客服` |

首次业务查询如果无法识别角色，先询问用户身份；不要默认提升为 `运营经理`。用户要求“切换为客服/经理”时，用当前 `session_id` 调用服务更新角色。

### 会话 (`session_id`)

保存服务返回的 `session_id`，后续追问必须回传它以保持上下文。`conversation_id` 仅用于排查和平台追踪；请求体不依赖它。

## 请求

### 首次查询游戏数据

```json
{
  "question": "统计近 30 天每天的 DAU 与玩家留存率",
  "role": "运营经理",
  "domain": "game"
}
```

### 首次查询电商数据

```json
{
  "question": "商品状态分布与销售额情况",
  "role": "运营经理",
  "domain": "ecommerce"
}
```

### 后续追问

```json
{
  "question": "已发货商品有多少件？",
  "role": "运营经理",
  "domain": "ecommerce",
  "session_id": "上次返回的session_id"
}
```

### 切换角色

```json
{
  "role": "一线客服",
  "domain": "game",
  "session_id": "上次返回的session_id"
}
```

## 响应

标准成功响应：

```json
{
  "summary": "业务决策洞察文本段落...",
  "sql": "SELECT ...",
  "fields": ["col1", "col2"],
  "rows": [{"col1": "val1", "col2": "val2"}],
  "chart": true,
  "html_url": null,
  "session_id": "您的当前会话ID_session_id",
  "conversation_id": "projects/...",
  "role": "运营经理"
}
```

## 结果展示与交互规范

### 数据优先

- 优先使用 `fields` 和 `rows` 生成 Markdown 表格与直接结论。
- `summary` 只用于提取可验证的业务解释和决策洞察，不要照搬执行过程、SQL 运行日志或推荐追问。
- 所有占比、环比、差异等补充计算必须基于 `rows`，不要编造原因或趋势。
- 简单事实查询保持简洁；分布、对比、趋势类查询补充占比或变化。
- 用户要求审计、口径复杂或需要复核时，再展示 SQL。

### 图表

- 如果响应包含 `html_url`，以可点击链接提供。
- 如果响应包含 `vega_config`，优先用它生成或展示图表。
- 如果用户明确要求柱状图、环形图、折线图等，但 `html_url` 为空，可基于 `rows` 生成本地 HTML/SVG/图片；同时必须保留数据表和统计口径。
- 图表数据不得脱离 `rows` 二次发挥。

### 权限提示

当 `role` 为 `一线客服` 时，回答开头和结尾都要提示当前是受限权限视图，并遵守防截单风控口径。`运营经理` 无需额外权限提示。

### 异常处理

- DNS、超时或网络失败：说明 BQCA 服务暂时不可达，可稍后重试。
- 401、403 或权限错误：提示当前角色权限不足或服务认证异常，不要尝试补充 API Key。
- 空结果：说明没有匹配数据，不要编造结果。
- 非 JSON：说明接口返回异常，并只展示可用错误信息。
- `rows` 过多：展示前 20-50 行并说明已截断，可继续分页、筛选或导出。
