 # BQCA 飞书智能查询助手 — 产品设计文档
 
 ## 1. 产品定位
 
 让业务人员通过飞书机器人，用自然语言查询 BigQuery 电商数据，获得可视化结果。
 
 核心价值：**零 SQL 门槛 + 即时可视化 + 飞书内闭环**
 
 ---
 
 ## 2. 现有资源盘点
 
 | 资源 | 详情 |
 |------|------|
 | GCP 项目 | `webeye-internal-test` |
 | 当前账号 | `chengkang.zhao@webeye.com`（BigQuery Admin + Gemini Data Analytics Admin） |
 | Service Account | `bqca-runner@webeye-internal-test.iam.gserviceaccount.com`（已具备 BQ Admin、Gemini、GCS 等权限） |
 | 已启用 API | BigQuery、Vertex AI、Dataform、Gemini、Cloud Run、Cloud Functions、Secret Manager |
 | 数据集 | `thelook_ecommerce`（5 张表） |
 | 飞书 | 待创建机器人应用 |
 
 ### 数据集 Schema
 
 **orders** — 订单主表
 - `order_id`, `user_id`, `status`, `gender`, `created_at`, `returned_at`, `shipped_at`, `delivered_at`, `num_of_item`
 
 **order_items** — 订单明细
 - `id`, `order_id`, `user_id`, `product_id`, `inventory_item_id`, `status`, `created_at`, `shipped_at`, `delivered_at`, `returned_at`, `sale_price`
 
 **products** — 商品
 - `id`, `cost`, `category`, `name`, `brand`, `retail_price`, `department`, `sku`, `distribution_center_id`
 
 **inventory_items** — 库存
 - `id`, `product_id`, `created_at`, `sold_at`, `cost`, `product_category`, `product_name`, `product_brand`, `product_retail_price`, `product_department`, `product_sku`, `product_distribution_center_id`
 
 **users** — 用户
 - `id`, `first_name`, `last_name`, `email`, `age`, `gender`, `state`, `street_address`, `postal_code`, `city`, `country`, `latitude`, `longitude`, `traffic_source`, `created_at`, `user_geom`
 
 ---
 
 ## 3. 核心用户流程
 
 ```
 用户在飞书群 @BQCA机器人 "上个月销售额最高的5个品类是什么？"
     │
     ▼
 飞书推送 event → 后端接收
     │
     ├─ 1. Gemini API：自然语言 → SQL（注入 schema 上下文）
     ├─ 2. BigQuery API：执行 SQL，获取结果
   ├─ 3. 后端生成 HTML 可视化页面（表格 + 图表）
   ├─ 4. HTML 上传至 GCS / Cloud Run 静态路由
    ├─ 3. Gemini API：问题 + 查询结果 → 生成完整 HTML 可视化代码
    ├─ 4. HTML 上传至 GCS，获取公开链接
     └─ 5. 飞书消息卡片：返回摘要 + "查看详情" 链接
 ```
 
 ---
 
 ## 4. 技术架构
 
 ```
 ┌──────────────────────────────────────────────────┐
 │                    飞书                           │
 │  ┌─────────┐    ┌──────────────┐                  │
 │  │ 用户提问 │───▶│  Bot Webhook  │◀──── 回复卡片   │
 │  └─────────┘    └──────┬───────┘                  │
 └────────────────────────┼─────────────────────────┘
                          │ HTTPS
                          ▼
 ┌──────────────────────────────────────────────────┐
 │            Cloud Run 后端服务 (Python)             │
 │  ┌──────────┐ ┌───────────┐ ┌──────────────────┐ │
 │  │ 飞书事件  │ │ 查询引擎  │ │  HTML 生成 & 托管 │ │
 │  │ 处理模块  │ │           │ │                  │ │
│  │ - 验签    │ │ - Gemini  │ │ - ECharts 图表   │ │
│  │ - 消息解析│ │   生成SQL  │ │ - Gemini 生成HTML│ │
│  │ - 卡片回复│ │ - BQ 执行 │ │ - GCS 上传       │ │
 │  └──────────┘ └───────────┘ └──────────────────┘ │
 │  ┌──────────┐                                    │
 │  │ 权限模块  │ （V2，预留接口）                    │
 │  └──────────┘                                    │
 └──────────────────────────────────────────────────┘
             │               │              │
             ▼               ▼              ▼
      ┌──────────┐   ┌──────────┐   ┌──────────┐
      │ BigQuery │   │ Vertex AI│   │   GCS    │
      │ (查询)   │   │ (Gemini) │   │ (HTML)   │
      └──────────┘   └──────────┘   └──────────┘
 ```
 
 ### 技术选型
 
 | 组件 | 选择 | 理由 |
 |------|------|------|
 | 语言 | Python 3.11 | GCP SDK 生态最完善，BigQuery 客户端库成熟 |
 | 框架 | FastAPI | 轻量、异步、自动 OpenAPI 文档 |
 | 部署 | Cloud Run | 自动扩缩、按请求计费、无需管服务器 |
 | SQL 生成 | Vertex AI Gemini API | 自然语言→SQL，项目已启用 |
| 可视化 | Gemini 生成 HTML | 动态生成完整 HTML 可视化代码，灵活适配任意问题 |
 | HTML 托管 | GCS 公开桶 / Cloud Run 静态路由 | 简单可靠 |
 | 认证 | Service Account (`bqca-runner`) | 已创建，权限齐全 |
 | 配置管理 | Secret Manager | 存放飞书 App 凭证等敏感信息 |
 
 ---
 
 ## 5. 模块设计
 
 ### 5.1 飞书事件处理模块
 
 - 接收飞书 ImMessageReceiveV1 事件
 - 验证请求签名（Encryption Key + Verification Token）
 - 提取用户消息文本（去除 @机器人 的部分）
 - 异步处理查询，先回复"正在查询..."，完成后更新卡片
 
 ### 5.2 查询引擎模块
 
 **Prompt 构造策略：**
 ```
 System: 你是一个 BigQuery SQL 专家。根据以下表结构，将用户问题转为 SQL。
 
 表结构：
 - thelook_ecommerce.orders(order_id, user_id, status, gender, created_at, ...)
 - thelook_ecommerce.order_items(id, order_id, user_id, product_id, ...)
 - ...
 
 规则：
 - 只输出 SQL，不要解释
 - 使用标准 BigQuery SQL 语法
 - 日期字段用 TIMESTAMP 类型处理
 - LIMIT 默认不超过 1000
 
 User: {用户的问题}
 ```
 
 **执行流程：**
 1. Gemini 生成 SQL
 2. 简单安全检查（禁止 DROP/DELETE/UPDATE/INSERT，只允许 SELECT）
 3. BigQuery 执行查询
 4. 返回结果 DataFrame
 
### 5.3 HTML 生成模块

- 将用户问题 + 查询结果发送给 Gemini，生成完整的 HTML 可视化页面代码
- Gemini 根据问题类型自动选择最合适的可视化方式（图表、表格、指标卡等）
- 生成的 HTML 包含 ECharts CDN 引用，可独立运行
- 响应式设计，移动端可查看
 
 ### 5.4 权限模块（V2 预留）
 
 ```
 接口设计：
 - get_user_permission(feishu_user_id) → { datasets: [...], tables: [...], max_rows: N }
 - check_query_allowed(user_id, sql) → bool
 ```
 
 V1 阶段：所有用户可查 `thelook_ecommerce`，上限 1000 行。
 V2 阶段：按飞书用户/群组配置不同的数据访问范围。
 
 ---
 
 ## 6. 飞书机器人配置清单
 
 需要在飞书开放平台创建应用，获取：
 
 | 配置项 | 说明 |
 |--------|------|
 | App ID | 应用唯一标识 |
 | App Secret | 用于获取 tenant_access_token |
 | Verification Token | 事件验签 |
 | Encrypt Key | 事件加密 |
 | 事件订阅 URL | Cloud Run 的 HTTPS 地址 + `/webhook/event` |
 | 权限 | `im:message`（接收消息）、`im:message:send_as_bot`（发送消息） |
 | 机器人能力 | 启用机器人，配置指令 |
 
 ---
 
 ## 7. 项目结构
 
 ```
 bqca-user/
 ├── app/
 │   ├── __init__.py
 │   ├── main.py              # FastAPI 入口，路由定义
 │   ├── feishu/
 │   │   ├── __init__.py
 │   │   ├── event.py         # 事件接收与验签
 │   │   ├── message.py       # 消息发送与卡片构建
 │   │   └── crypto.py        # 飞书加解密
 │   ├── engine/
 │   │   ├── __init__.py
 │   │   ├── sql_generator.py # Gemini 生成 SQL
 │   │   ├── query_runner.py  # BigQuery 执行
 │   │   └── safety.py        # SQL 安全检查
│   ├── renderer/
│   │   ├── __init__.py
│   │   └── html_generator.py # 调 Gemini 生成 HTML 可视化代码
 │   ├── storage/
 │   │   ├── __init__.py
 │   │   └── gcs.py           # GCS 上传 & 公开链接
 │   └── config.py            # 配置管理
├── tests/
 ├── Dockerfile
 ├── cloudbuild.yaml          # Cloud Build 部署配置
 ├── requirements.txt
 ├── .env.example
 └── README.md
 ```
 
 ---
 
 ## 8. 版本规划
 
 ### V1（当前）— 核心问答链路
 
 - [ ] 飞书机器人接收消息
 - [ ] Gemini 生成 SQL
 - [ ] BigQuery 执行查询
 - [ ] 生成 HTML 可视化页面
 - [ ] 飞书返回结果卡片 + 链接
 - [ ] 部署到 Cloud Run
 
 ### V2 — 权限与多数据集
 
 - [ ] 用户/群组权限管理
 - [ ] 支持多个 BigQuery 数据集
 - [ ] 查询历史记录
 - [ ] 查询结果收藏
 
 ### V3 — 协作与洞察
 
 - [ ] 对话式追问（上下文关联）
 - [ ] 定时报告推送
 - [ ] 数据异常告警
 - [ ] 多人协作看板
 
 ---
 
 ## 9. 关键风险与对策
 
 | 风险 | 对策 |
 |------|------|
 | Gemini 生成错误 SQL | SQL 安全校验 + 执行超时保护 + 错误友好提示 |
 | 查询耗时过长 | BigQuery 查询超时设 30s，飞书先回"查询中"，异步更新 |
 | 敏感数据泄露 | V1 限制只读 dataset + 行数上限；V2 加权限体系 |
 | 飞书事件重试 | 幂等处理（基于 message_id 去重） |
 | HTML 页面安全 | GCS 桶设 URL 签名过期，不长期暴露 |
