# BQCA 项目会话全量总结报告 (Session Summary)

> **最近更新时间**：2026-07-29 16:17:00 (Asia/Shanghai)  
> **状态**：双飞书机器人 + 双 BQCA Agent 架构已全量完成，配齐定时更新机制。

---

## 1. 🎯 项目核心目标与成果总览

本项目旨在构建一个高可用、多租户的 **BQCA (BigQuery Conversational Analytics) 智能数据分析服务**，支持通过**飞书机器人 (Feishu Bot)** 和 **Agent Skill (AI 智能体技能)** 两种渠道，快速查询与分析企业数据。

在本次会话中，完成了以下架构升级与问题修复：

1. **双飞书机器人 ➔ 绑定双 BQCA 智能体 (动态路由架构)**：
   * **电商机器人** (`cli_aaeacc3b15795cc4`) ➔ 动态路由至 **电商 BQCA Agent** (`ecommerce-analyst-cn`，全球区 `global`)。
   * **游戏机器人** (`cli_aae329757fb89ce0`) ➔ 动态路由至 **Flood-It! 游戏 BQCA Agent** (`agent_68092f81-2c23-4c6a-aa4c-633df98549f0`，美区 `us`)。
2. **多租户 Token 自动换取与 Card 适配器**：
   * 提炼出 `extract_app_id` 识别 Webhook 来源；
   * `_get_tenant_token(app_id)` 动态根据 `app_id` 自动换取专属机器人的 `tenant_access_token`；
   * 重构 `FeishuAdapter` 并彻底清理 `app/main.py` 主控制器（代码瘦身至 ~300 行）。
3. **消除硬编码与十二要素 (12-Factor) 环境变量重构**：
   * 所有凭证和配置均抽离至 `.env` 与 `.env.example`；
   * `.env` 在 `.gitignore` 中第一行高优先级屏蔽，确保凭证安全绝不外泄。
4. **定位并解决美区游戏机器人 403 报错根因**：
   * 明确游戏 Agent 因关联 BigQuery `firebas_bq` 数据集，物理区域锁定为 **`us`**；
   * 明确 Cloud Run 之前报错是因为默认使用了 `839062387451-compute` 账号；
   * 锁定最佳部署方案：部署时显式指定使用全量大权限账号 `--service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com`。

---

## 2. 🏛️ 系统总体架构与分发路由图

```mermaid
graph TD
    A1[飞书客户端 - 电商机器人] -->|App ID: cli_aaeacc3b15795cc4| B[Cloud Run: bqca-bot /webhook/event]
    A2[飞书客户端 - 游戏机器人] -->|App ID: cli_aae329757fb89ce0| B
    A3[Skill 客户端 /api/query] -->|"domain": "game" 或 "ecommerce"| B

    B -->|extract_app_id / domain| C{路由选择器 get_agent_config}

    C -->|电商领域| D1[BQCA Client: ecommerce-analyst-cn]
    C -->|游戏领域| D2[BQCA Client: agent_68092f81-2c23-4c6a-aa4c-633df98549f0]

    D1 -->|Location: global| E1[BigQuery 数据集: thelook_bq]
    D2 -->|Location: us| E2[BigQuery 数据集: firebas_bq]

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
  拥有全局大权限，作为 **Cloud Run 容器的主运行身份**，保证代码无障碍访问 GCP 全球区与美区的所有 BQCA 资源。
* **`bqca-restricted@webeye-internal-test.iam.gserviceaccount.com`**：
  受限权限账号，作为 **角色权限隔离（一线客服 vs 运营经理）的伪装测试身份**。

---

## 4. 📝 关键决策与规则记录 (User Directives)

1. **未审核绝不盲目 Git Commit / Push**：
   * 所有本地修改保持在 Working Directory 中，方便用户在 VSCode 中检查。
2. **凭证隔离**：
   * 所有局部凭证写在 `.env` 中，Cloud Run 通过环境变量注入。
3. **数据报告 HTML 处理**：
   * 当 BQCA 返回 HTML 代码块时，后端自动提取并上传至 GCS，在飞书交互卡片中附加查看链接；去除了多余的 HTML 模板生成器。

---

## 5. 🚀 上线部署指导命令

运行以下命令将服务发布至 GCP Cloud Run，并绑定专属大权限账号 `bqca-runner`：

```bash
gcloud run deploy bqca-bot \
  --source . \
  --region asia-east1 \
  --project webeye-internal-test \
  --allow-unauthenticated \
  --service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com \
  --env-vars-file=.env
```

---

*（本总结报告由定时任务自动维持每日晚 21:00 增量更新）*
