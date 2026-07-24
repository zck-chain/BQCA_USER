# BQCA 权限配置指南

## 权限四层模型

一个 Service Account 要能通过 BQCA 查询数据，需要四层权限：

| 层级 | 权限 | 作用 | 授予方式 |
|------|------|------|----------|
| 1. 项目级 | `cloudaicompanion.user` | 调用 CA API 服务 | gcloud 项目级 IAM |
| 2. Agent 级 | `geminidataanalytics.dataAgentUser` | 与指定 BQCA Agent 对话 | REST API Agent 级 IAM |
| 3. BigQuery | `bigquery.jobUser` + 数据集/行级权限 | 执行查询 + 读取数据 | gcloud / bq / SQL |
| 4. Impersonation | `iam.serviceAccountTokenCreator` | Cloud Run 冒充该 SA | gcloud SA 级 IAM |

## 快速配置新 SA

```bash
SA_NAME="your-sa"
SA_EMAIL="${SA_NAME}@webeye-internal-test.iam.gserviceaccount.com"

# 1. 创建 SA
gcloud iam service-accounts create $SA_NAME --display-name="$SA_NAME" --project=webeye-internal-test

# 2. CA API 权限
gcloud projects add-iam-policy-binding webeye-internal-test \
  --member="serviceAccount:$SA_EMAIL" --role="roles/cloudaicompanion.user" --condition=None

# 3. Agent 对话权限（REST API，见下方说明）
# 4. BigQuery 运行查询权限
gcloud projects add-iam-policy-binding webeye-internal-test \
  --member="serviceAccount:$SA_EMAIL" --role="roles/bigquery.jobUser" --condition=None

# 5. 数据集读权限（每个 BQCA Agent 关联的数据集都要加）
# 6. Impersonation 权限
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --member="serviceAccount:bqca-runner@webeye-internal-test.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

## 行级权限（RAP）关键规则

BQCA 内部执行 SQL 的身份取决于 API Key 模式：

| 模式 | BQCA 内部执行身份 | RAP 配置要点 |
|------|-------------------|-------------|
| 默认（无 impersonation） | bqca-runner | admin_all_rows 的 grantee 必须包含 bqca-runner |
| Impersonation | impersonated SA | RAP 的 grantee 写目标 SA 即可 |

### 正确的 RAP 配置

```sql
-- 受限用户
CREATE OR REPLACE ROW ACCESS POLICY restricted_policy
ON `project.dataset.table`
GRANT TO ("serviceAccount:restricted-sa@project.iam.gserviceaccount.com")
FILTER USING (your_filter_condition);

-- 管理员（必须包含 bqca-runner）
CREATE OR REPLACE ROW ACCESS POLICY admin_all_rows
ON `project.dataset.table`
GRANT TO (
  "user:admin@example.com",
  "serviceAccount:bqca-runner@project.iam.gserviceaccount.com"
)
FILTER USING (TRUE);
```

### 注意事项

- 表上一旦有 RAP，不在任何 grantee 里的用户一行都看不到
- RAP 只支持实体表，视图（VIEW）上不能创建 RAP
- US/EU 多区域数据集不支持 Policy Tag（列级权限）
- bigquery.admin 角色自动绕过 RAP

## Agent 级权限设置

gcloud 不支持直接设 Agent 级 IAM，必须用 REST API：

```bash
ACCESS_TOKEN=$(gcloud auth print-access-token)

# 获取当前策略
curl -s -X POST \
  "https://geminidataanalytics.googleapis.com/v1alpha/projects/PROJECT/locations/global/dataAgents/AGENT_ID:getIamPolicy" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d '{}'

# 设置策略（必须带上所有已有 binding）
curl -s -X POST \
  "https://geminidataanalytics.googleapis.com/v1alpha/projects/PROJECT/locations/global/dataAgents/AGENT_ID:setIamPolicy" \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"policy":{"bindings":[...]}}'
```
