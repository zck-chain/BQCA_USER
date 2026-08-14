# BQCA (BigQuery Conversational Analytics) 项目交接文档

> **文档生成时间**：2026-08-14  
> **服务部署版本**：Google Cloud Run Revision `bqca-bot-00077-rhx`  
> **Git 状态**：`main` 与 `test` 分支保持 100% 同步并最新（Commit `f6f737a`）

---

## 1. 📌 项目背景与架构总览 (Project Architecture)

本项目是一个基于 **Google BQCA (BigQuery Conversational Analytics)** 的多租户智能数据分析服务。支持企业员工通过 **飞书机器人 (Feishu Bot)** 以及 **Agent Skill (AI 智能体技能)**，以自然语言的方式查询与分析电商和游戏领域的业务数据。

### 🔄 核心数据流转架构：
```text
[用户飞书提问] 
      │
      ▼
[FastAPI Webhook /webhook/event] ── (异步解耦队列) ──► [_process_query 协程]
                                                               │
                                                               ▼
[飞书 0.5s 秒发 Stage 1 加载卡片] ◄────────────── [BQCA AsyncGenerator 实时事件流]
                                                               │
                                         ┌─────────────────────┴─────────────────────┐
                                         │  0.5s  THOUGHT (思考步骤)                  │
                                         │  2.0s  SQL (生成的 BigQuery 查询)          │
                                         │  5.0s  DATA & CHART (预览表格与 VChart)   │
                                         │ 10.0s  FINAL (Gemini 商业决策洞察)          │
                                         └─────────────────────┬─────────────────────┘
                                                               │
                                                               ▼
                                             [飞书 Stage 3 PATCH 原位平滑更新最终卡片]
```

---

## 2. 🌳 代码仓库与 Git 分支策略 (Git Branches)

- **GitHub 仓库**：`zck-chain/BQCA_USER`
- **主要分支说明**：
  - **`main` (生产主干)**：保持绝对稳定可部署代码，已同步推至 GitHub `origin/main`；
  - **`test` (测试分支)**：与 `main` 完全同步，供后续同事开展功能测试（Commit `f6f737a`）；
  - **`feature/session-pool` (备份分支)**：归档备份了 `ConversationPoolFactory` 预热池代码（若后续有长连接预热诉求可切至此分支合并）。

---

## 3. 📂 项目目录结构与核心模块 (Directory Map)

```text
BQCA_user/
├── app/
│   ├── main.py                # FastAPI 入口、Webhook 事件监听、_process_query 异步流处理
│   ├── config.py              # Pydantic 环境变量配置 (Settings)
│   ├── bqca/
│   │   └── client.py          # BQCA gRPC 客户端、SA 凭证鉴权与 chat_stream_events 异步生成器
│   ├── feishu/
│   │   ├── event.py           # 飞书事件解析与加解密校验
│   │   └── message.py         # 飞书 3 阶段【自然积木式追加卡片】渲染函数 (send_initial / patch_progress / patch_final)
│   ├── adapters/              # 多渠道适配器抽象层 (base.py / feishu.py)
│   └── storage/
│       └── sqlite.py          # 会话上下文 (session_key -> conversation_name) 本地持久化与 24h TTL 清理
├── tests/                     # 单元测试与集成测试套件 (test_main.py, test_bqca_client.py)
├── benchmark_bqca_latency.py  # 多场景 BQCA 接口响应耗时基准测试脚本
├── SESSION_SUMMARY.md         # 全量历史修改与云端部署记录
├── HANDOVER_DOCUMENT.md       # 本项目交接文档
├── Dockerfile                 # Cloud Run 容器构建定义
└── .env                       # 本地环境变量与凭证（勿提交至公开仓库）
```

---

## 4. 🚀 云端部署与运维指南 (Cloud Run Operations)

### 服务信息：
- **云厂商与服务**：Google Cloud Run
- **服务名称**：`bqca-bot`
- **部署 Region**：`asia-east1` (中国香港/台湾/东京/新加坡附近节点)
- **GCP 项目**：`webeye-internal-test`
- **服务账号**：`bqca-runner@webeye-internal-test.iam.gserviceaccount.com`
- **生产 URL**：`https://bqca-bot-839062387451.asia-east1.run.app`

### 部署命令 (Shell)：
```bash
gcloud run deploy bqca-bot \
  --source . \
  --region asia-east1 \
  --project webeye-internal-test \
  --allow-unauthenticated \
  --service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com \
  --env-vars-file=.env \
  --min-instances=1 \
  --max-instances=1 \
  --cpu=2 \
  --memory=2Gi \
  --no-cpu-throttling \
  --timeout=300s
```

### 常用运维与日志指令：
- **查看健康状态**：`curl -s https://bqca-bot-839062387451.asia-east1.run.app/health`
- **读取实时日志**：
  ```bash
  gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=bqca-bot" \
    --limit=50 \
    --project=webeye-internal-test \
    --format="value(textPayload,jsonPayload.message)"
  ```

---

## 5. 🔑 核心配置与环境变量 (Environment Variables)

关键配置存储在 `.env` 文件中：

| 变量名 | 作用说明 | 示例值/默认值 |
| :--- | :--- | :--- |
| `FEISHU_APP_ID` | 飞书开放平台开放应用 ID | `cli_a1b2c3...` |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret | `secret_xyz...` |
| `FEISHU_VERIFICATION_TOKEN` | 飞书事件订阅验证 Token | `token_123...` |
| `FEISHU_ENCRYPT_KEY` | 飞书 Event 报文解密 Key | `key_abc...` |
| `GCP_PROJECT` | Google Cloud 项目 ID | `webeye-internal-test` |
| `CA_LOCATION` | BQCA 服务部署位置 | `global` |
| `CA_AGENT_ID` | 电商 BQCA 数据 Agent ID | `ecommerce-analyst-cn` |
| `GAME_AGENT_ID` | 游戏 BQCA 数据 Agent ID | `game-analyst-cn` |

---

## 6. 🎨 核心功能特性与交互约定 (Core Features & Rules)

1. **自然积木式追加卡片 (Progressive Block Cards)**：
   - 避免掉任何无谓的居中遮罩和全屏刷弹；统一使用 `indigo` 色系 Header。
   - BQCA 产生什么，卡片底部就平滑追加压入什么（`提问` $\rightarrow$ `折叠 SQL` $\rightarrow$ `数据表格/VChart` $\rightarrow$ `商业洞察`）。
2. **快捷追问推荐按钮状态**：
   - 根据业务要求，Section 6（快捷追问决策按钮）在 [`app/feishu/message.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/feishu/message.py) 内保持 **100% 注释状态**，不要轻易开启。
3. **多轮对话上下文复用**：
   - 同一个 Chat ID 24小时内的提问会自动复用 `conversation_name`。对于“对比 Android 和 iOS”这种分析型追问，BQCA 会直接复用内存回答，不重复发起 SQL。

---

## 7. 🧪 测试与 Benchmark 工具 (Testing & Benchmarking)

### 运行单元测试：
```bash
PYTHONPATH=. python3 tests/test_bqca_client.py
PYTHONPATH=. python3 tests/test_main.py
```

### 运行 BQCA 响应耗时基准测试：
我们在根目录下提供了 `benchmark_bqca_latency.py` 脚本，可一键测试简单查询、复杂关联、深度归纳、趋势预测 4 类问题的真实响应耗时：
```bash
PYTHONPATH=. python3 benchmark_bqca_latency.py
```

---

## 8. 📝 常见排查手册与注意点 (Troubleshooting & Tips)

1. **`TypeError: _get_client() got an unexpected keyword argument`**：
   - 注意：`app/bqca/client.py` 中的 `_get_client(credentials)` 只接收凭证对象，身份伪装由 `_get_credentials(target_sa)` 完成，请勿在 `_get_client` 中传入 `target_sa`。
2. **飞书报错“查询处理失败，请稍后再试”**：
   - 查看 Cloud Run 报错日志；通常是 GCP IAM 服务账号权限、网络超时或客户端参数传错导致。`_process_query` 会安全捕获并原位 PATCH 提醒用户。
3. **切换/修改部署**：
   - 项目在 `main` 和 `test` 分支上均可独立编译运行，修改代码后记得运行 `python3 -m py_compile` 确保语法无误。

---

祝接手的同事开发顺利！有任何疑问可随时查阅项目根目录下的 `SESSION_SUMMARY.md` 与 Git Commit 历史！
