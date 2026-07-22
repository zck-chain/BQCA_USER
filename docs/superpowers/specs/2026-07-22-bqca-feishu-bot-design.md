 # BQCA 飞书智能查询助手 — 设计规格文档
 
 ## 产品定位
 
 让业务人员通过飞书机器人，用自然语言查询 BigQuery 电商数据，获得可视化结果。先内部验证，成熟后考虑对外。
 
 核心价值：**零 SQL 门槛 + 即时可视化 + 飞书内闭环**
 
 ---
 
 ## 现有资源
 
 | 资源 | 详情 |
 |------|------|
 | GCP 项目 | `webeye-internal-test` |
 | 当前账号 | `chengkang.zhao@webeye.com`（BigQuery Admin + Gemini Data Analytics Admin） |
 | Service Account | `bqca-runner@webeye-internal-test.iam.gserviceaccount.com`（BQ Admin、Gemini、GCS 等权限） |
 | 已启用 API | BigQuery、Vertex AI、Dataform、Gemini、Cloud Run、Cloud Functions、Secret Manager |
 | 数据集 | `thelook_ecommerce`（5 张表），通过配置切换 |
 | 飞书 | 待创建机器人应用 |
 
 ---
 
 ## 架构与数据流（方案 A：三步 Gemini 调用）
 
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
     ├─ 2. SQL 生成：问题 + 动态 schema → Gemini → SQL
     │     └─ 安全检查：只允许 SELECT，强制 LIMIT ≤ 1000
     │
     ├─ 3. 查询执行：SQL → BigQuery → DataFrame
     │
     ├─ 4. HTML 生成：问题 + 数据 → Gemini → 完整 HTML 可视化代码
     │
     ├─ 5. 摘要生成：问题 + 数据 → Gemini → 一段中文摘要
     │
     ├─ 6. 存储：HTML → GCS 公开桶 → 返回 URL
     │
     └─ 7. 回复飞书：摘要文字 + "查看详情"链接
           └─ 幂等处理：基于 message_id 去重，防飞书重试
 ```
 
 认证：所有 GCP 调用用 `bqca-runner` SA，飞书用户零权限要求。
 
 ---
 
 ## 模块职责与边界
 
 ```
 app/
 ├── main.py              # FastAPI 入口，路由，启动时加载 schema
 ├── config.py            # 配置管理（环境变量 + GCP 配置）
 ├── feishu/
 │   ├── event.py         # 接收事件、验签、提取问题
 │   ├── message.py       # 发送消息、构建卡片
 │   └── crypto.py        # 飞书加解密工具（V1 可不用）
 ├── engine/
 │   ├── schema.py        # 从 INFORMATION_SCHEMA 动态拉取表结构，启动时缓存
 │   ├── sql_generator.py # 调 Gemini 生成 SQL
 │   ├── query_runner.py  # 调 BigQuery 执行 SQL
 │   └── safety.py        # SQL 安全检查（只允许 SELECT + 强制 LIMIT）
 ├── renderer/
 │   ├── html_generator.py # 调 Gemini 生成完整 HTML
 │   └── summary.py        # 调 Gemini 生成中文摘要
 └── storage/
     └── gcs.py           # HTML 上传 GCS，返回公开 URL
 ```
 
 **关键设计点：**
 - `schema.py` 启动时拉一次缓存到内存，不每次请求都查
 - `sql_generator.py` 和 `html_generator.py` 都调 Gemini，但 prompt 不同、职责独立
 - `summary.py` 和 `html_generator.py` 可合并为一次 Gemini 调用（一次生成 HTML + 摘要）
 - `crypto.py` V1 可不启用
 
 ---
 
 ## 配置项（环境变量）
 
 | 变量 | 说明 | 示例 |
 |------|------|------|
 | `BQ_PROJECT` | GCP 项目 ID | `webeye-internal-test` |
 | `BQ_DATASET` | 目标数据集 | `thelook_ecommerce` |
 | `GCS_BUCKET` | HTML 存储桶名 | `bqca-results` |
 | `FEISHU_APP_ID` | 飞书应用 ID | 待提供 |
 | `FEISHU_APP_SECRET` | 飞书应用密钥 | 待提供 |
 | `FEISHU_VERIFICATION_TOKEN` | 事件验签 token | 待提供 |
 | `FEISHU_ENCRYPT_KEY` | 事件加密 key | 待提供（V1 可空） |
 | `GEMINI_MODEL` | 使用的 Gemini 模型 | `gemini-2.0-flash` |
 | `MAX_RESULT_ROWS` | 查询结果最大行数 | `1000` |
 
 ---
 
 ## 错误处理
 
 | 场景 | 处理方式 |
 |------|----------|
 | Gemini 生成无效 SQL | 捕获 BQ 执行错误，回复"无法理解您的问题，请换种说法" |
 | BigQuery 执行超时（>30s） | 回复"查询耗时较长，请稍后再试或缩小查询范围" |
 | Gemini 生成 HTML 失败 | 降级为纯数据表格 HTML，保证链接始终可用 |
 | 飞书事件重复推送 | 基于 message_id 去重，不重复处理 |
 | BigQuery 返回超过 MAX_RESULT_ROWS | 截断数据，摘要中提示"结果已截断" |
 
 ---
 
 ## GCP 资源与部署
 
 **需创建的 GCP 资源：**
 1. GCS 公开桶 `bqca-results`（allUsers 可读）
 2. Cloud Run 服务，关联 `bqca-runner` SA
 
 **部署流程：**
 1. `gcloud storage buckets create bqca-results`
 2. `gcloud builds submit` → 构建 Docker 镜像
 3. `gcloud run deploy` → 部署服务，绑定 SA
 4. 拿到 Cloud Run URL → 填入飞书事件订阅
 
 **CI/CD：** V1 手动部署，后续接 GitHub Actions。
 
 ---
 
 ## 版本规划
 
 **V1（当前）：** 核心问答链路 — 飞书提问 → Gemini SQL → BQ 执行 → HTML 可视化 → 返回摘要+链接
 
**V2：** 对话式追问、定时报告推送、数据异常告警、多数据集、查询历史

**V3：** 权限体系（谁可用、谁能看什么数据）
 
 ---
 
 ## 用户前置任务
 
 飞书开放平台创建机器人应用，提供：App ID、App Secret、Verification Token、Encrypt Key
