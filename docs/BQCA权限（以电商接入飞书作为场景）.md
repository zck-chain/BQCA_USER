# BQCA 飞书机器人 — 服务账号权限配置指南

本文档梳理新增服务账号（Service Account）时需要授予的完整权限链，以及实测中遇到的问题和解决方案。

## 权限总览

一个 SA 要能通过本项目查询数据，需要**四层权限**缺一不可：

| 层级 | 权限 | 作用 | 授予方式 |
|------|------|------|----------|
| 1. 项目级 | `cloudaicompanion.user` | 调用 Conversational Analytics API 服务 | gcloud 项目级 IAM |
| 2. Agent 级 | `geminidataanalytics.dataAgentUser` | 与指定 BQCA Agent 对话 | REST API Agent 级 IAM |
| 3. BigQuery | `bigquery.jobUser` + 数据集/表/列/行级权限 | 执行查询 + 读取数据 | gcloud / bq / SQL |
| 4. Impersonation | `iam.serviceAccountTokenCreator` | Cloud Run 冒充该 SA | gcloud SA 级 IAM |

## 详细步骤

### 第 1 步：创建 SA（如未创建）

```bash
gcloud iam service-accounts create SA_NAME \
  --display-name="SA 描述" \
  --project=webeye-internal-test
```

### 第 2 步：项目级 — CA API 访问权限

```bash
gcloud projects add-iam-policy-binding webeye-internal-test \
  --member="serviceAccount:SA_NAME@webeye-internal-test.iam.gserviceaccount.com" \
  --role="roles/cloudaicompanion.user" \
  --condition=None
```

> 作用：让 SA 能调用 Conversational Analytics API 服务本身（创建会话、发送消息等）。
> 缺失表现：`403 Permission 'cloudaicompanion.topics.create' denied`
> **踩坑**：项目 IAM 有 conditional bindings 时，gcloud 要求指定 `--condition=None`，否则报错。

### 第 3 步：Agent 级 — BQCA Agent 对话权限

```bash
# 获取当前 Agent IAM policy
ACCESS_TOKEN=$(gcloud auth print-access-token)

curl -s -X POST \
  "https://geminidataanalytics.googleapis.com/v1alpha/projects/webeye-internal-test/locations/global/dataAgents/ecommerce-analyst-cn:getIamPolicy" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# 把新 SA 加入 dataAgentUser binding，然后 setIamPolicy
# ⚠️ 必须带上所有已有的 binding，否则会覆盖掉其他 SA 的权限！
curl -s -X POST \
  "https://geminidataanalytics.googleapis.com/v1alpha/projects/webeye-internal-test/locations/global/dataAgents/ecommerce-analyst-cn:setIamPolicy" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "bindings": [
        {
          "role": "roles/geminidataanalytics.dataAgentOwner",
          "members": ["user:chengkang.zhao@webeye.com"]
        },
        {
          "role": "roles/geminidataanalytics.dataAgentUser",
          "members": [
            "serviceAccount:bqca-runner@webeye-internal-test.iam.gserviceaccount.com",
            "serviceAccount:bqca-restricted@webeye-internal-test.iam.gserviceaccount.com",
            "serviceAccount:SA_NAME@webeye-internal-test.iam.gserviceaccount.com"
          ]
        }
      ]
    }
  }'
```

> 作用：让 SA 能与指定的 BQCA Agent（ecommerce-analyst-cn）对话。
> 缺失表现：`403 User does not have permission to chat.`
> **踩坑1**：此权限不在项目级，gcloud 不支持 `roles/cloudaicompanion.agentUser` 设在项目级（报 `Role not supported for this resource`），必须通过 REST API 设在 Agent 资源上。
> **踩坑2**：setIamPolicy 时必须带上所有已有的 binding，否则会覆盖掉其他 SA 的权限。

### 第 4 步：BigQuery — 运行查询权限

不管数据访问粒度如何，都必须先有这个：

```bash
gcloud projects add-iam-policy-binding webeye-internal-test \
  --member="serviceAccount:SA_NAME@webeye-internal-test.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser" \
  --condition=None
```

> 没有 jobUser 会报：`Access Denied: User does not have bigquery.jobs.create permission`

### 第 5 步：BigQuery — 数据访问权限

根据需要的粒度选择。**重要：BQCA Agent 可能关联多个数据集，必须都给权限，否则查到无权限的表就直接报错中断。**

查看 Agent 关联了哪些数据集：
```bash
ACCESS_TOKEN=$(gcloud auth print-access-token)
curl -s -X GET \
  "https://geminidataanalytics.googleapis.com/v1alpha/projects/webeye-internal-test/locations/global/dataAgents/ecommerce-analyst-cn" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -c "
import sys, json
d = json.load(sys.stdin)
refs = d.get('dataAnalyticsAgent',{}).get('stagingContext',{}).get('datasourceReferences',{}).get('bq',{}).get('tableReferences',[])
datasets = set()
for r in refs:
    datasets.add(f\"{r['projectId']}.{r['datasetId']}\")
for ds in sorted(datasets):
    print(ds)
"
```

当前 Agent 关联的数据集：`thelook_bq`、`firebas_bq`、`workspace_test_demo_001`。

#### 5a. 数据集级只读（推荐）

```bash
# bq add-iam-policy-binding 需要 allowlist，用 bq show/update 替代
bq show --format=prettyjson webeye-internal-test:DATASET_NAME > /tmp/ds_policy.json

python3 -c "
import json
with open('/tmp/ds_policy.json') as f:
    data = json.load(f)
data['access'].append({
    'role': 'READER',
    'userByEmail': 'SA_NAME@webeye-internal-test.iam.gserviceaccount.com'
})
with open('/tmp/ds_policy.json', 'w') as f:
    json.dump(data, f, indent=2)
"

bq update --source /tmp/ds_policy.json webeye-internal-test:DATASET_NAME
```

> **踩坑**：`bq add-iam-policy-binding` 报 `This feature requires allowlisting`，必须用上面的导出-修改-写回方式。

#### 5b. 行级权限（Row Access Policy）

```sql
CREATE OR REPLACE ROW ACCESS POLICY policy_name
ON `project.dataset.table`
GRANT TO ("serviceAccount:SA_NAME@webeye-internal-test.iam.gserviceaccount.com")
FILTER USING (column_name = "allowed_value");
```

**关键规则：表上一旦有任何 RAP，不在任何 RAP grantee 里的用户一行都看不到！** 必须给所有需要全量访问的用户也加 RAP：

```sql
-- ⚠️ 重要：BQCA 行级权限的 grantee 必须包含实际执行 SQL 的身份，详见下方"BQCA 行级权限机制"章节
```

#### BQCA 行级权限机制（实测结论）

BQCA API 执行 SQL 时使用的身份**不是调用者本身**，而是由 BQCA 内部决定。实测发现两种模式：

| 模式 | API Key | BQCA 内部执行 SQL 身份 | RAP 效果 |
|------|---------|------------------------|----------|
| 默认（无 impersonation） | admin key（settings.API_KEY） | `bqca-runner@...`（Cloud Run 运行身份） | 受 RAP 限制，需要在 admin RAP 的 grantee 里加上 `bqca-runner` |
| Impersonation | restricted key（KEY_TO_SA） | impersonated SA（如 `bqca-restricted@...`） | 受 RAP 限制，`bqca-restricted` 只能看到 grant 给它的行 |

**验证过程**：

1. 在 `thelook_bq.orders` 上创建两个 RAP：
   - `shipped_only`：grant 给 `bqca-restricted`，过滤 `status = 'Shipped'`
   - `admin_all_rows`：grant 给 `user:chengkang.zhao@webeye.com`，`FILTER USING (TRUE)`

2. Admin key 通过 BQCA API 查询 orders → **0 行**！
   - 原因：BQCA 内部用 `bqca-runner` 执行 SQL，`bqca-runner` 不在 `admin_all_rows` 的 grantee 里
   - 通过 `INFORMATION_SCHEMA.JOBS_BY_PROJECT` 确认：所有 BQCA 执行的 SQL `user_email` 都是 `bqca-runner@...`

3. 把 `bqca-runner` 加到 `admin_all_rows` 的 grantee 后：
   - Admin key → **5 种状态，全部数据**（Shipped 37500, Complete 31109, Processing 24813, Cancelled 18571, Returned 12640）
   - Restricted key → **1 种状态**（Shipped 37500）
   - 行级权限生效！

**正确的 RAP 配置方式**：

```sql
-- 受限用户：只能看 Shipped
CREATE OR REPLACE ROW ACCESS POLICY shipped_only
ON `project.dataset.table`
GRANT TO ("serviceAccount:bqca-restricted@webeye-internal-test.iam.gserviceaccount.com")
FILTER USING (status = 'Shipped');

-- 管理员：全量访问（必须包含 bqca-runner，因为 admin key 不走 impersonation，BQCA 用 bqca-runner 执行 SQL）
CREATE OR REPLACE ROW ACCESS POLICY admin_all_rows
ON `project.dataset.table`
GRANT TO (
  "user:chengkang.zhao@webeye.com",
  "serviceAccount:bqca-runner@webeye-internal-test.iam.gserviceaccount.com"
)
FILTER USING (TRUE);
```

**核心结论**：
- Admin key 不走 impersonation → BQCA 用 `bqca-runner` 执行 SQL → `admin_all_rows` 的 grantee 必须包含 `bqca-runner`
- Restricted key 走 impersonation → BQCA 用 impersonated SA 执行 SQL → RAP 的 grantee 写那个 SA 即可
- 如果新增一个受限 SA，只需在 RAP 的 grantee 里加上它，并在代码的 `KEY_TO_SA` 里注册映射

**实测对比结果**（thelook_bq.orders & order_items）：

| API Key | 身份 | orders 状态数 | order_items 状态数 | 行级权限 |
|---------|------|--------------|-------------------|----------|
| ${BQCA_MANAGER_API_KEY} | bqca-runner（default） | 5 种（全量） | 5 种（全量） | admin_all_rows 生效 |
| ${BQCA_SUPPORT_API_KEY} | bqca-restricted（impersonated） | 1 种（Shipped only） | 1 种（Shipped only） | shipped_only 生效 |

```sql
-- 给需要全量访问的用户加全量行权限
CREATE OR REPLACE ROW ACCESS POLICY admin_all_rows
ON `project.dataset.table`
GRANT TO ("user:ADMIN_EMAIL")
FILTER USING (TRUE);
```

> `bigquery.admin` 角色自动绕过 RAP，不需要额外加。
>
> **踩坑1**：RAP 只支持实体表，**视图（VIEW）上不能创建 RAP**。报错：`Row access policies are only supported on BigQuery tables`。如果 BQCA 查的是视图，需要先转成实体表。
>
> **踩坑2**：加 RAP 后忘了给自己加白名单，导致自己查不到任何数据。务必给所有需要访问的人加 RAP（或依赖 bigquery.admin 绕过）。

#### 5c. 列级权限（Policy Tag）

```bash
# 1. 创建分类体系（位置必须和数据集同区域！）
ACCESS_TOKEN=$(gcloud auth print-access-token)
curl -s -X POST \
  "https://datacatalog.googleapis.com/v1/projects/webeye-internal-test/locations/REGION/taxonomies" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"displayName": "Sensitive Data", "description": "Column-level access control"}'

# 2. 创建 Policy tag
curl -s -X POST \
  "https://datacatalog.googleapis.com/v1/projects/webeye-internal-test/locations/REGION/taxonomies/TAXONOMY_ID/policyTags" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"displayName": "High Sensitivity"}'

# 3. 只给有权限的 SA 加 Fine-Grained Reader（没加的看不到该列）
curl -s -X POST \
  "https://datacatalog.googleapis.com/v1/projects/webeye-internal-test/locations/REGION/taxonomies/TAXONOMY_ID/policyTags/POLICY_TAG_ID:setIamPolicy" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"policy":{"bindings":[{"role":"roles/datacatalog.categoryFineGrainedReader","members":["serviceAccount:SA_NAME@webeye-internal-test.iam.gserviceaccount.com"]}]}}'

# 4. 激活强制访问控制
curl -s -X PATCH \
  "https://datacatalog.googleapis.com/v1/projects/webeye-internal-test/locations/REGION/taxonomies/TAXONOMY_ID?updateMask=activatedPolicyTypes" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"activatedPolicyTypes":["FINE_GRAINED_ACCESS_CONTROL"]}'

# 5. 给列绑定 Policy tag
bq show --schema --format=json PROJECT:DATASET.TABLE > /tmp/schema.json
# 编辑 JSON，给目标列加 "policyTags": {"names": ["projects/.../policyTags/POLICY_TAG_ID"]}
bq update --schema /tmp/schema.json PROJECT:DATASET.TABLE
```

> **踩坑1**：Taxonomy 的 location 必须和数据集 location 一致。数据集在 `us-central1` 就用 `us-central1`，在 `US`（多区域）则 Data Catalog 不支持（报 `UNIMPLEMENTED`）。目前 `thelook_ecommerce` 在 us-central1 可以用，`thelook_bq` 在 US 不支持 Policy Tag。
>
> **踩坑2**：`gcloud data-catalog taxonomies create` 子命令不存在，必须用 REST API 创建 taxonomy 和 policy tag。

### 第 6 步：Impersonation 权限（Cloud Run 需要）

本项目 Cloud Run 运行身份是 `bqca-runner`，使用非默认 SA 的 API Key 时需要 impersonate。admin key（bqca-runner 本身）不需要 impersonate。

```bash
gcloud iam service-accounts add-iam-policy-binding \
  SA_NAME@webeye-internal-test.iam.gserviceaccount.com \
  --member="serviceAccount:bqca-runner@webeye-internal-test.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

> 缺失表现：`403 Permission 'iam.serviceAccounts.getAccessToken' denied`
> **踩坑**：不要把 admin key（bqca-runner 本身）也放进 `KEY_TO_SA`，Cloud Run 以 bqca-runner 运行，自己 impersonate 自己会报 403。admin key 通过 `settings.API_KEY` 环境变量验证，不走 impersonation。

### 第 7 步：注册 API Key

在代码 `app/bqca/client.py` 的 `KEY_TO_SA` 字典中添加映射（只放需要 impersonate 的 SA）：

```python
KEY_TO_SA: dict[str, str] = {
    # admin key 不在这里，通过 settings.API_KEY 验证
    "${BQCA_SUPPORT_API_KEY}": "SA_NAME@webeye-internal-test.iam.gserviceaccount.com",
}
```

部署时设置 `API_KEY` 环境变量为 admin key：
```bash
gcloud run deploy bqca-bot ... --set-env-vars="API_KEY=${BQCA_MANAGER_API_KEY},..."
```

## 当前已配置的 SA

| SA | API Key | 权限范围 | 角色 |
|----|---------|----------|------|
| bqca-runner | ${BQCA_MANAGER_API_KEY} | 全部表全字段（bigquery.admin，无 impersonation） | admin |
| bqca-restricted | ${BQCA_SUPPORT_API_KEY} | 数据集级只读 + orders/order_items 行级（仅 Shipped），列级暂未生效（US 多区域不支持 Policy Tag） | restricted |

### bqca-runner 权限明细

**项目级角色**：
- `roles/bigquery.admin` — BigQuery 完全管理权限（绕过所有 RAP）
- `roles/cloudaicompanion.user` — 调用 CA API
- `roles/geminidataanalytics.admin` — 管理 BQCA Agent
- `roles/aiplatform.user` — AI Platform 使用
- `roles/dialogflow.admin` — Dialogflow 管理
- `roles/discoveryengine.admin` — Discovery Engine 管理
- `roles/storage.objectAdmin` — GCS 对象管理

**数据集级权限**：
- `thelook_bq`：通过 `bigquery.admin` 继承全量访问
- `workspace_test_demo_001`：OWNER

**Agent 级权限**：
- `ecommerce-analyst-cn`：`dataAgentUser`

**行级权限（RAP）**：
- `thelook_bq.orders` → `admin_all_rows`（grantee，FILTER USING TRUE）
- `thelook_bq.order_items` → `admin_all_rows`（grantee，FILTER USING TRUE）

**Impersonation**：无（admin key 走默认身份，不需要 impersonate）

---

### bqca-restricted 权限明细

**项目级角色**：
- `roles/bigquery.jobUser` — 可创建 BigQuery 查询作业
- `roles/cloudaicompanion.user` — 调用 CA API

**数据集级权限**：
- `thelook_bq`：READER
- `workspace_test_demo_001`：READER
- `firebas_bq`：通过项目级 `projectReaders` 继承 READER

**Agent 级权限**：
- `ecommerce-analyst-cn`：`dataAgentUser`

**行级权限（RAP）**：
- `thelook_bq.orders` → `shipped_only`（grantee，FILTER USING status = 'Shipped'）
- `thelook_bq.order_items` → `shipped_only`（grantee，FILTER USING status = 'Shipped'）

**Impersonation**：
- `bqca-runner` 可 impersonate `bqca-restricted`（`serviceAccountTokenCreator`）
- `chengkang.zhao@webeye.com` 可 impersonate `bqca-restricted`（`serviceAccountTokenCreator`）

**列级权限**：暂未生效（`thelook_bq` 在 US 多区域，不支持 Data Catalog Policy Tag）

## 实测问题与解决方案汇总

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 1 | `500 AttributeError: 'FastAPI' object has no attribute 'bqca'` | 代码里写了 `app.bqca.client.KEY_TO_SA`，`app` 是 FastAPI 实例不是 Python 模块 | 改为 `from app.bqca.client import KEY_TO_SA` 直接引用 |
| 2 | `403 Permission 'iam.serviceAccounts.getAccessToken' denied` | admin key 放在 `KEY_TO_SA` 里映射到 `bqca-runner`，Cloud Run 自己 impersonate 自己 | admin key 移出 `KEY_TO_SA`，改用 `settings.API_KEY` 环境变量验证；`KEY_TO_SA` 只放需要 impersonate 的 SA |
| 3 | `403 Permission 'cloudaicompanion.topics.create' denied` | SA 没有项目级 `cloudaicompanion.user` 角色 | `gcloud projects add-iam-policy-binding ... --role=roles/cloudaicompanion.user --condition=None` |
| 4 | `403 User does not have permission to chat` | SA 没有 Agent 级 `dataAgentUser` 角色 | 通过 REST API `setIamPolicy` 加到 Agent 资源上，gcloud 不支持 |
| 5 | `gcloud` 报 `Role not supported for this resource` | `agentUser` 角色不能设在项目级，只能设在 Agent 资源级 | 用 REST API 设在 Agent 上（见第 3 步） |
| 6 | `gcloud` 报 `specifying a condition is required` | 项目 IAM 有 conditional bindings | 所有 `add-iam-policy-binding` 加 `--condition=None` |
| 7 | `bq add-iam-policy-binding` 报 `This feature requires allowlisting` | 此功能需要 Google allowlist | 用 `bq show` → 修改 JSON → `bq update --source` 替代 |
| 8 | SA 能调 CA API 但查数据报 `403 Access Denied: Table xxx` | SA 没有该数据集的读权限 | 给 BQCA Agent 关联的所有数据集都加 READER 权限 |
| 9 | BQCA 查询报错但不是目标表的权限问题 | BQCA Agent 关联了多个数据集，restricted 没有非目标数据集的权限 | **必须给 BQCA Agent 关联的所有数据集都加读权限** |
| 10 | RAP 创建成功但行级权限不生效 | BQCA 查的是视图（指向 bigquery-public-data），RAP 设在了实体表上 | 把视图转成实体表：`bq rm -f -t` → `bq query --replace --destination_table=... SELECT * FROM source` |
| 11 | `Row access policies are only supported on BigQuery tables` | 视图（VIEW）不支持 RAP | 先把视图转成实体表，再创建 RAP |
| 12 | 加了 RAP 后自己查不到数据了 | RAP 规则：表上一旦有 RAP，不在任何 grantee 里的用户一行都看不到 | 给所有需要全量访问的用户也加 `FILTER USING (TRUE)` 的 RAP；`bigquery.admin` 角色自动绕过 RAP |
| 13 | Policy Tag 报 `does not belong to the allowed regions` | Taxonomy 的 location 和数据集的 location 不匹配 | Taxonomy 必须和数据集在同一区域；US 多区域不支持 Data Catalog Taxonomy |
| 14 | `gcloud data-catalog taxonomies create` 报 `Invalid choice: 'create'` | gcloud CLI 没有此子命令 | 用 REST API 创建 taxonomy 和 policy tag |
| 15 | BQCA API 查询有 RAP 的表返回 0 行（admin key 也不行） | BQCA 内部用 `bqca-runner` 执行 SQL，`bqca-runner` 不在 RAP 的 grantee 里 | `admin_all_rows` RAP 的 grantee 必须包含 `serviceAccount:bqca-runner@...`；restricted key 走 impersonation 则无需加 |
| 16 | US 多区域数据集列级权限（Policy Tag）不生效 | Data Catalog Taxonomy 只支持单区域，US/EU 多区域不支持 | 列级权限需要在应用层做过滤，或把数据集迁移到单区域（如 `us-central1`） |

## 权限验证测试

### 表级权限验证

验证不同 key 能否访问不同数据集中的表：

**1. thelook_bq 数据集（bqca-runner: admin 全量, bqca-restricted: READER）：**

```bash
# Admin key — 可查任意表
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_MANAGER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "thelook_bq里有哪些表，每张表多少条记录"}'

# Restricted key — 可查（有 READER 权限）
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_SUPPORT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "thelook_bq里有哪些表，每张表多少条记录"}'
```

**2. workspace_test_demo_001 数据集（bqca-runner: OWNER, bqca-restricted: READER）：**

```bash
# Admin key
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_MANAGER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "workspace_test_demo_001数据集里有什么数据"}'

# Restricted key
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_SUPPORT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "workspace_test_demo_001数据集里有什么数据"}'
```

**3. 无权限数据集测试（如果新建一个 SA 但没给某个数据集的 READER）：**

```bash
# 预期报错：403 Access Denied: Table xxx Permission denied
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: 缺少权限的KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "查看某数据集的数据"}'
```

**表级权限预期对比**：

| 数据集 | bqca-runner (admin) | bqca-restricted (restricted) |
|--------|---------------------|------|
| thelook_bq | 全量读写（admin） | 只读（READER） |
| firebas_bq | 全量读写（admin） | 只读（projectReaders 继承） |
| workspace_test_demo_001 | OWNER | READER |

### 行级权限验证

验证 RAP 是否按 key 过滤行数据（同一个问题，两个 key 结果不同）：

**1. orders 表行级权限（shipped_only RAP：status = 'Shipped'）：**

```bash
# Admin key — 预期：5 种状态（Shipped 37500, Complete 31109, Processing 24813, Cancelled 18571, Returned 12640）
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_MANAGER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "查看所有订单的状态分布"}'

# Restricted key — 预期：仅 Shipped（37500）
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_SUPPORT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "查看所有订单的状态分布"}'
```

**2. order_items 表行级权限（shipped_only RAP：status = 'Shipped'）：**

```bash
# Admin key — 预期：5 种状态
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_MANAGER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "订单明细中每个状态有多少条"}'

# Restricted key — 预期：仅 Shipped
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_SUPPORT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "订单明细中每个状态有多少条"}'
```

**3. 跨表关联查询行级权限（orders + order_items JOIN）：**

```bash
# Admin key — 预期：所有状态完整关联
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_MANAGER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "每个订单状态对应的订单明细总金额"}'

# Restricted key — 预期：仅 Shipped 状态的关联结果
curl -s -X POST https://bqca-bot-839062387451.asia-east1.run.app/api/query \
  -H "X-API-Key: ${BQCA_SUPPORT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"question": "每个订单状态对应的订单明细总金额"}'
```

**行级权限预期对比**：

| 维度 | Admin key | Restricted key |
|------|-----------|----------------|
| orders 状态种类 | 5 种 | 1 种（Shipped only） |
| order_items 状态种类 | 5 种 | 1 种（Shipped only） |
| 跨表 JOIN 结果 | 全量关联 | 仅 Shipped 关联 |
| RAP 策略 | admin_all_rows（TRUE） | shipped_only（status = 'Shipped'） |

## 快速配置清单（新增 SA 时按序执行）

```bash
# 变量替换
SA_NAME="your-sa-name"
SA_EMAIL="${SA_NAME}@webeye-internal-test.iam.gserviceaccount.com"

# 1. 创建 SA
gcloud iam service-accounts create $SA_NAME --display-name="$SA_NAME" --project=webeye-internal-test

# 2. CA API 权限
gcloud projects add-iam-policy-binding webeye-internal-test --member="serviceAccount:$SA_EMAIL" --role="roles/cloudaicompanion.user" --condition=None

# 3. Agent 对话权限（REST API，需手动编辑 members 列表）
#    → 参考第 3 步的 curl 命令

# 4. BigQuery 运行查询权限
gcloud projects add-iam-policy-binding webeye-internal-test --member="serviceAccount:$SA_EMAIL" --role="roles/bigquery.jobUser" --condition=None

# 5. 数据集读权限（每个 BQCA Agent 关联的数据集都要加）
#    → 参考第 5a 步的 bq show → 修改 → bq update 方法

# 6. Impersonation 权限
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL --member="serviceAccount:bqca-runner@webeye-internal-test.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator"

# 7. 代码注册 API Key（app/bqca/client.py 的 KEY_TO_SA）+ 重新部署
```
