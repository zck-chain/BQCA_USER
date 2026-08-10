# BQCA 项目全量会话与架构总结报告 (Project Session Summary)

> **最近更新时间**：2026-08-07 15:35:00 (Asia/Shanghai)  
> **云端部署状态**：Google Cloud Run 服务 `bqca-bot` 最新 Revision `bqca-bot-00070-d9f` 已 100% 满血上线！  
> **服务 URL**：`https://bqca-bot-839062387451.asia-east1.run.app`  
> **项目 Git 分支状态**：
> - **`main` 分支**（生产主干）：`origin/main` 保持干净安全状态，集成了 `ConversationPoolFactory` 预热对象池与 gRPC Channel 复用。
> - **`test` 分支** ([`origin/test`](https://github.com/zck-chain/BQCA_USER/tree/test))：包含毫秒级耗时 JSON 导出落盘代码与调试下载接口（GitHub 远程安全备份）。

---

## 1. 🎯 核心成果与最新进展概览

本项目旨在构建一个高可用、多租户的 **BQCA (BigQuery Conversational Analytics) 智能数据分析服务**，支持通过 **飞书机器人 (Feishu Bot)** 和 **Agent Skill (AI 智能体技能)** 快速查询与分析电商与游戏全领域数据。

最近完成的硬核提速与架构重构如下：

1. **工业级预热会话工厂 (`ConversationPoolFactory`) 落地**：
   * 在 [`app/bqca/pool.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/bqca/pool.py) 中重构了独立的 `ConversationPoolFactory` 模块。
   * 服务启动 (`@app.on_event("startup")`) 时，后台协程自动向 GCP 申请预热存满空闲的 Conversation Session，用户提问时 **0ms 秒出会话 ID**，彻底切掉 2.5 秒的云端 Session 创建等待！

2. **gRPC Channel 长连接复用 (`_CLIENT_CACHE`)**：
   * 在 [`app/bqca/client.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/bqca/client.py) 中通过 `_CLIENT_CACHE` 维持 gRPC Channel 单例长连接通道。
   * HTTP/2 多路复用 (Multiplexing) 支持成百上千个并发请求在同一个 Channel 上并行传输，**彻底省去反复 TCP 建连与 TLS 握手的 200ms 开销**。

3. **过天/过期会话无感自愈重连 (Auto-healing)**：
   * 捕获 `NOT_FOUND` 或会话过期的异常，0ms 自动从预热池弹出最新空闲 Session 重试，彻底解决隔天或长时间不聊天导致的失效报错问题。

---

## 📁 2. 项目目录与全量文件功能说明 (Project Directory & File Map)

```text
BQCA_user/
├── app/                        # 🌟 核心应用程序服务包
│   ├── main.py                 # FastAPI 服务入口，包含 Webhook 接收、/api/query 接口与 startup_event 预热触发器
│   ├── config.py               # 全局环境变量映射配置（加载 .env，管理多领域 Agent ID、Location 及飞书 App 密钥）
│   ├── permissions.py          # 角色权限控制模块（定义“运营经理”与“一线客服”权限与服务账号映射）
│   ├── bqca/                   # GCP BQCA (BigQuery Conversational Analytics) SDK 交互封装
│   │   ├── client.py           # BQCA API 核心客户端：包含 ChatResult 定义、gRPC Channel 复用池 _CLIENT_CACHE 与同步 chat()
│   │   ├── pool.py             # 🌟 工业级 ConversationPoolFactory 预热工厂（0ms 会话分配、动态补充、自愈重连）
│   │   └── __init__.py
│   ├── feishu/                 # 飞书开放平台原生协议交互层
│   │   ├── message.py          # 飞书消息卡片适配器：包含 send_result_card 等卡片构建逻辑
│   │   ├── event.py            # 飞书 Webhook 事件解析器（解析 App ID、提问文本、消息 ID、事件去重与超时防重放校验）
│   │   └── __init__.py
│   ├── adapters/               # 多平台卡片适配器模式抽象层
│   │   ├── factory.py          # 适配器工厂 get_card_adapter("feishu")
│   │   ├── feishu.py           # 飞书卡片适配实现类，包含 trim_analysis_preamble 与 clean_latex
│   │   ├── base.py             # 抽象基类 BaseCardAdapter
│   │   └── __init__.py
│   └── storage/                # 数据持久化存储模块
│       ├── sqlite.py           # 本地 SQLite 会话上下文持久化（管理 session_id 与 conversation_name 映射、去重排重锁）
│       ├── gcs.py              # GCP Cloud Storage (GCS) 文件上传（将 native HTML 报告上传至 GCS Bucket 并生成公开 URL）
│       └── __init__.py
│
├── BQCA_Query_Client/          # 🤖 BQCA Agent Skill (AI 智能体技能标准规范)
│   └── SKILL.md                # 规范定义文件：供前端/Agent 调用的统一技能接口说明与 Prompt 规范
│
├── docs/                       # 📚 项目产品设计与架构文档库
│   ├── BQCA飞书智能数据分析方案设计.md # 项目整体技术架构与产品设计方案
│   ├── BQCA权限（以电商接入飞书作为场景）.md # 数据权限隔离设计文档
│   ├── 电商分析师权限问题集.md      # 电商场景问题集与 Q&A 样例
│   ├── 游戏分析师权限问题集.md      # 游戏场景问题集与 Q&A 样例
│   └── drawio/                 # 架构设计图源文件
│
├── scratch/                    # 🧪 压测报告、调试脚本与临时测试文件
│   ├── BQCA智能体_全量链路耗时与性能分析报告.md # 10 问全量性能基准压测与云端/本地对比总结报告
│   ├── test_local_ttft.py      # 本地运行测试首包响应时间 (TTFT) 脚本
│   ├── test_latency_benchmark.py # 性能压测标杆脚本
│   └── ...                      # 其它数据探索与 API 探针脚本
│
├── tests/                      # 🧪 pytest 单元测试套件
│   ├── test_main.py            # main.py API 与 Webhook 路由测试
│   ├── test_bqca_client.py     # BQCA Client 结构解析测试
│   ├── test_message.py         # 飞书卡片构建与消息更新测试
│   ├── test_event.py           # Webhook 事件解析与去重锁测试
│   ├── test_gcs.py             # GCS 上传测试
│   ├── test_sqlite_storage.py  # SQLite 存储测试
│   └── conftest.py             # pytest 全局 fixtures 配置文件
│
├── logs/                       # 📜 本地运行与 ngrok 调试日志目录
├── Dockerfile                  # Cloud Run 容器镜像构建文件（集成思源黑体 fonts-noto-cjk 字体库与 Python 环境）
├── requirements.txt            # Python 依赖包包名与版本清单
├── bqca_sessions.db            # SQLite 本地数据库文件（自动生成，管理会话状态）
└── SESSION_SUMMARY.md          # 🌟 项目全量总结与上下文导览文档（本文件）
```

---

## 3. ⚙️ 当前环境变量与账号配置 (`.env`)

```env
GCP_PROJECT=webeye-internal-test
GCS_BUCKET=bqca-results
BQCA_SUPPORT_SERVICE_ACCOUNT=bqca-restricted@webeye-internal-test.iam.gserviceaccount.com

# 1. 电商飞书机器人 + 电商 BQCA Agent (全球区)
CA_AGENT_ID=ecommerce-analyst-cn
CA_LOCATION=global
FEISHU_APP_ID=cli_aaeacc3b15795cc4
FEISHU_APP_SECRET=<REDACTED>
FEISHU_VERIFICATION_TOKEN=<REDACTED>
FEISHU_ENCRYPT_KEY=

# 2. 游戏飞书机器人 + 游戏 BQCA Agent (全球区)
GAME_CA_AGENT_ID=game-analyst-cn
GAME_CA_LOCATION=global
GAME_FEISHU_APP_ID=cli_aae329757fb89ce0
GAME_FEISHU_APP_SECRET=<REDACTED>
GAME_FEISHU_VERIFICATION_TOKEN=<REDACTED>
GAME_FEISHU_ENCRYPT_KEY=
```

---

## 4. 🔮 后续规划与待办事项 (Roadmap & Next Steps)

1. **飞书端响应速度验收**：
   * 验证 `ConversationPoolFactory` 预热工厂上线后，飞书提问响应速度的切实提升。
2. **多领域多角色联动验固**：
   * 验证游戏机器人（`game-analyst-cn`）与电商机器人（`ecommerce-analyst-cn`）的并发稳定性。
3. **主分支 Git 提交**：
   * 验收无误后，将当前 `main` 分支最新代码推送到 GitHub `origin/main` 远程仓库。
