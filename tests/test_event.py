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


def test_verify_token_with_settings():
  from app.config import Settings
  s = Settings(FEISHU_VERIFICATION_TOKEN="my_token")
  assert s.FEISHU_VERIFICATION_TOKEN == "my_token"
