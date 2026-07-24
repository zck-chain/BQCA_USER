---
name: bqca-select-sql
description: 通过自然语言向 BQCA（BigQuery Conversational Analytics）Agent 提问，获取 SQL 查询结果。适用于需要用中文/英文自然语言查询 BigQuery 电商数据、获取结构化数据结果的场景。支持多 API Key 权限隔离，不同 Key 对应不同 Service Account，自动实现行级数据访问控制。
---

# BQCA Select SQL

## 使用场景

- 用户用自然语言提问，需要从 BigQuery 电商数据集中获取数据
- 需要将自然语言转化为 SQL 并执行查询
- 需要根据不同 API Key 实现数据权限隔离（行级 RAP）
- 需要获取查询结果的表格、图表、SQL 等结构化信息
- 不适用于：直接执行已知 SQL（应走 BigQuery SDK）、需要写操作的场景

## 核心流程

1. 接收自然语言问题和 API Key
2. 根据 API Key 确定目标 Service Account（权限隔离）
3. 调用 BQCA Conversational Analytics API，创建或复用会话
4. 解析 BQCA 返回的流式响应，提取摘要、SQL、数据行、图表配置
5. 返回结构化结果（ChatResult）

## 输入

| 参数 | 必填 | 说明 |
|------|------|------|
| question | 是 | 自然语言问题，如"查看订单数量前5的商品类别" |
| api_key | 否 | API Key，决定使用哪个 Service Account。未提供则使用默认身份 |
| conversation_id | 否 | 会话 ID，传入则延续多轮对话，不传则创建新会话 |

## 输出形式

返回 ChatResult 对象，包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| summary | str | BQCA 生成的自然语言摘要 |
| sql | str | BQCA 生成的 SQL 语句 |
| fields | list[str] | 结果列名列表 |
| rows | list[dict] | 查询结果数据行 |
| vega_config | dict or None | 图表配置（Vega-Lite），可用于渲染图表 |
| conversation_id | str | 会话资源名，用于多轮对话 |

可选：生成 HTML 结果页并上传 GCS，返回可访问的 URL。

## 权限隔离机制

通过 API Key → Service Account 映射实现：

- Admin Key → 使用 Cloud Run 默认身份（bqca-runner），拥有全量数据权限
- Restricted Key → impersonate 对应 SA（如 bqca-restricted），受 BigQuery RAP 行级权限约束
- 新增受限 Key 只需：在 KEY_TO_SA 映射中注册 + 给 SA 配置对应 BQ 权限

详见 `references/permissions.md`

## 依赖

- Python 3.11+
- google-cloud-geminidataanalytics
- google-auth（含 impersonated_credentials）
- Google Cloud 项目已启用 Conversational Analytics API
- BQCA Agent 已创建并关联数据集

## 参考文件

- `references/permissions.md`：Service Account 权限配置完整指南
- `references/bqca-api.md`：Conversational Analytics API 调用说明
