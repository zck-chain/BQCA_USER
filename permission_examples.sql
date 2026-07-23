-- ============================================================
-- BigQuery 权限控制测试脚本
-- 项目: webeye-internal-test
-- 数据集: thelook_ecommerce
-- ============================================================

-- ============================================================
-- 0. 准备：创建测试用数据集和测试用户
-- ============================================================

-- 创建测试数据集（如果还没有）
CREATE SCHEMA IF NOT EXISTS `webeye-internal-test.thelook_ecommerce_test`
OPTIONS(location="US");

-- 复制一张小表到测试数据集，用于独立测试不影响原始数据
CREATE OR REPLACE TABLE `webeye-internal-test.thelook_ecommerce_test.users` AS
SELECT * FROM `webeye-internal-test.thelook_ecommerce.users`;

CREATE OR REPLACE TABLE `webeye-internal-test.thelook_ecommerce_test.orders` AS
SELECT * FROM `webeye-internal-test.thelook_ecommerce.orders`;


-- ============================================================
-- 1. 项目级 IAM — 通过 gcloud 命令操作
-- ============================================================
-- 项目级权限无法用 SQL 操作，需要用 gcloud 或 Console
-- 以下命令在终端执行（不是 BigQuery SQL）
--
-- 给用户授予项目级 BigQuery Data Viewer 角色：
--   gcloud projects add-iam-policy-binding webeye-internal-test \
--     --member="user:someone@example.com" \
--     --role="roles/bigquery.dataViewer"
--
-- 效果：该用户可以读取项目中所有数据集的所有表
-- 撤销：
--   gcloud projects remove-iam-policy-binding webeye-internal-test \
--     --member="user:someone@example.com" \
--     --role="roles/bigquery.dataViewer"
--
-- ============================================================


-- ============================================================
-- 2. 数据集级 IAM — 用 SQL DCL 语句
-- ============================================================

-- 授予某用户对数据集的只读权限
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `webeye-internal-test.thelook_ecommerce_test`
TO "user:someone@example.com";

-- 验证：该用户可以查询数据集中的表
-- SELECT * FROM `webeye-internal-test.thelook_ecommerce_test.users` LIMIT 10;

-- 撤销数据集级权限
REVOKE `roles/bigquery.dataViewer`
ON SCHEMA `webeye-internal-test.thelook_ecommerce_test`
FROM "user:someone@example.com";

-- 也可以用 gcloud 操作：
-- 先导出策略：
--   bq show --format=prettyjson webeye-internal-test:thelook_ecommerce_test > /tmp/ds_policy.json
-- 编辑 access 部分，加入：
--   {"role": "READER", "userByEmail": "someone@example.com"}
-- 再应用：
--   bq update --source /tmp/ds_policy.json webeye-internal-test:thelook_ecommerce_test


-- ============================================================
-- 3. 表级 IAM — 对单张表设置独立权限
-- ============================================================

-- 授予某用户只能读 users 表，不能读同一数据集的 orders 表
-- 注意：表级 IAM 需要 BigQuery Data Owner 角色

-- 方法一：用 bq 命令行
-- 先获取表的 IAM 策略：
--   bq get-iam-policy webeye-internal-test:thelook_ecommerce_test.users > /tmp/table_policy.json
-- 编辑 JSON，添加 binding：
--   {
--     "bindings": [
--       {
--         "role": "roles/bigquery.dataViewer",
--         "members": ["user:someone@example.com"]
--       }
--     ],
--     "etag": "..."
--   }
-- 再设置回去：
--   bq set-iam-policy webeye-internal-test:thelook_ecommerce_test.users /tmp/table_policy.json

-- 方法二：用 API / Terraform（SQL DCL 目前不支持表级）

-- 验证：
-- 该用户执行 SELECT * FROM `webeye-internal-test.thelook_ecommerce_test.users` → 成功
-- 该用户执行 SELECT * FROM `webeye-internal-test.thelook_ecommerce_test.orders` → 权限拒绝

-- 撤销：
--   bq get-iam-policy webeye-internal-test:thelook_ecommerce_test.users > /tmp/table_policy.json
-- 删除对应 binding 后：
--   bq set-iam-policy webeye-internal-test:thelook_ecommerce_test.users /tmp/table_policy.json


-- ============================================================
-- 4. 列级安全 — Policy Tags
-- ============================================================
-- 列级安全需要先在 Data Catalog 创建 Taxonomy 和 Policy Tag，
-- 再把 Tag 绑定到列上，最后对 Tag 设置 IAM。
-- 这部分用 gcloud + bq 命令操作。
--
-- 步骤 1: 创建分类体系（Taxonomy）
--   gcloud data-catalog taxonomies create sensitive_data \
--     --location=US \
--     --display-name="Sensitive Data Classification"
--
-- 步骤 2: 创建 Policy tag（高敏感）
--   gcloud data-catalog policy-tags create high_sensitivity \
--     --taxonomy=sensitive_data \
--     --location=US \
--     --display-name="High Sensitivity"
--
--   记下输出的 Policy tag ID，格式类似：
--   projects/webeye-internal-test/locations/US/taxonomies/TAXONOMY_ID/policyTags/POLICY_TAG_ID
--
-- 步骤 3: 对 Policy tag 设置 IAM — 只有特定用户/组能读
--   gcloud data-catalog policy-tags add-iam-policy-binding POLICY_TAG_ID \
--     --member="user:someone@example.com" \
--     --role="roles/datacatalog.categoryFineGrainedReader"
--
--   其他没有 Fine-Grained Reader 角色的人查这个列会报权限错误
--
-- 步骤 4: 给列绑定 Policy tag（用 bq update）
--   bq update --schema \
--     webeye-internal-test:thelook_ecommerce_test.users \
--     /tmp/users_schema.json
--
--   schema JSON 中，给 email 列加上 policyTags：
--   [
--     {"name": "id", "type": "INT64"},
--     {"name": "email", "type": "STRING", "policyTags": {"names": ["projects/webeye-internal-test/locations/US/taxonomies/TAXONOMY_ID/policyTags/POLICY_TAG_ID"]}},
--     ...
--   ]
--
-- 步骤 5: 启用分类的强制访问控制
--   gcloud data-catalog taxonomies update sensitive_data \
--     --location=US \
--     --activated-policy-types=FINE_GRAINED_ACCESS_CONTROL
--
-- 验证：
--   有 Fine-Grained Reader 权限的用户：
--     SELECT email FROM `webeye-internal-test.thelook_ecommerce_test.users` → 成功
--   没有权限的用户：
--     SELECT email FROM `webeye-internal-test.thelook_ecommerce_test.users` → 报错
--   没有权限的用户查询其他非保护列仍然可以：
--     SELECT id, first_name FROM `webeye-internal-test.thelook_ecommerce_test.users` → 成功
--
-- 撤销：
--   gcloud data-catalog policy-tags remove-iam-policy-binding POLICY_TAG_ID \
--     --member="user:someone@example.com" \
--     --role="roles/datacatalog.categoryFineGrainedReader"
--   删除 Policy tag：
--   gcloud data-catalog policy-tags delete POLICY_TAG_ID
--   删除 Taxonomy：
--   gcloud data-catalog taxonomies delete sensitive_data --location=US


-- ============================================================
-- 5. 行级安全 — Row Access Policies（最细粒度）
-- ============================================================

-- 5a. 按地区过滤：APAC 组只能看某些行
-- 先给表加一个 region 列来演示（实际数据没有这个列，模拟一下）
ALTER TABLE `webeye-internal-test.thelook_ecommerce_test.users`
ADD COLUMN IF NOT EXISTS region STRING DEFAULT 'US';

-- 给部分行设为 APAC
UPDATE `webeye-internal-test.thelook_ecommerce_test.users`
SET region = 'APAC'
WHERE state IN ('Hawaii', 'Alaska')
LIMIT 10;

-- 创建行级访问策略：只有指定用户能看到 APAC 行
CREATE OR REPLACE ROW ACCESS POLICY apac_only
ON `webeye-internal-test.thelook_ecommerce_test.users`
GRANT TO ("user:chengkang.zhao@webeye.com")
FILTER USING (region = "APAC");

-- 验证：chengkang.zhao@webeye.com 执行
--   SELECT id, first_name, region FROM `webeye-internal-test.thelook_ecommerce_test.users` WHERE region = 'APAC'
--   → 能看到 APAC 行
-- 其他用户执行同样查询 → 看不到任何 APAC 行


-- 5b. 用 SESSION_USER() 实现"每个人只能看自己的数据"
-- 假设 users 表的 email 对应 Google 账号
CREATE OR REPLACE ROW ACCESS POLICY self_only
ON `webeye-internal-test.thelook_ecommerce_test.users`
GRANT TO ("domain:webeye.com")
FILTER USING (email = SESSION_USER());

-- 验证：用户 jim@example.com 查询时只能看到 email = 'jim@example.com' 的行


-- 5c. 用查找表管理行级权限（推荐方案，适合多用户多权限）
-- 创建一张权限映射表
CREATE OR REPLACE TABLE `webeye-internal-test.thelook_ecommerce_test.user_region_access` (
  user_email STRING,
  allowed_region STRING
);

INSERT INTO `webeye-internal-test.thelook_ecommerce_test.user_region_access`
VALUES
  ("chengkang.zhao@webeye.com", "US"),
  ("chengkang.zhao@webeye.com", "APAC"),
  ("someone@example.com", "APAC");

-- 创建基于查找表的行级策略
CREATE OR REPLACE ROW ACCESS POLICY region_lookup
ON `webeye-internal-test.thelook_ecommerce_test.users`
GRANT TO ("domain:webeye.com")
FILTER USING (
  region IN (
    SELECT allowed_region
    FROM `webeye-internal-test.thelook_ecommerce_test.user_region_access`
    WHERE user_email = SESSION_USER()
  )
);

-- 好处：加用户/改权限只需要改查找表，不需要重建 Row Access Policy

-- 查看表上的所有 Row Access Policy
SELECT * FROM `webeye-internal-test.thelook_ecommerce_test.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES`
WHERE table_name = 'users';

-- 删除行级策略
DROP ROW ACCESS POLICY apac_only ON `webeye-internal-test.thelook_ecommerce_test.users`;
DROP ROW ACCESS POLICY self_only ON `webeye-internal-test.thelook_ecommerce_test.users`;
DROP ROW ACCESS POLICY region_lookup ON `webeye-internal-test.thelook_ecommerce_test.users`;


-- ============================================================
-- 6. Authorized Views — 授权视图
-- ============================================================

-- 场景：让某些用户只能看到脱敏后的用户信息（隐藏 email 和地址）
-- 源数据在 thelook_ecommerce_test，视图放在另一个数据集

-- 创建视图所在的数据集
CREATE SCHEMA IF NOT EXISTS `webeye-internal-test.thelook_ecommerce_public`
OPTIONS(location="US");

-- 创建脱敏视图（去掉 email、street_address、postal_code 等敏感列）
CREATE OR REPLACE VIEW `webeye-internal-test.thelook_ecommerce_public.users_safe` AS
SELECT
  id,
  first_name,
  last_name,
  age,
  gender,
  state,
  city,
  country
FROM `webeye-internal-test.thelook_ecommerce_test.users`;

-- 授权视图可以读取源数据集（关键步骤！）
-- 用 bq 命令：
--   bq add-iam-policy-binding --member=project:webeye-internal-test:thelook_ecommerce_public \
--     --role=roles/bigquery.dataViewer \
--     webeye-internal-test:thelook_ecommerce_test
--
-- 或者更简单地用 authorized dataset 方式：
-- 在源数据集的 access 列表中添加授权数据集

-- 给普通用户授权访问视图数据集
GRANT `roles/bigquery.dataViewer`
ON SCHEMA `webeye-internal-test.thelook_ecommerce_public`
TO "user:someone@example.com";

-- 验证：
--   该用户查询视图 → 成功：
--     SELECT * FROM `webeye-internal-test.thelook_ecommerce_public.users_safe` LIMIT 10;
--   该用户查询源表 → 权限拒绝：
--     SELECT * FROM `webeye-internal-test.thelook_ecommerce_test.users` LIMIT 10;

-- 撤销：
REVOKE `roles/bigquery.dataViewer`
ON SCHEMA `webeye-internal-test.thelook_ecommerce_public`
FROM "user:someone@example.com";


-- ============================================================
-- 清理测试资源
-- ============================================================
-- DROP SCHEMA IF EXISTS `webeye-internal-test.thelook_ecommerce_test` CASCADE;
-- DROP SCHEMA IF EXISTS `webeye-internal-test.thelook_ecommerce_public` CASCADE;
