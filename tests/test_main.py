import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch
from fastapi.testclient import TestClient
from app.main import BQCAAgentConfig, _process_query, app
from app.bqca.client import BQCAEvent, BQCAEventType
from app.storage import sqlite as sqlite_storage

client = TestClient(app)


def _final_stream(result):
    """Return an async function usable as a chat_stream_events replacement yielding one FINAL event."""
    async def _gen(*args, **kwargs):
        yield BQCAEvent(event_type=BQCAEventType.FINAL, data=getattr(result, "summary", ""), result=result)
    return _gen


def _stream_chain(results):
    """Return an async function whose successive calls each yield one FINAL for the given results."""
    it = iter(results)

    async def _gen(*args, **kwargs):
        result = next(it)
        yield BQCAEvent(event_type=BQCAEventType.FINAL, data=getattr(result, "summary", ""), result=result)
    return _gen


def _stream_with_events(events):
    """Return an async chat_stream_events replacement yielding the given BQCAEvents then a FINAL."""
    async def _gen(*args, **kwargs):
        for e in events:
            yield e
    return _gen


@pytest.fixture(autouse=True)
def isolated_sqlite_db(tmp_path, monkeypatch):
  db_path = tmp_path / "bqca_sessions.db"
  monkeypatch.setattr(sqlite_storage, "DB_PATH", str(db_path))
  sqlite_storage.init_db()
  yield


@pytest.fixture(autouse=True)
def _disable_feishu_verification(monkeypatch):
  """Default webhook tests to auth-disabled (no token configured).

  The real .env sets verification tokens; without this, every synthetic webhook
  POST would be rejected with 401. Auth-specific tests re-enable a known token.
  """
  from app.config import settings as app_settings
  monkeypatch.setattr(app_settings, "FEISHU_VERIFICATION_TOKEN", "")
  monkeypatch.setattr(app_settings, "GAME_FEISHU_VERIFICATION_TOKEN", "")
  monkeypatch.setattr(app_settings, "GAME_FEISHU_APP_ID", "game-app")
  yield


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


def test_webhook_rejects_event_with_wrong_token(monkeypatch):
  from app.config import settings as app_settings
  monkeypatch.setattr(app_settings, "FEISHU_VERIFICATION_TOKEN", "secret-token")

  event = {
      "header": {"event_id": "evt_bad", "token": "wrong-token"},
      "event": {
          "message": {
              "message_id": "msg_bad",
              "chat_id": "oc_test",
              "chat_type": "p2p",
              "content": '{"text":"查看订单"}',
              "message_type": "text",
          },
          "sender": {"sender_id": {"open_id": "ou_1"}},
      },
  }
  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      resp = client.post("/webhook/event", json=event)
  assert resp.status_code == 401
  mock_process.assert_not_awaited()


def test_webhook_accepts_event_with_correct_v2_header_token(monkeypatch):
  from app.config import settings as app_settings
  monkeypatch.setattr(app_settings, "FEISHU_VERIFICATION_TOKEN", "secret-token")

  event = {
      "header": {"event_id": "evt_ok", "token": "secret-token"},
      "event": {
          "message": {
              "message_id": "msg_ok",
              "chat_id": "oc_test",
              "chat_type": "p2p",
              "content": '{"text":"查看订单"}',
              "message_type": "text",
          },
          "sender": {"sender_id": {"open_id": "ou_1"}},
      },
  }
  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      resp = client.post("/webhook/event", json=event)
  assert resp.status_code == 200
  mock_process.assert_awaited_once()


def test_webhook_uses_game_token_for_game_app(monkeypatch):
  from app.config import settings as app_settings
  monkeypatch.setattr(app_settings, "FEISHU_VERIFICATION_TOKEN", "ecom-token")
  monkeypatch.setattr(app_settings, "GAME_FEISHU_VERIFICATION_TOKEN", "game-token")
  monkeypatch.setattr(app_settings, "GAME_FEISHU_APP_ID", "game-app")

  # Event tagged with the game app_id but carrying the ecommerce token must be rejected.
  event = {
      "header": {
          "event_id": "evt_game",
          "token": "ecom-token",
          "app_id": "game-app",
      },
      "event": {
          "app_id": "game-app",
          "message": {
              "message_id": "msg_game",
              "chat_id": "oc_game",
              "chat_type": "p2p",
              "content": '{"text":"DAU"}',
              "message_type": "text",
          },
          "sender": {"sender_id": {"open_id": "ou_g"}},
      },
  }
  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      resp = client.post("/webhook/event", json=event)
  assert resp.status_code == 401
  mock_process.assert_not_awaited()

  # ...and accepted with the game token.
  event["header"]["token"] = "game-token"
  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      resp = client.post("/webhook/event", json=event)
  assert resp.status_code == 200
  mock_process.assert_awaited_once()


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


def _message_event(event_id: str, message_id: str, create_time: str | None = None) -> dict:
  message = {
      "message_id": message_id,
      "chat_id": "oc_test",
      "chat_type": "p2p",
      "content": '{"text":"查看订单数量"}',
      "message_type": "text",
  }
  if create_time is not None:
      message["create_time"] = create_time

  return {
      "header": {"event_id": event_id},
      "event": {
          "message": message,
          "sender": {"sender_id": {"open_id": "ou_001"}},
      },
  }


def test_webhook_deduplicates_by_event_id_when_message_id_changes():
  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      first = client.post("/webhook/event", json=_message_event("evt_same", "msg_001"))
      second = client.post("/webhook/event", json=_message_event("evt_same", "msg_002"))

  assert first.status_code == 200
  assert second.status_code == 200
  mock_process.assert_awaited_once()


def test_webhook_ignores_message_older_than_ten_minutes():
  old_create_time = str(int((time.time() - 601) * 1000))

  with patch("app.main._process_query", new_callable=AsyncMock) as mock_process:
      resp = client.post(
          "/webhook/event",
          json=_message_event("evt_old", "msg_old", old_create_time),
      )

  assert resp.status_code == 200
  assert resp.json() == {"status": "ok"}
  mock_process.assert_not_awaited()


def test_feishu_query_defaults_role_for_new_session():
  result = MagicMock(
      summary="默认结果",
      sql="SELECT 1",
      fields=["status"],
      rows=[{"status": "OK"}],
      vega_config=None,
      conversation_name="conversations/default-1",
  )
  with patch("app.main.chat", return_value=result) as mock_chat:
      resp = client.post("/api/query", json={"question": "商品状态分布"})

  assert resp.status_code == 200
  assert resp.json()["role"] == "运营经理"


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


def test_game_api_query_uses_support_service_account_for_support_role():
    game_result = MagicMock(
        summary="游戏客服结果",
        sql="SELECT 1",
        fields=["玩家数"],
        rows=[{"玩家数": 1000}],
        vega_config=None,
        conversation_name="conversations/game-support-1",
    )

    with patch("app.main.settings.BQCA_SUPPORT_SERVICE_ACCOUNT", "support-test-sa"), \
         patch("app.main.chat", return_value=game_result) as mock_chat:
        resp = client.post("/api/query", json={
            "question": "游戏玩家活跃度",
            "role": "客服",
            "domain": "game",
        })

    assert resp.status_code == 200
    assert mock_chat.call_args_list[0].kwargs["target_service_account"] == "support-test-sa"


def test_switching_from_default_ecommerce_to_game_clears_conversation():
    ecommerce_result = MagicMock(
        summary="电商结果",
        sql="SELECT 1",
        fields=["商品数"],
        rows=[{"商品数": 10}],
        vega_config=None,
        conversation_name="conversations/ecommerce-1",
    )
    game_result = MagicMock(
        summary="游戏结果",
        sql="SELECT 2",
        fields=["玩家数"],
        rows=[{"玩家数": 20}],
        vega_config=None,
        conversation_name="conversations/game-1",
    )

    with patch("app.main.chat", side_effect=[ecommerce_result, game_result]) as mock_chat:
        first_resp = client.post("/api/query", json={"question": "商品状态分布"})
        second_resp = client.post("/api/query", json={
            "question": "玩家活跃度",
            "domain": "game",
            "session_id": first_resp.json()["session_id"],
        })

    assert second_resp.status_code == 200
    assert mock_chat.call_args_list[1].args == ("玩家活跃度", None)


@pytest.mark.asyncio
async def test_game_query_uses_game_fallback_questions():
    result = MagicMock(
        summary="游戏结果",
        sql="",
        fields=[],
        rows=[],
        vega_config=None,
        conversation_name="conversations/game-1",
        recommended_questions=[],
        thinking_process=[],
    )
    adapter = MagicMock()
    adapter.send_text_message = AsyncMock()
    adapter.send_initial_card = AsyncMock(return_value=None)
    adapter.send_result_card = AsyncMock()
    adapter.format_summary.return_value = ""

    with patch("app.main.get_card_adapter", return_value=adapter), \
         patch("app.main.get_agent_config", return_value=BQCAAgentConfig(
             agent_id="game-analyst-cn",
             location="global",
             display_name="Flood-It! 游戏数据洞察专家",
             domain="game",
         )), \
         patch("app.bqca.client.chat_stream_events", side_effect=_final_stream(result)), \
         patch("app.main._get_conversation", return_value=None), \
         patch("app.main._save_conversation"):
        await _process_query("分析玩家数据", "ou_test", app_id="game-app")

    fallback_questions = adapter.send_result_card.await_args.kwargs["recommended_questions"]
    assert fallback_questions == [
        "分析近期 DAU 与玩家活跃趋势",
        "查看玩家留存率变化",
        "查询失败次数最高的关卡",
    ]


@pytest.mark.asyncio
async def test_webhook_conversation_is_scoped_by_domain():
    ecommerce_result = MagicMock(
        summary="电商结果",
        sql="",
        fields=[],
        rows=[],
        vega_config=None,
        conversation_name="conversations/ecommerce-1",
        recommended_questions=[],
        thinking_process=[],
    )
    game_result = MagicMock(
        summary="游戏结果",
        sql="",
        fields=[],
        rows=[],
        vega_config=None,
        conversation_name="conversations/game-1",
        recommended_questions=[],
        thinking_process=[],
    )
    adapter = MagicMock()
    adapter.send_text_message = AsyncMock()
    adapter.send_initial_card = AsyncMock(return_value=None)
    adapter.send_result_card = AsyncMock()
    adapter.format_summary.return_value = ""

    with patch("app.main.get_card_adapter", return_value=adapter), \
         patch("app.main.settings.GAME_FEISHU_APP_ID", "game-app"), \
         patch("app.bqca.client.chat_stream_events",
               side_effect=_stream_chain([ecommerce_result, game_result])) as mock_stream, \
         patch("app.main.upload_html", new_callable=AsyncMock):
        await _process_query("商品状态分布", "oc_same_chat")
        await _process_query("玩家活跃度", "oc_same_chat", app_id="game-app")

    assert mock_stream.call_args_list[0].args == ("商品状态分布",)
    assert mock_stream.call_args_list[0].kwargs["conversation_name"] is None
    assert mock_stream.call_args_list[1].args == ("玩家活跃度",)
    assert mock_stream.call_args_list[1].kwargs["conversation_name"] is None


@pytest.mark.asyncio
async def test_streaming_loop_patches_thoughts_summary_and_final():
    result = MagicMock(
        summary="完整洞察",
        sql="SELECT 1",
        fields=["a"],
        rows=[{"a": 1}],
        vega_config=None,
        conversation_name="conversations/stream-1",
        recommended_questions=[],
        thinking_process=[],
    )

    async def _gen(*args, **kwargs):
        yield BQCAEvent(BQCAEventType.THOUGHT, data=["正在分析"], result=result)
        yield BQCAEvent(BQCAEventType.SUMMARY, data="第一段", result=result)
        # Wait beyond the 0.8s throttle so the trailing SUMMARY PATCH fires.
        await asyncio.sleep(0.9)
        yield BQCAEvent(BQCAEventType.SUMMARY, data="第一段\n第二段", result=result)
        await asyncio.sleep(0.9)
        yield BQCAEvent(BQCAEventType.FINAL, data="完整洞察", result=result)

    adapter = MagicMock()
    adapter.send_text_message = AsyncMock()
    adapter.send_initial_card = AsyncMock(return_value="om_msg")
    adapter.patch_partial_summary = AsyncMock(return_value=True)
    adapter.patch_final_card = AsyncMock(return_value=True)
    adapter.format_summary.return_value = "完整洞察"

    with patch("app.main.get_card_adapter", return_value=adapter), \
         patch("app.bqca.client.chat_stream_events", side_effect=_gen), \
         patch("app.main._get_conversation", return_value=None), \
         patch("app.main._save_conversation"), \
         patch("app.main.upload_html", new_callable=AsyncMock):
        await _process_query("分析数据", "oc_test")

    # THOUGHT produced an immediate PATCH; SUMMARY produced trailing-edge PATCHes.
    assert adapter.patch_partial_summary.await_count >= 2
    first = adapter.patch_partial_summary.await_args_list[0]
    # signature: (message_id, question, thoughts, partial_summary, stage, app_id=...)
    assert first.args[2] == ["正在分析"]
    # Final card landed and was not clobbered by a stale trailing PATCH
    # (final is the last card-mutating await).
    adapter.patch_final_card.assert_awaited_once()
    assert adapter.patch_final_card.await_args.kwargs["summary"] == "完整洞察"


@pytest.mark.asyncio
async def test_streaming_loop_cancels_trailing_summary_patch_before_final():
    """A SUMMARY right before FINAL must not PATCH after the final card lands."""
    result = MagicMock(
        summary="最终",
        sql="", fields=[], rows=[], vega_config=None,
        conversation_name="conversations/c-1",
        recommended_questions=[], thinking_process=[],
    )

    async def _gen(*args, **kwargs):
        # SUMMARY scheduled but no sleep — its trailing 0.8s PATCH must be cancelled by FINAL.
        yield BQCAEvent(BQCAEventType.SUMMARY, data="部分", result=result)
        yield BQCAEvent(BQCAEventType.FINAL, data="最终", result=result)

    adapter = MagicMock()
    adapter.send_text_message = AsyncMock()
    adapter.send_initial_card = AsyncMock(return_value="om_msg")
    adapter.patch_partial_summary = AsyncMock(return_value=True)
    adapter.patch_final_card = AsyncMock(return_value=True)
    adapter.format_summary.return_value = "最终"

    with patch("app.main.get_card_adapter", return_value=adapter), \
         patch("app.bqca.client.chat_stream_events", side_effect=_gen), \
         patch("app.main._get_conversation", return_value=None), \
         patch("app.main._save_conversation"), \
         patch("app.main.upload_html", new_callable=AsyncMock):
        await _process_query("分析数据", "oc_test")

    # No trailing partial PATCH ever fired (it was cancelled before its 0.8s delay).
    adapter.patch_partial_summary.assert_not_awaited()
    adapter.patch_final_card.assert_awaited_once()
