import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_feishu_demo_sessions():
  from app import main

  sessions = getattr(main, "_feishu_role_sessions", None)
  if sessions is not None:
      sessions.clear()
  conversations = getattr(main, "_feishu_conversations", None)
  if conversations is not None:
      conversations.clear()
  yield
  if sessions is not None:
      sessions.clear()
  if conversations is not None:
      conversations.clear()


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


def test_feishu_query_requires_role_for_new_session():
  resp = client.post("/api/query", json={"question": "商品状态分布"})

  assert resp.status_code == 400
  assert resp.json()["detail"] == "role is required for a new session"


def test_feishu_query_sets_role_without_calling_bqca():
  with patch("app.main.chat") as mock_chat:
      resp = client.post("/api/query", json={"role": "客服"})

  assert resp.status_code == 200
  assert resp.json()["role"] == "一线客服"
  assert resp.json()["session_id"]
  assert resp.json()["message"] == "已切换为一线客服"
  mock_chat.assert_not_called()


def test_feishu_query_reuses_session_role_and_allows_switching():
  support_result = MagicMock(
      summary="客服结果",
      sql="SELECT 1",
      fields=["订单状态"],
      rows=[{"订单状态": "Shipped"}],
      vega_config=None,
      conversation_name="conversations/support-1",
  )
  manager_result = MagicMock(
      summary="经理结果",
      sql="SELECT 2",
      fields=["订单状态"],
      rows=[{"订单状态": "Complete"}],
      vega_config=None,
      conversation_name="conversations/manager-1",
  )

  session_resp = client.post("/api/query", json={"role": "一线客服"})
  session_id = session_resp.json()["session_id"]

  with patch("app.main._service_account_for_role", create=True,
             side_effect=["support-test-sa", None]) as mock_service_account, \
       patch("app.main.chat", side_effect=[support_result, manager_result]) as mock_chat:
      first_query = client.post("/api/query", json={
          "question": "商品状态分布",
          "session_id": session_id,
      })
      switch_resp = client.post("/api/query", json={
          "role": "经理",
          "session_id": session_id,
      })
      second_query = client.post("/api/query", json={
          "question": "查看已完成订单",
          "session_id": session_id,
          "conversation_id": first_query.json()["conversation_id"],
      })

  assert first_query.status_code == 200
  assert first_query.json()["role"] == "一线客服"
  assert switch_resp.status_code == 200
  assert switch_resp.json()["role"] == "运营经理"
  assert second_query.status_code == 200
  assert second_query.json()["role"] == "运营经理"
  assert mock_service_account.call_args_list == [
      call("一线客服"), call("运营经理")
  ]
  assert mock_chat.call_args_list[0].args == ("商品状态分布", None)
  assert mock_chat.call_args_list[0].kwargs == {
      "target_service_account": "support-test-sa",
      "agent_id": "ecommerce-analyst-cn",
      "location": "global",
  }
  assert mock_chat.call_args_list[1].args == ("查看已完成订单", None)
  assert mock_chat.call_args_list[1].kwargs == {
      "target_service_account": None,
      "agent_id": "ecommerce-analyst-cn",
      "location": "global",
  }


def test_game_agent_routing():
    game_result = MagicMock(
        summary="游戏活跃度正常",
        sql="SELECT 1",
        fields=["玩家数"],
        rows=[{"玩家数": 1000}],
        vega_config=None,
        conversation_name="conversations/game-1",
    )
    with patch("app.main.chat", return_value=game_result) as mock_chat:
        resp = client.post("/api/query", json={
            "question": "游戏玩家活跃度",
            "role": "运营经理",
            "domain": "game",
        })

    assert resp.status_code == 200
    assert mock_chat.call_args_list[0].kwargs["agent_id"] == "game-analyst-cn"
    assert mock_chat.call_args_list[0].kwargs["location"] == "global"

