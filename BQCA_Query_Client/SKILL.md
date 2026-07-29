---
name: bqca-query-client
description: BQCA 全领域智能数据查询技能。适用于一线客服、运营经理、数据分析师统一查询【电商业务】（订单、商品、销售额、退货）与【游戏业务】（玩家、DAU/MAU、留存率、付费渗透、关卡）数据。
---

# BQCA 全领域智能数据查询

调用接口：`POST` 请求。

### 服务端接口地址：
* **生产环境 (Production)**：`https://bqca-bot-839062387451.asia-east1.run.app/api/query`

服务端根据 `domain` 参数自动选择对应的 BQCA Agent（电商 `ecommerce-analyst-cn` 与游戏 `game-analyst-cn`），绝不在请求、回答或本技能文件中包含 API Key 或敏感凭证。

## 智能领域路由与角色识别

### 1. 业务领域路由 (`domain`)
根据用户问题的业务场景，自动推断并分配 `"domain"` 参数：
* **游戏业务 (`"domain": "game"`)**：当用户提问包含游戏指标（如 DAU/MAU、玩家留存率、付费渗透率、ARPU/ARPPU、关卡通关率、Flood-It、道具消耗等）时选用。
* **电商业务 (`"domain": "ecommerce"`)**：当用户提问包含电商指标（如 订单数、销售额、商品 SKU、退货率、发货状态等）时选用。

### 2. 角色与会话 (`role`)
- **支持角色**：`运营经理`、`一线客服`。
- **主动识别角色**：经理、管理员、高管、游戏策划对应 `运营经理`；客服、物流、玩家支持对应 `一线客服`。
- **首次问答**：首次出现业务问题但未说明角色时，默认开启数据查询，也可主动询问；用户说“切换为客服/经理”时，调用服务更新角色。
- **保存会话**：保存服务返回的 `session_id`，并在之后每次请求中携带它。

## 请求

### 1. 首次查询游戏数据 (带 `"domain": "game"`):
```json
{
  "question": "统计近 30 天每天的 DAU 与玩家留存率",
  "role": "运营经理",
  "domain": "game"
}
```

### 2. 首次查询电商数据 (带 `"domain": "ecommerce"`):
```json
{
  "question": "商品状态分布与销售额情况",
  "role": "运营经理",
  "domain": "ecommerce"
}
```

### 3. 后续追问 (携带 `session_id` 保持上下文):
```json
{
  "question": "已发货商品有多少件？",
  "role": "运营经理",
  "domain": "ecommerce",
  "session_id": "上次返回的session_id"
}
```

### 4. 切换角色:
```json
{
  "role": "一线客服",
  "domain": "game",
  "session_id": "上次返回的session_id"
}
```

## 响应

### 标准数据查询成功响应：
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

- **优先用数据呈现**：优先用 `fields` 和 `rows` 生成直观的数据表格，并搭配最底部的核心直接结论进行回答。
- **呈现决策洞察**：展示 `summary` 中的【业务决策洞察】。
- **客服合规提示**：当角色为 `一线客服` 时，回答首尾显著提示防截单风控与受限权限视图。
