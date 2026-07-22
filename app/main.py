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

  # Feishu URL verification
  if body.get("type") == "url_verification":
      return {"challenge": body.get("challenge")}

  # Handle message event
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
  """Async full query pipeline."""
  try:
      await send_text_message(chat_id, "正在查询，请稍候...")

      # 1. Generate SQL
      sql = await generate_sql(question, _schema_text)
      if not check_sql_safety(sql):
          await send_text_message(chat_id, "无法执行该查询：仅支持数据查询操作。")
          return
      sql = enforce_limit(sql, settings.MAX_RESULT_ROWS)

      # 2. Execute query
      rows, columns = await run_query(sql)

      # 3. Generate HTML + summary
      html, summary = await generate_html_and_summary(question, rows, columns)

      # 4. Upload HTML
      query_id = generate_query_id()
      url = await upload_html(query_id, html)

      # 5. Reply with result
      await send_result_card(chat_id, summary, url)

  except Exception as e:
      logger.error("Query processing failed: %s", e, exc_info=True)
      await send_text_message(chat_id, "查询处理失败，请稍后再试或换种说法。")
