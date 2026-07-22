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


def test_handle_message_event():
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
