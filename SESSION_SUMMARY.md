# BQCA 项目会话全量总结报告 (Session Summary)

> **最近更新时间**：2026-07-30 09:26:00 (Asia/Shanghai)  
> **状态**：双飞书机器人 + 全球区 (Global) 双 BQCA Agent 架构已全量部署上线，所有单元测试 100% 通过，Git 提交已推送 GitHub。

---

## 1. 🎯 项目核心目标与成果总览

本项目旨在构建一个高可用、多租户的 **BQCA (BigQuery Conversational Analytics) 智能数据分析服务**，支持通过**飞书机器人 (Feishu Bot)** 和 **Agent Skill (AI 智能体技能)** 两种渠道，快速查询与分析企业电商与游戏数据。

在本次会话中，完成了以下架构升级与问题修复：

1. **全新 Global 游戏 BQCA Agent 创建与切换**：
   * 在 GCP `locations/global` 区域调用 SDK 创建了全新的游戏 Agent **`game-analyst-cn`**（绑定 `firebas_bq.all_events_view`），彻底解决了跨区域 `us` 端点不匹配与 403 权限问题。
   * 更新 `.env` 与 `app/config.py` 配置为 `GAME_CA_AGENT_ID=game-analyst-cn` 与 `GAME_CA_LOCATION=global`。
2. **Cloud Run 生产部署与上线验证**：
   * 部署 Cloud Run Revision `bqca-bot-00029-hjz`，绑定 `bqca-runner` 全局大权限账号。
   * 验证线上健康检查接口 `/health` 正常（返回 `{"status":"ok"}`）；实测飞书卡片推送正常（129 行分析数据正常返回，HTTP 200 OK）。
3. **全领域 Skill 升维与目录瘦身**：
   * 升级项目技能 [BQCA_Query_Client/SKILL.md](file:///Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user/BQCA_Query_Client/SKILL.md)，原生支持通过 `domain` 参数实现【电商业务】与【游戏业务】的一站式智能路由与角色管控（`运营经理` vs `一线客服`）。
   * 清理并删除了多余的 `BQCA_Game_Client` 与 `BQCA_Unified_Client` 冗余目录。
   * 实测通过升维后的 Skill 分别成功调用了电商（2025 各国有效销售额/毛利率）和游戏（Android vs iOS DAU/留存对比）查询，均生成高质量 SQL 和业务洞察。
4. **Git 代码提交与 GitHub 远程同步**：
   * 提交全量代码（Commit: `0a00103`），附带规范的中文 Git Commit 记录；成功避开 Sensitive Tokens 并推送至 `origin/main` 远程仓库。

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
  --env-vars-file=.env
```

健康检查验证：
```bash
curl -s https://bqca-bot-839062387451.asia-east1.run.app/health
# 返回 {"status":"ok"}
```

---

*（本总结报告由定时任务自动维持每日晚 21:00 增量更新）*
