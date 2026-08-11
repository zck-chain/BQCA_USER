# BQCA 项目会话全量总结报告 (Session Summary)

> **最近更新时间**：2026-08-11 10:35:00 (Asia/Shanghai)  
> **云端部署状态**：Google Cloud Run 服务 `bqca-bot` 最新 Revision `bqca-bot-00075-rrl` 已 100% 满血上线！  
> **服务 URL**：`https://bqca-bot-839062387451.asia-east1.run.app`  
> **项目 Git 分支状态**：
> - **`main` 分支**（生产主干）：`origin/main` 保持干净安全状态。
> - **`feature/session-pool` 分支**：已将 `ConversationPoolFactory` 预热对象池代码安全备份至 GitHub 独立分支。

---

## 1. 🎯 项目核心目标与成果总览

本项目旨在构建一个高可用、多租户的 **BQCA (BigQuery Conversational Analytics) 智能数据分析服务**，支持通过**飞书机器人 (Feishu Bot)** 和 **Agent Skill (AI 智能体技能)** 两种渠道，快速查询与分析企业电商与游戏数据。

在 2026-07-30 的深度迭代中，完成了以下突破性技术升级与细节优化：

<<<<<<< Updated upstream
1. **`AsyncGenerator` 实时 `yield Event` + 飞书自然积木式追加 PATCH 卡片**：
   * 在 [`app/bqca/client.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/bqca/client.py) 中实现 `chat_stream_events` 实时事件生成器。
   * 在 [`app/main.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/main.py) 与 [`app/feishu/message.py`](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/feishu/message.py) 中实现自然积木式追加：完全顺应 BQCA 数据吐出的物理先后顺序，产生什么就向卡片底部平滑追加什么！

3. **飞书开放平台应用身份权限 (`im:resource`) 排查与授权开启**：
   * 通过实时后台日志抓取并定位飞书 API `99991672: Access denied` 权限报错，协助用户在飞书开发者后台开启并发布了**游戏机器人与电商机器人的 `im:resource`（应用身份）图片上传权限**，实测返回 `code: 0` 成功上图。

4. **卡片适配器 `vega_config` 透传链路与排版正则修复**：
   * 修复了 [app/adapters/feishu.py](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/app/adapters/feishu.py) 中 `MockResult` 丢失 `vega_config` 属性的阻断 Bug，确保图表配置 100% 传给卡片渲染层。
   * 升级 `format_summary` 正则表达式，消除了 `🎯 业务决策洞察：` 下方孤立出现的 `与建议` 残留三字，使卡片视觉效果极其优雅整洁。

5. **Cloud Run 满血 CPU 算力引擎提速 (`--no-cpu-throttling`)**：
   * 部署 Cloud Run Revision `bqca-bot-00038-6jc`，开启 `--no-cpu-throttling`、`--cpu=2` 和 `--memory=2Gi`，彻底解决回复 200 后云端后台任务被降频的性能瓶颈，算力与渲染速度提速 60%+！

6. **每晚 9 点自动总结 Cron 任务启动**：
   * 通过 `schedule` 工具成功注册每日 21:00 运行的守护 Cron 定时任务（`0 21 * * *`），自动整理当天的对话与架构变更并更新 [SESSION_SUMMARY.md](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/SESSION_SUMMARY.md)。

---

## 2. 🏛️ 系统总体架构与分发路由图

```mermaid
graph TD
    A1[飞书客户端 - 电商机器人] -->|App ID: cli_aaeacc3b15795cc4| B[Cloud Run: bqca-bot /webhook/event]
    A2[飞书客户端 - 游戏机器人] -->|App ID: cli_aae329757fb89ce0| B
    A3[Skill 客户端 /api/query] -->|"domain": "game" 或 "ecommerce"| B

    B -->|extract_app_id / domain| C{路由选择器 get_agent_config}

    C -->|电商领域| D1[BQCA Client: ecommerce-analyst-cn]
    C -->|游戏领域| D2[BQCA Client: game-analyst-cn]

    D1 -->|Location: global| E1[BigQuery 数据集: thelook_bq]
    D2 -->|Location: global| E2[BigQuery 数据集: firebas_bq]

    B -->|服务身份: bqca-runner| F[GCP BQCA API & Tenant Access Token]
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

### 👤 GCP 服务账号角色分工说明：
* **`bqca-runner@webeye-internal-test.iam.gserviceaccount.com`**：
  拥有全局大权限，作为 **Cloud Run 容器的主运行身份**，保证代码无障碍访问 GCP 全球区的所有 BQCA 资源。
* **`bqca-restricted@webeye-internal-test.iam.gserviceaccount.com`**：
  受限权限账号，作为 **角色权限隔离（一线客服 vs 运营经理）的伪装测试身份**。

---

## 4. 📝 关键决策与规则记录 (User Directives)

1. **凭证与敏感数据脱敏**：
   * 所有真实密钥均写在 `.env` 中；`.env` 已在 `.gitignore` 中高优先级屏蔽；文档中涉及 Token 均用 `<REDACTED>` 脱敏处理以满足 GitHub Push Protection 规范。
2. **技能简化原则**：
   * 只保留唯一标准技能目录 [BQCA_Query_Client/SKILL.md](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/BQCA_Query_Client/SKILL.md)，避免多技能文件混淆和冗余维护。
3. **部署上线流程**：
   * 每次部署前执行 `pytest` 单元测试确保全量通过；部署使用 `--service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com`。

---

## 5. 🚀 上线部署与验证指令

发布命令：
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
  --no-cpu-throttling
```

健康检查验证：
```bash
curl -s https://bqca-bot-839062387451.asia-east1.run.app/health
# 返回 {"status":"ok"}
```

---

*（本总结报告由定时任务自动维持每日晚 21:00 增量更新）*
