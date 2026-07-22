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
    vertexai.init(project=settings.BQ_PROJECT, location=settings.VERTEX_LOCATION)
    model = GenerativeModel(settings.GEMINI_MODEL)
    response = model.generate_content(prompt)
    return response


async def generate_sql(question: str, schema_text: str) -> str:
    """Use Gemini to convert natural language question to BigQuery SQL."""
    prompt = f"""{SQL_SYSTEM_PROMPT.format(max_rows=settings.MAX_RESULT_ROWS)}

表结构：
{schema_text}

用户问题：{question}"""

    response = _call_gemini(prompt)
    sql = response.text.strip()
    # Strip markdown code block markers
    if sql.startswith("```sql"):
        sql = sql[6:]
    if sql.startswith("```"):
        sql = sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]
    return sql.strip()
