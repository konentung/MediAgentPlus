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


def test_discharge_department_step_lists_quick_replies(post_line_event, container):
    resp = post_line_event(
        _event("postback", postback={"data": "intent=discharge&step=department"})
    )
    assert resp.status_code == 200
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage)
    labels = [i.action.label for i in messages[0].quick_reply.items]
    assert "心臟內科" in labels and len(labels) <= 13
    assert all(len(label) <= 20 for label in labels)


def test_event_without_user_id_is_skipped(post_line_event, container):
    ev = _event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL)})
    ev["source"] = {"type": "group", "groupId": "G1"}
    before = len(container.line_client.replies)
    assert post_line_event(ev).status_code == 200
    assert len(container.line_client.replies) == before


def test_confirm_missing_prescription_replies(post_line_event, container):
    ev = _event(
        "postback",
        postback={
            "data": postback_data(Intent.MEDICATION, action="confirm", prescription_id="nope")
        },
    )
    assert post_line_event(ev).status_code == 200
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "找不到" in messages[0].text


def test_redelivered_event_is_processed_once(post_line_event, container):
    """LINE 重送（isRedelivery）同一 webhookEventId 的事件只處理一次，不會重複回覆。"""
    event = _event("postback", postback={"data": postback_data(Intent.REMINDER)})
    event["webhookEventId"] = "evt-dup"
    post_line_event(event)
    assert len(container.line_client.replies) == 1
    event["deliveryContext"] = {"isRedelivery": True}
    post_line_event(event)
    assert len(container.line_client.replies) == 1
    # 沒處理過的重送（第一次根本沒送到）照常處理
    fresh = {**event, "webhookEventId": "evt-new"}
    post_line_event(fresh)
    assert len(container.line_client.replies) == 2
