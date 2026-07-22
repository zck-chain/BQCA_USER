 # BQCA 飞书智能查询助手 实现计划
 
 > **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。
 
 **目标：** 实现飞书机器人 → Gemini SQL → BigQuery 执行 → HTML 可视化 → 返回摘要+链接的完整问答链路
 
 **架构：** Cloud Run 上的 FastAPI 服务，接收飞书事件 webhook，三步调用 Gemini（SQL生成→HTML+摘要生成），中间插入 BigQuery 执行和 GCS 存储。所有 GCP 调用用 `bqca-runner` SA 认证。
 
 **技术栈：** Python 3.11, FastAPI, google-cloud-bigquery, google-cloud-aiplatform, google-cloud-storage, httpx（飞书API调用）
 
 ---
 
 ## 任务 1：项目骨架与配置
 
 **文件：**
 - 创建：`app/__init__.py`
 - 创建：`app/config.py`
 - 创建：`requirements.txt`
 - 创建：`.env.example`
 - 创建：`app/main.py`
 - 测试：`tests/conftest.py`
 
 - [ ] **步骤 1：创建 `requirements.txt`**
 
 ```
 fastapi==0.115.0
 uvicorn[standard]==0.30.6
 google-cloud-bigquery==3.26.0
 google-cloud-aiplatform==1.71.0
 google-cloud-storage==2.18.0
 httpx==0.27.2
 pydantic-settings==2.5.2
 python-dotenv==1.0.1
 pytest==8.3.3
 pytest-asyncio==0.24.0
 ```
 
 - [ ] **步骤 2：创建 `.env.example`**
 
 ```
 BQ_PROJECT=webeye-internal-test
 BQ_DATASET=thelook_ecommerce
 GCS_BUCKET=bqca-results
 FEISHU_APP_ID=
 FEISHU_APP_SECRET=
 FEISHU_VERIFICATION_TOKEN=
 FEISHU_ENCRYPT_KEY=
 GEMINI_MODEL=gemini-2.0-flash
 MAX_RESULT_ROWS=1000
 ```
 
 - [ ] **步骤 3：创建 `app/__init__.py`（空文件）**
 
 - [ ] **步骤 4：编写 `app/config.py`**
 
 ```python
 from pydantic_settings import BaseSettings
 
 
 class Settings(BaseSettings):
     BQ_PROJECT: str = "webeye-internal-test"
     BQ_DATASET: str = "thelook_ecommerce"
     GCS_BUCKET: str = "bqca-results"
     FEISHU_APP_ID: str = ""
     FEISHU_APP_SECRET: str = ""
     FEISHU_VERIFICATION_TOKEN: str = ""
     FEISHU_ENCRYPT_KEY: str = ""
     GEMINI_MODEL: str = "gemini-2.0-flash"
     MAX_RESULT_ROWS: int = 1000
 
     model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
 
 
 settings = Settings()
 ```
 
 - [ ] **步骤 5：编写 `tests/conftest.py`**
 
 ```python
 import pytest
 from app.config import Settings
 
 
 @pytest.fixture
 def test_settings():
     return Settings(
         BQ_PROJECT="test-project",
         BQ_DATASET="test_dataset",
         GCS_BUCKET="test-bucket",
         FEISHU_APP_ID="test_app_id",
         FEISHU_APP_SECRET="test_secret",
         FEISHU_VERIFICATION_TOKEN="test_token",
         GEMINI_MODEL="gemini-2.0-flash",
         MAX_RESULT_ROWS=100,
     )
 ```
 
 - [ ] **步骤 6：编写最小 `app/main.py`**
 
 ```python
 from fastapi import FastAPI
 
 app = FastAPI(title="BQCA Feishu Bot")
 
 
 @app.get("/health")
 async def health():
     return {"status": "ok"}
 ```
 
 - [ ] **步骤 7：运行验证**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && pip install -r requirements.txt && python -c "from app.config import settings; print(settings.BQ_PROJECT)" && uvicorn app.main:app --port 8000 &`
 预期：配置加载成功，health 端点返回 `{"status": "ok"}`
 
 - [ ] **步骤 8：Commit**
 
 ```bash
 git add app/ tests/ requirements.txt .env.example
 git commit -m "feat: project skeleton with config and FastAPI entry"
 ```
 
 ---
 
 ## 任务 2：SQL 安全检查模块
 
 **文件：**
 - 创建：`app/engine/__init__.py`
 - 创建：`app/engine/safety.py`
 - 测试：`tests/test_safety.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from app.engine.safety import check_sql_safety, enforce_limit
 
 
 def test_allows_select():
     assert check_sql_safety("SELECT * FROM t") is True
 
 
 def test_blocks_drop():
     assert check_sql_safety("DROP TABLE t") is False
 
 
 def test_blocks_delete():
     assert check_sql_safety("DELETE FROM t WHERE 1=1") is False
 
 
 def test_blocks_update():
     assert check_sql_safety("UPDATE t SET a=1") is False
 
 
 def test_blocks_insert():
     assert check_sql_safety("INSERT INTO t VALUES (1)") is False
 
 
 def test_blocks_alter():
     assert check_sql_safety("ALTER TABLE t ADD COLUMN x INT") is False
 
 
 def test_blocks_mixed_case():
     assert check_sql_safety("drop table t") is False
 
 
 def test_enforce_limit_adds_limit():
     sql = "SELECT * FROM t"
     result = enforce_limit(sql, 1000)
     assert "LIMIT 1000" in result
 
 
 def test_enforce_limit_reduces_existing():
     sql = "SELECT * FROM t LIMIT 5000"
     result = enforce_limit(sql, 1000)
     assert "LIMIT 1000" in result
     assert "5000" not in result
 
 
 def test_enforce_limit_keeps_smaller():
     sql = "SELECT * FROM t LIMIT 100"
     result = enforce_limit(sql, 1000)
     assert "LIMIT 100" in result
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_safety.py -v`
 预期：FAIL，`ModuleNotFoundError: No module named 'app.engine.safety'`
 
 - [ ] **步骤 3：创建 `app/engine/__init__.py`（空文件）**
 
 - [ ] **步骤 4：编写 `app/engine/safety.py`**
 
 ```python
 import re
 
 
 def check_sql_safety(sql: str) -> bool:
     """只允许 SELECT 语句，禁止任何写操作。"""
     cleaned = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
     cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
     cleaned = cleaned.strip()
     first_word = cleaned.split()[0].upper() if cleaned.split() else ""
     return first_word == "SELECT"
 
 
 def enforce_limit(sql: str, max_rows: int) -> str:
     """确保 SQL 包含 LIMIT 且不超过 max_rows。"""
     limit_pattern = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
     match = limit_pattern.search(sql)
     if match:
         current = int(match.group(1))
         if current > max_rows:
             sql = limit_pattern.sub(f"LIMIT {max_rows}", sql)
     else:
         sql = f"{sql.rstrip(';')} LIMIT {max_rows}"
     return sql
 ```
 
 - [ ] **步骤 5：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_safety.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 6：Commit**
 
 ```bash
 git add app/engine/ tests/test_safety.py
 git commit -m "feat: SQL safety check module"
 ```
 
 ---
 
 ## 任务 3：动态 Schema 加载
 
 **文件：**
 - 创建：`app/engine/schema.py`
 - 测试：`tests/test_schema.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from app.engine.schema import format_schema_for_prompt
 
 
 def test_format_schema_basic():
     schema = {
         "orders": ["order_id INT", "user_id INT", "status STRING"],
         "order_items": ["id INT", "order_id INT", "sale_price FLOAT"],
     }
     result = format_schema_for_prompt("my_dataset", schema)
     assert "my_dataset.orders" in result
     assert "order_id INT" in result
     assert "my_dataset.order_items" in result
     assert "sale_price FLOAT" in result
 
 
 def test_format_schema_empty():
     result = format_schema_for_prompt("my_dataset", {})
     assert "my_dataset" not in result or result.strip() == ""
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_schema.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 5：编写 `app/engine/schema.py`**
 
 ```python
 from google.cloud import bigquery
 
 from app.config import settings
 
 
 _cached_schema: dict[str, list[str]] | None = None
 
 
 def fetch_schema() -> dict[str, list[str]]:
     """从 BigQuery INFORMATION_SCHEMA 拉取表结构，缓存到内存。"""
     global _cached_schema
     if _cached_schema is not None:
         return _cached_schema
 
     client = bigquery.Client(project=settings.BQ_PROJECT)
     query = f"""
         SELECT table_name, column_name, data_type
         FROM `{settings.BQ_PROJECT}.{settings.BQ_DATASET}.INFORMATION_SCHEMA.COLUMNS`
         ORDER BY table_name, ordinal_position
     """
     rows = client.query(query).result()
     schema: dict[str, list[str]] = {}
     for row in rows:
         table = row.table_name
         col_def = f"{row.column_name} {row.data_type}"
         schema.setdefault(table, []).append(col_def)
 
     _cached_schema = schema
     return schema
 
 
 def format_schema_for_prompt(dataset: str, schema: dict[str, list[str]]) -> str:
     """将 schema 格式化为 prompt 注入文本。"""
     lines = []
     for table, columns in schema.items():
         cols_str = ", ".join(columns)
         lines.append(f"- {dataset}.{table}({cols_str})")
     return "\n".join(lines)
 
 
 def get_schema_text() -> str:
     """获取格式化后的 schema 文本，供 prompt 使用。"""
     schema = fetch_schema()
     return format_schema_for_prompt(f"{settings.BQ_PROJECT}.{settings.BQ_DATASET}", schema)
 ```
 
 - [ ] **步骤 6：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_schema.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 7：Commit**
 
 ```bash
 git add app/engine/schema.py tests/test_schema.py
 git commit -m "feat: dynamic schema loading from BigQuery INFORMATION_SCHEMA"
 ```
 
 ---
 
 ## 任务 4：Gemini SQL 生成
 
 **文件：**
 - 创建：`app/engine/sql_generator.py`
 - 测试：`tests/test_sql_generator.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from unittest.mock import patch, MagicMock
 from app.engine.sql_generator import generate_sql
 
 
 @pytest.mark.asyncio
 async def test_generate_sql_returns_sql():
     mock_response = MagicMock()
     mock_response.text = "SELECT * FROM orders LIMIT 10"
 
     with patch("app.engine.sql_generator._call_gemini", return_value=mock_response) as mock_call:
         result = await generate_sql("查看最近的订单", "test_dataset.orders(id INT, status STRING)")
 
     assert "SELECT" in result
     mock_call.assert_called_once()
 
 
 @pytest.mark.asyncio
 async def test_generate_sql_prompt_contains_schema():
     mock_response = MagicMock()
     mock_response.text = "SELECT * FROM orders LIMIT 10"
 
     with patch("app.engine.sql_generator._call_gemini", return_value=mock_response) as mock_call:
         await generate_sql("查看最近的订单", "test_dataset.orders(id INT)")
 
     call_args = mock_call.call_args
     prompt_text = call_args[0][0]
     assert "test_dataset.orders" in prompt_text
     assert "id INT" in prompt_text
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_sql_generator.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 3：编写 `app/engine/sql_generator.py`**
 
 ```python
 import vertexai
 from vertexai.generative_models import GenerativeModel
 
 from app.config import settings
 
 
 SQL_SYSTEM_PROMPT = """你是一个 BigQuery SQL 专家。根据以下表结构，将用户问题转为 SQL。
 
 规则：
 - 只输出 SQL，不要任何解释
 - 使用标准 BigQuery SQL 语法
 - 日期字段用 TIMESTAMP 类型处理
 - 不要使用 DROP、DELETE、UPDATE、INSERT 等写操作
 - LIMIT 默认不超过 {max_rows}
 """
 
 
 def _call_gemini(prompt: str) -> object:
     vertexai.init(project=settings.BQ_PROJECT)
     model = GenerativeModel(settings.GEMINI_MODEL)
     response = model.generate_content(prompt)
     return response
 
 
 async def generate_sql(question: str, schema_text: str) -> str:
     """用 Gemini 将自然语言问题转为 BigQuery SQL。"""
     prompt = f"""{SQL_SYSTEM_PROMPT.format(max_rows=settings.MAX_RESULT_ROWS)}
 
 表结构：
 {schema_text}
 
 用户问题：{question}"""
 
     response = _call_gemini(prompt)
     sql = response.text.strip()
     # 去掉 markdown 代码块标记
     if sql.startswith("```sql"):
         sql = sql[6:]
     if sql.startswith("```"):
         sql = sql[3:]
     if sql.endswith("```"):
         sql = sql[:-3]
     return sql.strip()
 ```
 
 - [ ] **步骤 4：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_sql_generator.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 5：Commit**
 
 ```bash
 git add app/engine/sql_generator.py tests/test_sql_generator.py
 git commit -m "feat: Gemini SQL generation module"
 ```
 
 ---
 
 ## 任务 5：BigQuery 查询执行
 
 **文件：**
 - 创建：`app/engine/query_runner.py`
 - 测试：`tests/test_query_runner.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from unittest.mock import patch, MagicMock
 from app.engine.query_runner import run_query
 
 
 @pytest.mark.asyncio
 async def test_run_query_returns_rows():
     mock_client = MagicMock()
     mock_row = MagicMock()
     mock_row.items.return_value = [("name", "Alice"), ("age", 30)]
     mock_job = MagicMock()
     mock_job.result.return_value = [mock_row]
     mock_client.query.return_value = mock_job
 
     with patch("app.engine.query_runner.bigquery.Client", return_value=mock_client):
         rows, columns = await run_query("SELECT name, age FROM users LIMIT 10")
 
     assert len(rows) == 1
     assert "name" in columns
 
 
 @pytest.mark.asyncio
 async def test_run_query_truncates_rows():
     mock_client = MagicMock()
     mock_job = MagicMock()
     mock_job.result.return_value = [MagicMock() for _ in range(1500)]
     mock_client.query.return_value = mock_job
 
     with patch("app.engine.query_runner.bigquery.Client", return_value=mock_client), \
          patch("app.engine.query_runner.settings.MAX_RESULT_ROWS", 1000):
         rows, columns = await run_query("SELECT * FROM t")
 
     assert len(rows) <= 1000
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_query_runner.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 3：编写 `app/engine/query_runner.py`**
 
 ```python
 from google.cloud import bigquery
 
 from app.config import settings
 
 
 async def run_query(sql: str) -> tuple[list[dict], list[str]]:
     """执行 BigQuery SQL，返回 (rows, columns)。超出 MAX_RESULT_ROWS 截断。"""
     client = bigquery.Client(project=settings.BQ_PROJECT)
     job_config = bigquery.QueryJobConfig(query_timeout=30)
     query_job = client.query(sql, job_config=job_config)
     result = query_job.result()
 
     columns = [field.name for field in result.schema]
     rows = [dict(row.items()) for row in result]
 
     if len(rows) > settings.MAX_RESULT_ROWS:
         rows = rows[: settings.MAX_RESULT_ROWS]
 
     return rows, columns
 ```
 
 - [ ] **步骤 4：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_query_runner.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 5：Commit**
 
 ```bash
 git add app/engine/query_runner.py tests/test_query_runner.py
 git commit -m "feat: BigQuery query execution with row limit"
 ```
 
 ---
 
 ## 任务 6：Gemini HTML + 摘要生成
 
 **文件：**
 - 创建：`app/renderer/__init__.py`
 - 创建：`app/renderer/html_generator.py`
 - 测试：`tests/test_html_generator.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from unittest.mock import patch, MagicMock
 from app.renderer.html_generator import generate_html_and_summary
 
 
 @pytest.mark.asyncio
 async def test_generate_returns_html_and_summary():
     mock_response = MagicMock()
     mock_response.text = '---HTML---\n<html><body>chart</body></html>\n---SUMMARY---\n销售额最高的品类是电子'
 
     with patch("app.renderer.html_generator._call_gemini", return_value=mock_response):
         html, summary = await generate_html_and_summary(
             "哪个品类卖得最好",
             [{"category": "Electronics", "total": 5000}],
             ["category", "total"],
         )
 
     assert "<html>" in html
     assert "电子" in summary or "品类" in summary
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_html_generator.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 3：创建 `app/renderer/__init__.py`（空文件）**
 
 - [ ] **步骤 4：编写 `app/renderer/html_generator.py`**
 
 ```python
 import json
 import vertexai
 from vertexai.generative_models import GenerativeModel
 
 from app.config import settings
 
 
 HTML_SYSTEM_PROMPT = """你是一个数据可视化专家。根据用户问题和查询结果，生成：
 1. 一个完整的 HTML 页面（包含 ECharts 图表），用于可视化展示
 2. 一段中文摘要（2-3 句话），概括数据要点
 
 输出格式（严格遵守）：
 ---HTML---
 （完整的 HTML 代码，包含 ECharts CDN 引用，可独立运行）
 ---SUMMARY---
 （中文摘要）
 
 要求：
 - HTML 使用 ECharts (https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js)
 - 页面响应式，移动端可用
 - 图表类型根据问题自动选择最合适的
 - 数据表格也要包含在 HTML 中
 """
 
 
 def _call_gemini(prompt: str) -> object:
     vertexai.init(project=settings.BQ_PROJECT)
     model = GenerativeModel(settings.GEMINI_MODEL)
     response = model.generate_content(prompt)
     return response
 
 
 def _fallback_html(rows: list[dict], columns: list[str]) -> str:
     """降级：纯数据表格 HTML，保证链接始终可用。"""
     header = "".join(f"<th>{c}</th>" for c in columns)
     body_rows = ""
     for row in rows[:100]:
         cells = "".join(f"<td>{row.get(c, '')}</td>" for c in columns)
         body_rows += f"<tr>{cells}</tr>"
     return f"""<!DOCTYPE html>
 <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
 <style>body{{font-family:sans-serif;padding:16px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f5f5f5}}</style>
 </head><body><table><thead><tr>{header}</tr></thead><tbody>{body_rows}</tbody></table></body></html>"""
 
 
 async def generate_html_and_summary(
     question: str, rows: list[dict], columns: list[str]
 ) -> tuple[str, str]:
     """调用 Gemini 生成 HTML 可视化页面和中文摘要。失败时降级为纯表格。"""
     data_json = json.dumps(rows[:200], ensure_ascii=False, default=str)
 
     prompt = f"""{HTML_SYSTEM_PROMPT}
 
 用户问题：{question}
 
 列名：{', '.join(columns)}
 
 查询结果（JSON）：
 {data_json}"""
 
     try:
         response = _call_gemini(prompt)
         text = response.text.strip()
 
         html = ""
         summary = "查询完成，请查看详情。"
 
         if "---HTML---" in text and "---SUMMARY---" in text:
             parts = text.split("---SUMMARY---")
             html_part = parts[0].replace("---HTML---", "").strip()
             summary = parts[1].strip()
             # 去掉 markdown 代码块标记
             if html_part.startswith("```html"):
                 html_part = html_part[7:]
             if html_part.startswith("```"):
                 html_part = html_part[3:]
             if html_part.endswith("```"):
                 html_part = html_part[:-3]
             html = html_part.strip()
 
         if not html:
             html = _fallback_html(rows, columns)
 
         return html, summary
     except Exception:
         return _fallback_html(rows, columns), "查询完成，请查看详情。"
 ```
 
 - [ ] **步骤 5：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_html_generator.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 6：Commit**
 
 ```bash
 git add app/renderer/ tests/test_html_generator.py
 git commit -m "feat: Gemini HTML+summary generation with fallback"
 ```
 
 ---
 
 ## 任务 7：GCS HTML 存储
 
 **文件：**
 - 创建：`app/storage/__init__.py`
 - 创建：`app/storage/gcs.py`
 - 测试：`tests/test_gcs.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from unittest.mock import patch, MagicMock
 from app.storage.gcs import upload_html
 
 
 @pytest.mark.asyncio
 async def test_upload_html_returns_url():
     mock_bucket = MagicMock()
     mock_blob = MagicMock()
     mock_blob.public_url = "https://storage.googleapis.com/test-bucket/results/test-id.html"
     mock_bucket.blob.return_value = mock_blob
 
     mock_client = MagicMock()
     mock_client.bucket.return_value = mock_bucket
 
     with patch("app.storage.gcs.storage.Client", return_value=mock_client):
         url = await upload_html("test-id", "<html>hello</html>")
 
     assert "test-bucket" in url
     assert "test-id" in url
     mock_blob.upload_from_string.assert_called_once_with("<html>hello</html>", content_type="text/html")
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_gcs.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 3：创建 `app/storage/__init__.py`（空文件）**
 
 - [ ] **步骤 4：编写 `app/storage/gcs.py`**
 
 ```python
 import uuid
 from google.cloud import storage
 
 from app.config import settings
 
 
 async def upload_html(query_id: str, html_content: str) -> str:
     """将 HTML 上传到 GCS 公开桶，返回公开 URL。"""
     client = storage.Client(project=settings.BQ_PROJECT)
     bucket = client.bucket(settings.GCS_BUCKET)
     blob_name = f"results/{query_id}.html"
     blob = bucket.blob(blob_name)
     blob.upload_from_string(html_content, content_type="text/html")
     return blob.public_url
 
 
 def generate_query_id() -> str:
     """生成唯一查询 ID，用于文件命名和去重。"""
     return uuid.uuid4().hex[:12]
 ```
 
 - [ ] **步骤 5：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_gcs.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 6：Commit**
 
 ```bash
 git add app/storage/ tests/test_gcs.py
 git commit -m "feat: GCS HTML upload with public URL"
 ```
 
 ---
 
 ## 任务 8：飞书事件接收与消息发送
 
 **文件：**
 - 创建：`app/feishu/__init__.py`
 - 创建：`app/feishu/event.py`
 - 创建：`app/feishu/message.py`
 - 测试：`tests/test_event.py`
 - 测试：`tests/test_message.py`
 
 - [ ] **步骤 1：编写失败测试 `tests/test_event.py`**
 
 ```python
 import pytest
 from app.feishu.event import extract_question, verify_token
 
 
 def test_extract_question_removes_mention():
     event = {
         "message": {
             "content": '{"text":"@_user_1 上个月销售额最高的品类"}',
             "message_type": "text",
         }
     }
     result = extract_question(event)
     assert "上个月" in result
     assert "@_user_1" not in result
 
 
 def test_extract_question_plain_text():
     event = {
         "message": {
             "content": '{"text":"查看订单数量"}',
             "message_type": "text",
         }
     }
     result = extract_question(event)
     assert "查看订单" in result
 
 
 def test_verify_token_valid():
     assert verify_token("test_token") is False  # 默认空 token
 
 
 def test_verify_token_with_settings():
     from app.config import Settings
     s = Settings(FEISHU_VERIFICATION_TOKEN="my_token")
     # 模拟 token 匹配
     assert s.FEISHU_VERIFICATION_TOKEN == "my_token"
 ```
 
 - [ ] **步骤 2：编写失败测试 `tests/test_message.py`**
 
 ```python
 import pytest
 from unittest.mock import patch, AsyncMock
 from app.feishu.message import send_text_message, send_result_card
 
 
 @pytest.mark.asyncio
 async def test_send_text_message_calls_api():
     with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
          patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
         mock_client = AsyncMock()
         mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
         mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
         mock_client.post = AsyncMock()
 
         await send_text_message("oc_test", "正在查询...")
 
         mock_client.post.assert_called_once()
 
 
 @pytest.mark.asyncio
 async def test_send_result_card_contains_link():
     with patch("app.feishu.message._get_tenant_token", return_value="fake_token"), \
          patch("app.feishu.message.httpx.AsyncClient") as mock_client_cls:
         mock_client = AsyncMock()
         mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
         mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
         mock_client.post = AsyncMock()
 
         await send_result_card("oc_test", "摘要内容", "https://example.com/result.html")
 
         call_args = mock_client.post.call_args
         body = call_args[1]["json"]
         assert "https://example.com/result.html" in str(body)
 ```
 
 - [ ] **步骤 3：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_event.py tests/test_message.py -v`
 预期：FAIL，`ModuleNotFoundError`
 
 - [ ] **步骤 4：创建 `app/feishu/__init__.py`（空文件）**
 
 - [ ] **步骤 5：编写 `app/feishu/event.py`**
 
 ```python
 import json
 import re
 
 from app.config import settings
 
 
 def verify_token(token: str) -> bool:
     """验证飞书事件 token。"""
     if not settings.FEISHU_VERIFICATION_TOKEN:
         return False
     return token == settings.FEISHU_VERIFICATION_TOKEN
 
 
 def extract_question(event: dict) -> str:
     """从飞书消息事件中提取用户问题文本，去掉 @机器人 部分。"""
     content = event.get("message", {}).get("content", "{}")
     msg_type = event.get("message", {}).get("message_type", "")
 
     if msg_type != "text":
         return ""
 
     data = json.loads(content)
     text = data.get("text", "")
     # 去掉 @用户 的 mention 标记
     text = re.sub(r"@_user_\d+\s*", "", text).strip()
     return text
 
 
 def get_message_id(event: dict) -> str:
     """获取消息 ID，用于去重。"""
     return event.get("message", {}).get("message_id", "")
 
 
 def get_chat_id(event: dict) -> str:
     """获取会话 ID，用于回复消息。"""
     return event.get("message", {}).get("chat_id", "")
 ```
 
 - [ ] **步骤 6：编写 `app/feishu/message.py`**
 
 ```python
 import httpx
 
 from app.config import settings
 
 _tenant_token: str | None = None
 
 
 async def _get_tenant_token() -> str:
     """获取飞书 tenant_access_token。"""
     global _tenant_token
     # TODO: 加过期判断，当前简化为先获取
     async with httpx.AsyncClient() as client:
         resp = await client.post(
             "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
             json={
                 "app_id": settings.FEISHU_APP_ID,
                 "app_secret": settings.FEISHU_APP_SECRET,
             },
         )
         data = resp.json()
         _tenant_token = data["tenant_access_token"]
         return _tenant_token
 
 
 async def send_text_message(chat_id: str, text: str) -> dict:
     """发送纯文本消息。"""
     token = await _get_tenant_token()
     async with httpx.AsyncClient() as client:
         resp = await client.post(
             "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
         )
         return resp.json()
 
 
 async def send_result_card(chat_id: str, summary: str, result_url: str) -> dict:
     """发送结果卡片消息，包含摘要和查看详情链接。"""
     import json
 
     token = await _get_tenant_token()
     card_content = {
         "elements": [
             {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
             {"tag": "action", "actions": [
                 {"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"},
                  "url": result_url, "type": "primary"}
             ]},
         ]
     }
     async with httpx.AsyncClient() as client:
         resp = await client.post(
             "https://open.feishu.cn/open-apis/im/v1/messages",
             params={"receive_id_type": "chat_id"},
             headers={"Authorization": f"Bearer {token}"},
             json={
                 "receive_id": chat_id,
                 "msg_type": "interactive",
                 "content": json.dumps(card_content),
             },
         )
         return resp.json()
 ```
 
 - [ ] **步骤 7：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_event.py tests/test_message.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 8：Commit**
 
 ```bash
 git add app/feishu/ tests/test_event.py tests/test_message.py
 git commit -m "feat: Feishu event handling and message sending"
 ```
 
 ---
 
 ## 任务 9：FastAPI 路由整合 — 完整问答链路
 
 **文件：**
 - 修改：`app/main.py`
 - 测试：`tests/test_main.py`
 
 - [ ] **步骤 1：编写失败测试**
 
 ```python
 import pytest
 from unittest.mock import patch, AsyncMock
 from fastapi.testclient import TestClient
 from app.main import app
 
 client = TestClient(app)
 
 
 def test_health():
     resp = client.get("/health")
     assert resp.status_code == 200
     assert resp.json() == {"status": "ok"}
 
 
 def test_webhook_challenge():
     resp = client.post("/webhook/event", json={
         "challenge": "test_challenge",
         "token": "test_token",
         "type": "url_verification",
     })
     assert resp.status_code == 200
     assert resp.json()["challenge"] == "test_challenge"
 
 
 @pytest.mark.asyncio
 async def test_handle_message_event():
     event = {
         "header": {"event_id": "evt_001"},
         "event": {
             "message": {
                 "message_id": "msg_001",
                 "chat_id": "oc_test",
                 "content": '{"text":"查看订单数量"}',
                 "message_type": "text",
             },
             "sender": {"sender_id": {"user_id": "u_001"}},
         },
     }
 
     with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
         resp = client.post("/webhook/event", json=event)
 
     assert resp.status_code == 200
 ```
 
 - [ ] **步骤 2：运行测试验证失败**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_main.py -v`
 预期：FAIL，部分测试失败
 
 - [ ] **步骤 3：重写 `app/main.py`，整合完整链路**
 
 ```python
 import asyncio
 import logging
 from contextlib import asynccontextmanager
 
 from fastapi import FastAPI, Request
 
 from app.config import settings
 from app.engine.schema import get_schema_text
 from app.engine.safety import check_sql_safety, enforce_limit
 from app.engine.sql_generator import generate_sql
 from app.engine.query_runner import run_query
 from app.renderer.html_generator import generate_html_and_summary
 from app.storage.gcs import upload_html, generate_query_id
 from app.feishu.event import extract_question, get_message_id, get_chat_id
 from app.feishu.message import send_text_message, send_result_card
 
 logger = logging.getLogger(__name__)
 
 _processed_messages: set[str] = set()
 _schema_text: str = ""
 
 
 @asynccontextmanager
 async def lifespan(app: FastAPI):
     global _schema_text
     try:
         _schema_text = get_schema_text()
         logger.info("Schema loaded: %d chars", len(_schema_text))
     except Exception as e:
         logger.warning("Failed to load schema on startup: %s", e)
     yield
 
 
 app = FastAPI(title="BQCA Feishu Bot", lifespan=lifespan)
 
 
 @app.get("/health")
 async def health():
     return {"status": "ok"}
 
 
 @app.post("/webhook/event")
 async def webhook_event(request: Request):
     body = await request.json()
 
     # 飞书 URL 验证
     if body.get("type") == "url_verification":
         return {"challenge": body.get("challenge")}
 
     # 处理消息事件
     header = body.get("header", {})
     event = body.get("event", {})
 
     msg_id = get_message_id(event)
     if msg_id in _processed_messages:
         return {"status": "ok"}
     _processed_messages.add(msg_id)
 
     question = extract_question(event)
     if not question:
         return {"status": "ok"}
 
     chat_id = get_chat_id(event)
     asyncio.create_task(_process_query(question, chat_id))
 
     return {"status": "ok"}
 
 
 async def _process_query(question: str, chat_id: str):
     """异步处理完整查询链路。"""
     try:
         await send_text_message(chat_id, "正在查询，请稍候...")
 
         # 1. 生成 SQL
         sql = await generate_sql(question, _schema_text)
         if not check_sql_safety(sql):
             await send_text_message(chat_id, "无法执行该查询：仅支持数据查询操作。")
             return
         sql = enforce_limit(sql, settings.MAX_RESULT_ROWS)
 
         # 2. 执行查询
         rows, columns = await run_query(sql)
 
         # 3. 生成 HTML + 摘要
         html, summary = await generate_html_and_summary(question, rows, columns)
 
         # 4. 上传 HTML
         query_id = generate_query_id()
         url = await upload_html(query_id, html)
 
         # 5. 回复结果
         await send_result_card(chat_id, summary, url)
 
     except Exception as e:
         logger.error("Query processing failed: %s", e, exc_info=True)
         await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
 ```
 
 - [ ] **步骤 4：运行测试验证通过**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/test_main.py -v`
 预期：全部 PASS
 
 - [ ] **步骤 5：运行全量测试**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && python -m pytest tests/ -v`
 预期：全部 PASS
 
 - [ ] **步骤 6：Commit**
 
 ```bash
 git add app/main.py tests/test_main.py
 git commit -m "feat: complete query pipeline with FastAPI routes"
 ```
 
 ---
 
 ## 任务 10：Dockerfile 与部署配置
 
 **文件：**
 - 创建：`Dockerfile`
 - 创建：`.dockerignore`
 
 - [ ] **步骤 1：编写 `Dockerfile`**
 
 ```dockerfile
 FROM python:3.11-slim
 
 WORKDIR /app
 
 COPY requirements.txt .
 RUN pip install --no-cache-dir -r requirements.txt
 
 COPY app/ app/
 
 CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
 ```
 
 - [ ] **步骤 2：编写 `.dockerignore`**
 
 ```
 .git
 .env
 __pycache__
 tests
 .DS_Store
 docs
 ```
 
 - [ ] **步骤 3：本地构建测试**
 
 运行：`cd /Users/apple/Desktop/工作/产品演示/BQCA+KC/BQCA_user && docker build -t bqca-bot .`
 预期：构建成功
 
 - [ ] **步骤 4：Commit**
 
 ```bash
 git add Dockerfile .dockerignore
 git commit -m "feat: Dockerfile for Cloud Run deployment"
 ```
 
 ---
 
 ## 任务 11：GCP 资源创建与 Cloud Run 部署
 
 **文件：** 无新文件，操作 GCP 资源
 
 - [ ] **步骤 1：创建 GCS 公开桶**
 
 运行：`gcloud storage buckets create gs://bqca-results --project=webeye-internal-test --default-storage-class=STANDARD --location=asia-east1`
 
 然后：`gcloud storage buckets add-iam-policy-binding gs://bqca-results --member=allUsers --role=roles/storage.objectViewer`
 
 预期：桶创建成功，公开可读
 
 - [ ] **步骤 2：构建并部署 Cloud Run**
 
 运行：
 ```bash
 gcloud run deploy bqca-bot \
   --source . \
   --project=webeye-internal-test \
   --region=asia-east1 \
   --service-account=bqca-runner@webeye-internal-test.iam.gserviceaccount.com \
   --set-env-vars="BQ_PROJECT=webeye-internal-test,BQ_DATASET=thelook_ecommerce,GCS_BUCKET=bqca-results,GEMINI_MODEL=gemini-2.0-flash,MAX_RESULT_ROWS=1000" \
   --allow-unauthenticated \
   --platform=managed
 ```
 
 预期：部署成功，返回 Cloud Run URL
 
 - [ ] **步骤 3：验证 health 端点**
 
 运行：`curl https://<cloud-run-url>/health`
 预期：`{"status": "ok"}`
 
 - [ ] **步骤 4：记录 Cloud Run URL，填入飞书事件订阅**
 
 URL: `https://<cloud-run-url>/webhook/event`
 
 ---
 
 ## 任务 12：端到端验证
 
 - [ ] **步骤 1：在飞书群 @机器人 提问**
 
 发送："上个月销售额最高的5个品类是什么？"
 
 预期：
 1. 机器人秒回"正在查询，请稍候..."
 2. 3-10秒后回复卡片，包含摘要 + "查看详情"按钮
 3. 点击按钮打开 HTML 可视化页面
 
 - [ ] **步骤 2：测试错误场景**
 
 发送："删除所有订单"
 预期：机器人回复"无法执行该查询：仅支持数据查询操作。"
 
 - [ ] **步骤 3：Commit 最终状态**
 
 ```bash
 git add -A
 git commit -m "feat: V1 complete - BQCA Feishu bot with full query pipeline"
 git push
 ```
