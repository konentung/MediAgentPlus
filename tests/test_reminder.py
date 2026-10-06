"""服藥小幫手：預設時間規則、從處方建立、run-due 推播（去重 / 合併 / 封鎖略過）、回報、慢箋提醒。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from linebot.v3.messaging import FlexMessage, TextMessage

from app.domain.enums import Intent
from app.domain.reminder_rules import default_times, normalize_frequency, normalize_timing
from app.line.intents import postback_data
from app.utils.clock import TAIPEI
from tests.test_prescription import _event, _image_event


def test_default_times_rules():
    assert default_times("qd", None) == ["08:00"]
    assert default_times("bid", "pc") == ["08:30", "18:30"]
    assert default_times("tid", "ac") == ["07:30", "11:30", "17:30"]
    assert default_times("qid", None) == ["08:00", "12:00", "18:00", "22:00"]
    assert default_times("q12h", None) == ["08:00", "20:00"]
    assert default_times("q8h", None) == ["08:00", "16:00", "00:00"]
    assert default_times("hs", None) == ["22:00"]
    assert default_times("qd", "hs") == ["22:00"]
    assert default_times("prn", None) == [] and default_times("other", None) == []


def test_normalize_codes():
    assert normalize_frequency("BID") == "bid"
    assert normalize_frequency("b.i.d.") == "bid"
    assert normalize_frequency("每日三次") == "tid"
    assert normalize_frequency("睡前") == "hs"
    assert normalize_frequency("怪字") == "other"
    assert normalize_timing("PC") == "pc" and normalize_timing("飯前") == "ac"
    assert normalize_timing(None) is None and normalize_timing("?") is None


def _principal(container, line_user_id="U1"):
    user = container.user_repo.get_by_line_user_id(line_user_id)
    return container.users.to_principal(user)


def _create_prescription(post_line_event, container, user_id="U1"):
    post_line_event(
        _event("postback", user_id=user_id, postback={"data": postback_data(Intent.MEDICATION)})
    )
    post_line_event(_image_event(user_id=user_id, message_id=f"img-{user_id}"))
    user = container.user_repo.get_by_line_user_id(user_id)
    return container.prescription_repo.list_for_user(user.id)[0]["id"]


def test_propose_and_create_from_prescription(client, container, auth_header, post_line_event):
    pid = _create_prescription(post_line_event, container)
    user = container.user_repo.get_by_line_user_id("U1")
    headers = auth_header(user)

    resp = client.post(f"/api/v1/liff/prescriptions/{pid}/schedules/propose", headers=headers)
    assert resp.status_code == 200
    drafts = resp.get_json()["data"]["drafts"]
    assert drafts[0]["times"] == ["08:30", "18:30"]  # bid + pc
    assert drafts[1]["times"] == ["22:00"]  # hs
    assert drafts[0]["end_date"] is not None and drafts[0]["prescription_id"] == pid

    resp = client.post("/api/v1/liff/reminders", json={"schedules": drafts}, headers=headers)
    assert resp.status_code == 201, resp.get_json()
    created = resp.get_json()["data"]
    assert len(created) == 2 and created[0]["drug_name"].startswith("Metformin")

    listing = client.get("/api/v1/liff/reminders", headers=headers).get_json()["data"]
    assert len(listing["schedules"]) == 2 and listing["schedules"][0]["status"] == "active"

    # 驗證失敗：時間格式、缺時間
    bad = client.post(
        "/api/v1/liff/reminders",
        json={"schedules": [{"drug_name": "X", "frequency_code": "qd", "times": ["25:00"]}]},
        headers=headers,
    )
    assert bad.status_code == 422
    bad = client.post(
        "/api/v1/liff/reminders",
        json={"schedules": [{"drug_name": "X", "frequency_code": "qd", "times": []}]},
        headers=headers,
    )
    assert bad.status_code == 422

    # 修改 / 暫停 / 刪除
    sid = created[0]["id"]
    resp = client.patch(
        f"/api/v1/liff/reminders/{sid}", json={"times": ["09:00", "21:00"]}, headers=headers
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["times"] == ["09:00", "21:00"]
    resp = client.patch(
        f"/api/v1/liff/reminders/{sid}",
        json={"paused_until": (datetime.now(TAIPEI).date() + timedelta(days=3)).isoformat()},
        headers=headers,
    )
    assert resp.status_code == 200
    assert (
        client.get("/api/v1/liff/reminders", headers=headers).get_json()["data"]["schedules"][1][
            "status"
        ]
        == "paused"
    )
    assert client.delete(f"/api/v1/liff/reminders/{sid}", headers=headers).status_code == 200
    # 別人碰不到
    other_headers = auth_header(container.user_repo.save(_new_user(container)))
    assert (
        client.delete(
            f"/api/v1/liff/reminders/{created[1]['id']}", headers=other_headers
        ).status_code
        == 404
    )


def _new_user(container):
    from app.repositories.user_repository import UserRecord

    return UserRecord(id=UserRecord.new_id(), line_user_id="U-other")


def _schedule(container, line_user_id="U1", **overrides):
    if container.user_repo.get_by_line_user_id(line_user_id) is None:
        container.users.get_or_create_by_line(line_user_id)
    principal = _principal(container, line_user_id)
    draft = {
        "drug_name": "Metformin",
        "dose_text": "1 顆",
        "frequency_code": "bid",
        "timing": "pc",
        "times": ["08:30", "18:30"],
        "start_date": "2026-09-01",
        "end_date": None,
    }
    draft.update(overrides)
    return container.reminders.create_many(principal, [draft])[0]


def test_run_due_sends_once_and_merges_same_user(container):
    _schedule(container, drug_name="Metformin")
    _schedule(container, drug_name="Amlodipine", times=["08:30"])
    _schedule(container, drug_name="Later", times=["12:00"])
    now = datetime(2026, 9, 28, 8, 35, tzinfo=TAIPEI)

    summary = container.reminders.run_due(now)
    assert summary["sent"] == 2 and summary["skipped"] == 0
    pushes = container.line_client.pushes
    assert len(pushes) == 1  # 同一人同一時段兩顆藥合併成一則
    to, messages = pushes[0]
    assert to == "U1" and isinstance(messages[0], FlexMessage)
    assert "Metformin" in messages[0].alt_text and "Amlodipine" in messages[0].alt_text

    # 5 分鐘後再跑：同一時段不重送
    assert container.reminders.run_due(now + timedelta(minutes=5))["sent"] == 0
    assert len(container.line_client.pushes) == 1
    # 超出視窗（15 分鐘）也不補送
    assert container.reminders.run_due(now + timedelta(hours=1))["sent"] == 0


def test_run_due_skips_unfollowed_and_marks_failed(container, post_line_event):
    schedule = _schedule(container)
    now = datetime(2026, 9, 28, 18, 31, tzinfo=TAIPEI)
    user = container.user_repo.get_by_line_user_id("U1")

    # 使用者封鎖 → push_enabled=False → 略過
    post_line_event(_event("unfollow"))
    assert container.user_repo.get_by_id(user.id).push_enabled is False
    assert container.reminders.run_due(now)["skipped"] == 1
    assert container.line_client.pushes == []

    # 重新加好友 → 恢復
    post_line_event(_event("follow", follow={"isUnblocked": True}))
    assert container.user_repo.get_by_id(user.id).push_enabled is True

    container.line_client.fail_push = True
    later = now + timedelta(days=1)
    assert container.reminders.run_due(later)["failed"] == 1
    rows = container.delivery_repo.list_for_schedules(
        [schedule["id"]], since=now - timedelta(days=1)
    )
    assert {r["status"] for r in rows} == {"skipped", "failed"}


def test_respond_via_postback(container, post_line_event):
    schedule = _schedule(container)
    # 用今天的 08:31：最後的 history(days=7) 以真實時間往回算，固定日期過了七天就查不到
    now = datetime.now(TAIPEI).replace(hour=8, minute=31, second=0, microsecond=0)
    container.reminders.run_due(now)
    delivery = container.delivery_repo.list_for_schedules(
        [schedule["id"]], since=now - timedelta(hours=1)
    )[0]
    assert delivery["status"] == "sent"

    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(Intent.REMINDER, action="snooze", delivery_id=delivery["id"])
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "30 分鐘" in messages[0].text
    assert container.delivery_repo.get(delivery["id"])["status"] == "snoozed"

    # respond() 以真實時間算 snooze_until，測試改回固定的 now + 30 分鐘；之後 run-due 會再送一次
    container.delivery_repo.update(
        delivery["id"], snooze_until=(now + timedelta(minutes=30)).astimezone(UTC)
    )
    assert container.reminders.run_due(now + timedelta(minutes=31))["sent"] == 1
    assert len(container.line_client.pushes) == 2

    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(Intent.REMINDER, action="taken", delivery_id=delivery["id"])
            },
        )
    )
    assert container.delivery_repo.get(delivery["id"])["status"] == "taken"
    assert "已記錄" in container.line_client.replies[-1][1][0].text

    # 別人的提醒不能回報
    post_line_event(
        _event(
            "postback",
            user_id="U2",
            postback={
                "data": postback_data(Intent.REMINDER, action="taken", delivery_id=delivery["id"])
            },
        )
    )
    assert "找不到" in container.line_client.replies[-1][1][0].text

    # 歷史紀錄
    principal = _principal(container)
    history = container.reminders.history(principal, days=7)
    assert history and history[0]["status"] == "taken" and history[0]["drug_name"] == "Metformin"


def test_reminder_menu_opens_liff_directly(post_line_event, container):
    """選單的「查看 / 編輯提醒」直接開 LIFF 提醒頁，不經過 postback 再回一張 Flex。"""
    post_line_event(_event("postback", postback={"data": postback_data(Intent.REMINDER)}))
    _, messages = container.line_client.replies[-1]
    actions = [i.action for i in messages[0].quick_reply.items]
    assert [a.label for a in actions] == ["查看 / 編輯提醒", "拍處方箋建立提醒"]
    assert actions[0].type == "uri" and actions[0].uri.endswith("/reminders")


def test_reminder_menu_and_list_via_line(post_line_event, container):
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.REMINDER, action="list")})
    )
    _, messages = container.line_client.replies[-1]
    assert (
        isinstance(messages[0], FlexMessage)
        and "還沒有" in messages[0].contents.to_dict()["header"]["contents"][0]["text"]
    )
    _schedule(container)
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.REMINDER, action="list")})
    )
    _, messages = container.line_client.replies[-1]
    assert "Metformin" in str(messages[0].contents.to_dict())


def test_chronic_refill_reminder(container, post_line_event):
    pid = _create_prescription(post_line_event, container)
    principal = _principal(container)
    container.prescriptions.confirm(principal, pid)
    assert container.prescription_repo.get(pid)["refill_reminder_on"] == "2026-10-15"

    before = datetime(2026, 10, 14, 9, 0, tzinfo=TAIPEI)
    assert container.reminders.run_due(before)["refill_sent"] == 0
    due = datetime(2026, 10, 15, 9, 0, tzinfo=TAIPEI)
    assert container.reminders.run_due(due)["refill_sent"] == 1
    to, messages = container.line_client.pushes[-1]
    assert to == "U1" and "慢箋" in messages[0].alt_text
    # 只提醒一次
    assert container.reminders.run_due(due + timedelta(hours=1))["refill_sent"] == 0
    assert container.prescription_repo.get(pid)["refill_reminded_at"] is not None
