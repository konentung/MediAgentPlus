import json

from linebot.v3.messaging import FlexMessage, TextMessage

from app.domain.enums import BindingStatus, Intent, Role
from app.line.intents import IntentRequest, postback_data


def _event(event_type: str, user_id: str = "U1", **extra) -> dict:
    return {
        "type": event_type,
        "mode": "active",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "webhookEventId": "01",
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "rt",
        **extra,
    }


def test_rejects_invalid_signature(client):
    body = json.dumps({"destination": "x", "events": []})
    resp = client.post("/line/webhook", data=body, headers={"X-Line-Signature": "bad"})
    assert resp.status_code == 400


def test_follow_sends_onboarding(post_line_event, container):
    assert post_line_event(_event("follow", follow={"isUnblocked": False})).status_code == 200
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and isinstance(messages[1], TextMessage)
    assert "119" in messages[1].text
    assert isinstance(messages[2], FlexMessage)
    assert container.user_repo.get_by_line_user_id("U1") is not None


def test_text_goes_through_ai_then_module(post_line_event, container):
    """文字一律先過 AI 助理（FakeLlmClient）判斷事件，再套用模組；關鍵字只剩 AI 失敗時的退路。"""
    post_line_event(
        _event(
            "message", message={"type": "text", "id": "m1", "text": "我頭痛兩天", "quoteToken": "q"}
        )
    )
    assert container.llm_client.calls
    _, messages = container.line_client.replies[-1]
    assert "家醫科" in messages[0].text


def test_emergency_keyword(post_line_event, container):
    post_line_event(
        _event("message", message={"type": "text", "id": "m1", "text": "我胸痛", "quoteToken": "q"})
    )
    _, messages = container.line_client.replies[-1]
    assert "119" in messages[0].text


def test_reminder_available_without_binding(post_line_event, container):
    """服藥提醒開放給所有人，不需先填基本資料。"""
    post_line_event(_event("postback", postback={"data": postback_data(Intent.REMINDER)}))
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "服藥小幫手" in messages[0].text


def test_reminder_available_when_verified(post_line_event, container):
    post_line_event(_event("follow", user_id="U2", follow={"isUnblocked": False}))
    user = container.user_repo.get_by_line_user_id("U2")
    container.user_repo.update(user.id, role=Role.PATIENT, binding_status=BindingStatus.VERIFIED)
    post_line_event(
        _event("postback", user_id="U2", postback={"data": postback_data(Intent.REMINDER)})
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "服藥小幫手" in messages[0].text


def test_unknown_text_falls_back_when_ai_unavailable(post_line_event, container):
    container.conversation.enabled = False
    post_line_event(
        _event("message", message={"type": "text", "id": "m1", "text": "哈囉", "quoteToken": "q"})
    )
    _, messages = container.line_client.replies[-1]
    assert "選單" in messages[0].text


def test_intent_request_parsing():
    assert IntentRequest.from_postback("intent=reminder&action=create").params == {
        "action": "create"
    }
    assert IntentRequest.from_postback("foo=bar") is None
    assert IntentRequest.from_text("找醫院").intent is Intent.FIND_HOSPITAL
