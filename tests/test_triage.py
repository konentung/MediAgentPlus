"""急診動態分流：規則、API 解析、快照去重、建議生命週期與通知、LINE 找醫院、後台 / 公開 API。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from linebot.v3.messaging import FlexMessage, TextMessage

from app.domain import triage_rules as rules
from app.domain.enums import Intent, Permission, Role
from app.domain.rbac import permissions_for
from app.integrations.nhi_er import parse_feed
from app.integrations.webhook import sign
from app.line.intents import postback_data
from tests.conftest import er_row, sample_er_payload
from tests.test_webhook import _event


# ---- 規則 ----
def test_congestion_level_depends_on_tier():
    assert (
        rules.congestion_level(hosp_type="1", full_119=False, wait_admit=35, wait_icu=0, wait_see=0)
        == "busy"
    )
    assert (
        rules.congestion_level(hosp_type="1", full_119=False, wait_admit=75, wait_icu=0, wait_see=0)
        == "critical"
    )
    assert (
        rules.congestion_level(hosp_type="3", full_119=False, wait_admit=7, wait_icu=0, wait_see=0)
        == "busy"
    )
    assert (
        rules.congestion_level(hosp_type="2", full_119=False, wait_admit=1, wait_icu=0, wait_see=0)
        == "normal"
    )
    assert (
        rules.congestion_level(hosp_type="2", full_119=True, wait_admit=0, wait_icu=0, wait_see=0)
        == "critical"
    )
    assert (
        rules.congestion_level(hosp_type="2", full_119=False, wait_admit=0, wait_icu=5, wait_see=0)
        == "critical"
    )
    assert (
        rules.congestion_level(hosp_type="2", full_119=False, wait_admit=0, wait_icu=3, wait_see=0)
        == "busy"
    )
    assert (
        rules.congestion_level(hosp_type="2", full_119=False, wait_admit=0, wait_icu=0, wait_see=25)
        == "busy"
    )


def test_adjacent_areas_are_symmetric():
    for code, neighbors in rules.ADJACENT_AREAS.items():
        for n in neighbors:
            assert code in rules.ADJACENT_AREAS[n]
    assert set(rules.AREA_NAMES) == set(rules.ADJACENT_AREAS)
    assert {c for _, codes in rules.AREA_GROUPS for c in codes} == set(rules.AREA_NAMES)


def test_new_permissions():
    assert Permission.TRIAGE_READ in permissions_for(Role.CS_AGENT)
    assert Permission.TRIAGE_READ in permissions_for(Role.MEDICAL_STAFF)
    assert Permission.TRIAGE_SUBSCRIPTION_MANAGE in permissions_for(Role.HOSPITAL_ADMIN)
    assert Permission.TRIAGE_SUBSCRIPTION_MANAGE not in permissions_for(Role.MEDICAL_STAFF)
    assert Permission.TRIAGE_READ not in permissions_for(Role.PATIENT)


# ---- API 解析 ----
def test_parse_feed_normalizes_fields():
    feed = parse_feed(sample_er_payload("2026/09/26 14:45"))
    assert len(feed.reports) == 4
    ntuh = feed.reports[0]
    assert ntuh.nhi_hospital_id == "0401180014" and ntuh.full_119 and ntuh.wait_admit == 80
    # 台灣時間 14:45 → UTC 06:45
    assert ntuh.reported_at == datetime(2026, 9, 26, 6, 45, tzinfo=UTC)
    # 缺 hosP_ID 的列略過、非數字視為 0
    feed = parse_feed(
        {
            "sysdate": "",
            "data": [{"hosP_NAME": "x", "areA_NO_N": "01"}, er_row("1", "a", "01", wait_see="?")],
        }
    )
    assert len(feed.reports) == 1 and feed.reports[0].wait_see == 0


# ---- 快照與看板 ----
def test_poll_stores_snapshots_once_per_report_time(container):
    triage = container.triage
    first = triage.poll()
    assert first["fetched"] == 4 and first["added"] == 4
    again = triage.poll()
    assert again["added"] == 0  # 同一回報時間不重複
    container.er_client.payload = sample_er_payload("2026-09-26T15:00:03")
    assert triage.poll()["added"] == 4
    assert container.snapshot_repo.count() == 8


def test_board_sorted_by_headroom_and_links_hospital_master(container):
    container.hospital_repo.create(
        id="0101020017",
        name="臺北市立聯合醫院仁愛院區",
        region="臺北市",
        address="臺北市大安區仁愛路四段10號",
        contact_phone="02-27093600",
        capacity_tier="regional",
    )
    board = container.triage.board(area_code="01")
    assert container.er_client.calls == 1  # 沒有快照 → 即時抓一次
    names = [h["hospital_name"] for h in board["hospitals"]]
    assert names == ["聯醫仁愛", "臺大"]
    assert (
        board["hospitals"][0]["level"] == "normal"
        and board["hospitals"][0]["phone"] == "02-27093600"
    )
    assert board["hospitals"][1]["level"] == "critical" and board["hospitals"][1]["full_119"]
    assert board["area_name"] == "台北市" and board["stale"] is False
    # 第二次查詢用快照，不再抓
    container.triage.board(area_code="01")
    assert container.er_client.calls == 1


def test_board_survives_api_failure(container):
    from app.core.exceptions import ExternalServiceError

    container.er_client.error = ExternalServiceError("down")
    board = container.triage.board(area_code="01")
    assert board["hospitals"] == [] and board["stale"] is True


def test_board_for_address_and_nearby_clinics(container):
    container.hospital_repo.create(
        id="C1",
        name="大安診所",
        region="台北市",
        address="台北市大安區信義路三段1號",
        contact_phone="02-1111",
        capacity_tier="clinic",
    )
    container.hospital_repo.create(
        id="C2",
        name="松山診所",
        region="臺北市",
        address="臺北市松山區八德路1號",
        capacity_tier="clinic",
    )
    container.hospital_repo.create(
        id="M1",
        name="某醫學中心",
        region="台北市",
        address="台北市大安區x",
        capacity_tier="medical_center",
    )
    board = container.triage.board_for_address("台灣台北市大安區信義路")
    assert board["area_code"] == "01" and board["district"] == "大安區"
    assert container.triage.board_for_address("Some English Address") is None

    result = container.triage.nearby_clinics("台北市", "大安區")
    assert [h["name"] for h in result["hospitals"]] == ["大安診所"] and result[
        "scope"
    ] == "district"
    result = container.triage.nearby_clinics("台北市", "不存在區")
    assert result["scope"] == "city" and len(result["hospitals"]) == 2


# ---- 分流建議生命週期與通知 ----
def _line_subscriber(container, make_user, areas, label="北市勤指中心"):
    user = make_user(Role.GUEST, line_user_id=f"U-{label}")
    actor = container.users.to_principal(make_user(Role.SUPER_ADMIN))
    return container.triage.create_subscription(
        actor, {"kind": "line", "label": label, "area_codes": areas, "user_id": user.id}
    ), user


def test_advisory_lifecycle_and_notifications(container, make_user):
    triage = container.triage
    _, taipei_user = _line_subscriber(container, make_user, ["台北市"])
    _, taichung_user = _line_subscriber(container, make_user, ["03"], label="中市")
    actor = container.users.to_principal(make_user(Role.SUPER_ADMIN))
    webhook = triage.create_subscription(
        actor,
        {
            "kind": "webhook",
            "label": "119 系統",
            "webhook_url": "https://eoc.example/hook",
            "area_codes": [],
        },
    )
    assert webhook["secret"] and webhook["has_secret"]

    t0 = datetime(2026, 9, 26, 6, 45, tzinfo=UTC)
    container.er_client.payload = sample_er_payload("2026-09-26T14:45:03")  # 與 t0 對齊
    summary = triage.poll(t0)
    assert summary["opened"] == 1 and summary["resolved"] == 0 and summary["notified"] == 2
    advisories = triage.list_advisories(status="open")
    assert len(advisories) == 1 and advisories[0]["area_name"] == "台北市"
    assert [h["hospital_name"] for h in advisories[0]["congested"]] == ["臺大"]
    alternatives = [h["hospital_name"] for h in advisories[0]["alternatives"]]
    assert alternatives == ["聯醫仁愛", "亞東"]  # 同縣市有餘裕 → 鄰近縣市（新北）
    assert (
        "【急診分流建議】台北市" in advisories[0]["report"]
        and "119 滿床通報" in advisories[0]["report"]
    )

    pushes = container.line_client.pushes
    assert [to for to, _ in pushes] == [taipei_user.line_user_id]  # 台中訂閱者不會收到
    assert isinstance(pushes[0][1][0], FlexMessage)
    posts = container.webhook_client.posts
    assert (
        len(posts) == 1
        and posts[0]["event"] == "opened"
        and posts[0]["secret"] == webhook["secret"]
    )
    assert posts[0]["payload"]["advisory"]["area_code"] == "01"

    # 15 分鐘後仍壅塞：更新但不重複通知
    container.er_client.payload = sample_er_payload("2026-09-26T15:00:03")
    summary = triage.poll(t0 + timedelta(minutes=15))
    assert summary["updated"] == 1 and summary["notified"] == 0
    # 新增一家壅塞醫院 → 再通知
    payload = sample_er_payload("2026-09-26T15:15:03")
    payload["data"].append(
        er_row("0101020099", "聯醫忠孝", "01", wait_admit=40, txt_date="2026-09-26T15:15:03")
    )
    container.er_client.payload = payload
    summary = triage.poll(t0 + timedelta(minutes=30))
    assert summary["notified"] == 2 and len(container.webhook_client.posts) == 2
    assert container.webhook_client.posts[-1]["event"] == "updated"
    # 超過重複通知間隔（120 分）也再通知
    container.er_client.payload = sample_er_payload("2026-09-26T17:00:03")
    assert triage.poll(t0 + timedelta(hours=2, minutes=35))["notified"] == 2

    # 解除：臺大回到餘裕
    payload = sample_er_payload("2026-09-26T17:15:03")
    payload["data"][0] = er_row(
        "0401180014", "臺大", "01", hosp_type="1", wait_admit=10, txt_date="2026-09-26T17:15:03"
    )
    container.er_client.payload = payload
    summary = triage.poll(t0 + timedelta(hours=3))
    assert summary["resolved"] == 1 and summary["notified"] == 2
    assert triage.list_advisories(status="open") == []
    resolved = triage.list_advisories()[0]
    assert resolved["status"] == "resolved" and resolved["notify_count"] == 4
    assert container.webhook_client.posts[-1]["event"] == "resolved"
    assert taichung_user.line_user_id not in [to for to, _ in container.line_client.pushes]


def test_notification_failure_is_recorded_not_raised(container, make_user):
    sub, _ = _line_subscriber(container, make_user, [])
    container.line_client.fail_push = True
    summary = container.triage.poll()
    assert summary["opened"] == 1 and summary["notified"] == 0
    actor = container.users.to_principal(make_user(Role.SUPER_ADMIN))
    listed = container.triage.list_subscriptions(actor)[0]
    assert listed["last_error"] and listed["notify_count"] == 0


def test_purge_old_snapshots(container):
    triage = container.triage
    container.er_client.payload = sample_er_payload("2026-08-01T10:00:00")
    triage.poll(datetime(2026, 8, 1, 2, 0, tzinfo=UTC))
    container.er_client.payload = sample_er_payload("2026-09-26T14:45:03")
    summary = triage.poll(datetime(2026, 9, 26, 6, 45, tzinfo=UTC))
    assert summary["purged"] == 4 and container.snapshot_repo.count() == 4


# ---- LINE 找醫院 ----
def test_find_hospital_menu_sets_location_state(post_line_event, container):
    post_line_event(_event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL)}))
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "分享位置" in messages[0].text
    labels = [item.action.label for item in messages[0].quick_reply.items]
    assert "📍 分享位置" in labels and "🗺 選縣市" in labels
    state = container.conversation_state.peek("U1")
    assert state and state.action == "await_location" and state.payload["for"] == "hospital"


def test_find_hospital_by_location_message(post_line_event, container):
    post_line_event(_event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL)}))
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "家",
                "address": "台灣台北市大安區信義路三段",
                "latitude": 25.03,
                "longitude": 121.54,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage)
    flex = messages[0].contents.to_dict()
    text = str(flex)
    assert (
        "台北市 急診即時狀況" in text
        and "聯醫仁愛" in text
        and "臺大" in text
        and "119 滿床通報" in text
    )
    # 附近診所按鈕帶縣市與區
    assert "action=clinics" in text and "district=" in text
    assert container.conversation_state.peek("U1") is None


def test_find_hospital_area_picker_and_area(post_line_event, container):
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL, action="areas")})
    )
    _, messages = container.line_client.replies[-1]
    flex = messages[0].contents.to_dict()
    assert flex["type"] == "carousel" and len(flex["contents"]) == 4
    post_line_event(
        _event(
            "postback",
            postback={"data": postback_data(Intent.FIND_HOSPITAL, action="area", area="31")},
        )
    )
    _, messages = container.line_client.replies[-1]
    text = str(messages[0].contents.to_dict())
    assert "新北市 急診即時狀況" in text and "亞東" in text and "臺大" not in text
    # 不合法代碼 → 回選縣市
    post_line_event(
        _event(
            "postback",
            postback={"data": postback_data(Intent.FIND_HOSPITAL, action="area", area="99")},
        )
    )
    _, messages = container.line_client.replies[-1]
    assert messages[0].contents.to_dict()["type"] == "carousel"


def test_emergency_flow_adds_119_button(post_line_event, container):
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL, emergency="1")})
    )
    _, messages = container.line_client.replies[-1]
    assert "119" in messages[0].text
    assert messages[0].quick_reply.items[0].action.label == "🚨 撥打 119"
    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(Intent.FIND_HOSPITAL, action="area", area="01", emergency="1")
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "119" in messages[0].text
    assert "tel:119" in str(messages[1].contents.to_dict())


def test_find_clinics_postback(post_line_event, container):
    container.hospital_repo.create(
        id="C1",
        name="大安診所",
        region="台北市",
        address="台北市大安區信義路三段1號",
        contact_phone="(02)2700-0000",
        capacity_tier="clinic",
    )
    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(
                    Intent.FIND_HOSPITAL, action="clinics", city="台北市", district="大安區"
                )
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    text = str(messages[0].contents.to_dict())
    assert "大安診所" in text and "tel:0227000000" in text
    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(
                    Intent.FIND_HOSPITAL, action="clinics", city="台北市", district="中正區"
                )
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "同縣市" in str(messages[0].contents.to_dict())


# ---- 後台與公開 API ----
def test_admin_board_and_advisories_require_permission(client, make_user, auth_header, container):
    container.triage.poll()
    patient = make_user(Role.PATIENT)
    assert client.get("/api/v1/admin/triage/board", headers=auth_header(patient)).status_code == 403
    agent = make_user(Role.CS_AGENT, hospital_id="H1")
    resp = client.get("/api/v1/admin/triage/board?area=01", headers=auth_header(agent))
    assert resp.status_code == 200 and len(resp.get_json()["data"]["hospitals"]) == 2
    resp = client.get("/api/v1/admin/triage/advisories", headers=auth_header(agent))
    assert resp.status_code == 200 and resp.get_json()["data"][0]["area_code"] == "01"
    assert (
        client.get("/api/v1/admin/triage/stats", headers=auth_header(agent)).get_json()["data"][
            "open_advisories"
        ]
        == 1
    )
    assert client.post("/api/v1/admin/triage/poll", headers=auth_header(agent)).status_code == 403
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    assert client.post("/api/v1/admin/triage/poll", headers=auth_header(admin)).status_code == 200
    history = client.get(
        "/api/v1/admin/triage/hospitals/0401180014/history", headers=auth_header(agent)
    ).get_json()["data"]
    assert len(history) == 1 and history[0]["wait_admit"] == 80


def test_subscription_crud_scoped_to_hospital(client, make_user, auth_header):
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    h2_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H2")
    line_user = make_user(Role.GUEST, line_user_id="U-119")
    body = {
        "kind": "line",
        "label": "北市 119",
        "area_codes": "台北市, 新北市",
        "user_id": line_user.id,
    }
    resp = client.post(
        "/api/v1/admin/triage/subscriptions", json=body, headers=auth_header(h1_admin)
    )
    assert resp.status_code == 201, resp.get_json()
    created = resp.get_json()["data"]
    assert (
        created["area_codes"] == ["01", "31"]
        and created["hospital_id"] == "H1"
        and "secret" not in created
    )

    # 驗證錯誤
    assert (
        client.post(
            "/api/v1/admin/triage/subscriptions",
            json={"kind": "line", "label": "x", "user_id": "nope"},
            headers=auth_header(h1_admin),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/triage/subscriptions",
            json={"kind": "webhook", "label": "x", "webhook_url": "http://insecure"},
            headers=auth_header(h1_admin),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/triage/subscriptions",
            json={"kind": "line", "label": "x", "area_codes": ["99"], "user_id": line_user.id},
            headers=auth_header(h1_admin),
        ).status_code
        == 422
    )

    # 院所隔離
    assert (
        client.get("/api/v1/admin/triage/subscriptions", headers=auth_header(h2_admin)).get_json()[
            "data"
        ]
        == []
    )
    assert (
        client.patch(
            f"/api/v1/admin/triage/subscriptions/{created['id']}",
            json={"active": False},
            headers=auth_header(h2_admin),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/admin/triage/subscriptions/{created['id']}", headers=auth_header(h2_admin)
        ).status_code
        == 403
    )
    resp = client.patch(
        f"/api/v1/admin/triage/subscriptions/{created['id']}",
        json={"active": False, "label": "北市 119（暫停）"},
        headers=auth_header(h1_admin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["active"] is False

    # 測試通知（沒有建議時用範例）
    client.patch(
        f"/api/v1/admin/triage/subscriptions/{created['id']}",
        json={"active": True},
        headers=auth_header(h1_admin),
    )
    resp = client.post(
        f"/api/v1/admin/triage/subscriptions/{created['id']}/test", headers=auth_header(h1_admin)
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["delivered"] is True

    superadmin = make_user(Role.SUPER_ADMIN)
    assert (
        len(
            client.get(
                "/api/v1/admin/triage/subscriptions", headers=auth_header(superadmin)
            ).get_json()["data"]
        )
        == 1
    )
    assert (
        client.delete(
            f"/api/v1/admin/triage/subscriptions/{created['id']}", headers=auth_header(h1_admin)
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/admin/triage/subscriptions", headers=auth_header(h1_admin)).get_json()[
            "data"
        ]
        == []
    )


def test_public_board_and_api_key_pull(client, make_user, auth_header, container):
    resp = client.get("/api/v1/triage/board?area=01")
    assert resp.status_code == 200 and resp.get_json()["data"]["area_name"] == "台北市"
    assert client.get("/api/v1/triage/board?area=zz").status_code == 422
    assert "areas" in client.get("/api/v1/triage/areas").get_json()["data"]

    superadmin = make_user(Role.SUPER_ADMIN)
    created = client.post(
        "/api/v1/admin/triage/subscriptions",
        json={"kind": "api", "label": "119 拉取", "area_codes": ["01"]},
        headers=auth_header(superadmin),
    ).get_json()["data"]
    assert client.get("/api/v1/triage/advisories").status_code == 403
    assert (
        client.get("/api/v1/triage/advisories", headers={"X-Api-Key": "wrong"}).status_code == 403
    )
    assert (
        client.get(
            "/api/v1/triage/advisories", headers={"X-Api-Key": created["secret"]}
        ).get_json()["data"]
        == []
    )
    container.triage.poll()
    rows = client.get(
        "/api/v1/triage/advisories", headers={"X-Api-Key": created["secret"]}
    ).get_json()["data"]
    assert len(rows) == 1 and rows[0]["area_code"] == "01"
    # 拉取型訂閱不會被推送
    assert container.webhook_client.posts == [] and container.line_client.pushes == []


def test_webhook_signature():
    assert sign("s", b"{}").startswith("sha256=") and sign("s", b"{}") != sign("t", b"{}")


def test_pages_render(client):
    assert client.get("/liff/er?area=01").status_code == 200
    assert "急診即時看板" in client.get("/liff/er").get_data(as_text=True)
    assert client.get("/admin/triage").status_code == 200


# ---- 依使用者位置（LINE 位置訊息經緯度）----
def _seed_er_hospitals(container):
    """給三家重度級醫院座標（與 sample_er_payload 的 hosP_ID 對應）。"""
    container.hospital_repo.create(
        id="0401180014",
        name="臺大醫院",
        region="台北市",
        address="台北市中正區中山南路7號",
        capacity_tier="medical_center",
        lat=25.0410,
        lng=121.5185,
    )
    container.hospital_repo.create(
        id="0101020017",
        name="聯醫仁愛",
        region="台北市",
        address="台北市大安區仁愛路四段10號",
        capacity_tier="regional",
        lat=25.0372,
        lng=121.5452,
    )
    container.hospital_repo.create(
        id="1131010011",
        name="亞東醫院",
        region="新北市",
        address="新北市板橋區南雅南路二段21號",
        capacity_tier="medical_center",
        lat=24.9971,
        lng=121.4530,
    )


def test_board_for_location_sorts_by_distance_and_recommends_nearest_open(container):
    triage = container.triage
    assert triage.board_for_location(25.03, 121.54) is None  # 主檔沒座標 → 退回縣市
    _seed_er_hospitals(container)
    board = triage.board_for_location(25.0405, 121.5190, address="台灣台北市中正區")
    assert (
        board["mode"] == "distance" and board["area_code"] == "01" and board["district"] == "中正區"
    )
    names = [h["hospital_name"] for h in board["hospitals"]]
    assert names == ["臺大", "聯醫仁愛", "亞東"]  # 純距離排序，壅塞的臺大仍是最近
    assert board["hospitals"][0]["distance_km"] < 0.2 and board["hospitals"][2]["distance_km"] > 5
    # 推薦最近且未壅塞：臺大 119 滿床 → 跳過，推薦仁愛
    assert board["recommended"]["hospital_name"] == "聯醫仁愛" and board["all_congested"] is False
    # 全部壅塞 → recommended None
    from app.utils.clock import now_tw

    later = (now_tw() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = sample_er_payload(later)  # 回報時間要比第一輪晚，否則被去重
    for row in payload["data"]:
        row["inform"] = "Y"
    container.er_client.payload = payload
    triage.ingest(container.er_client.fetch())
    board = triage.board_for_location(25.03, 121.54)
    assert board["recommended"] is None and board["all_congested"] is True


def test_location_message_replies_nearest_hospitals_with_navigation(post_line_event, container):
    from linebot.v3.messaging import LocationMessage

    _seed_er_hospitals(container)
    post_line_event(_event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL)}))
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "家",
                "address": "台灣台北市大安區信義路三段",
                "latitude": 25.0330,
                "longitude": 121.5430,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage) and isinstance(messages[1], LocationMessage)
    text = str(messages[0].contents.to_dict())
    assert "離你最近的急救責任醫院" in text and "建議前往：聯醫仁愛" in text and "km" in text
    assert "lat=25.033" in text and "action=clinics" in text  # 附近診所按鈕帶座標
    assert messages[1].title == "聯醫仁愛" and abs(messages[1].latitude - 25.0372) < 1e-6
    # 先傳位置、再選「找醫院」（PENDING_LOCATION 帶座標）
    post_line_event(
        _event(
            "message",
            user_id="U9",
            message={
                "type": "location",
                "id": "m3",
                "title": "x",
                "address": "台北市中正區",
                "latitude": 25.0405,
                "longitude": 121.5190,
            },
        )
    )
    post_line_event(
        _event(
            "postback",
            user_id="U9",
            postback={"data": postback_data(Intent.FIND_HOSPITAL, source="state")},
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "建議前往：聯醫仁愛" in str(messages[0].contents.to_dict())


def test_nearby_clinics_by_distance(container):
    container.hospital_repo.create(
        id="C1",
        name="遠方診所",
        region="台北市",
        address="台北市北投區x",
        capacity_tier="clinic",
        lat=25.13,
        lng=121.50,
    )
    container.hospital_repo.create(
        id="C2",
        name="隔壁診所",
        region="台北市",
        address="台北市大安區y",
        capacity_tier="clinic",
        lat=25.034,
        lng=121.544,
    )
    container.hospital_repo.create(
        id="C3", name="無座標診所", region="台北市", address="台北市大安區z", capacity_tier="clinic"
    )
    result = container.triage.nearby_clinics("台北市", "大安區", lat=25.033, lng=121.543)
    assert result["scope"] == "distance" and [h["name"] for h in result["hospitals"]] == [
        "隔壁診所"
    ]
    # 10 km 內沒有座標院所 → 退回鄉鎮市區比對
    result = container.triage.nearby_clinics("台北市", "大安區", lat=23.0, lng=120.2)
    assert result["scope"] == "district" and {h["name"] for h in result["hospitals"]} == {
        "隔壁診所",
        "無座標診所",
    }


def test_advisory_alternatives_prefer_nearest_to_congested_hospital(container):
    _seed_er_hospitals(container)
    container.triage.poll()
    advisory = container.triage.list_advisories(status="open")[0]
    alternatives = advisory["alternatives"]
    assert [a["hospital_name"] for a in alternatives] == ["聯醫仁愛", "亞東"]
    assert (
        alternatives[0]["distance_from"] == "臺大"
        and alternatives[0]["distance_km"] < 3 < alternatives[1]["distance_km"]
    )
    assert "距臺大" in advisory["report"] and "km" in advisory["report"]


def test_public_board_by_location_and_hospital_import_with_coords(
    client, make_user, auth_header, container
):
    superadmin = make_user(Role.SUPER_ADMIN)
    csv_text = "id,name,region,address,contact_phone,capacity_tier,lat,lng\n0101020017,聯醫仁愛,台北市,台北市大安區仁愛路四段10號,,regional,25.0372,121.5452\n"
    import io

    resp = client.post(
        "/api/v1/admin/hospitals/import",
        data={"file": (io.BytesIO(csv_text.encode()), "h.csv")},
        headers=auth_header(superadmin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["created"] == 1
    assert container.hospital_repo.get("0101020017")["lat"] == 25.0372
    bad = client.patch(
        "/api/v1/admin/hospitals/H1",
        json={"lat": "abc", "lng": "1"},
        headers=auth_header(superadmin),
    )
    assert bad.status_code == 422
    board = client.get("/api/v1/triage/board?lat=25.03&lng=121.54").get_json()["data"]
    assert board["mode"] == "distance" and board["hospitals"][0]["hospital_name"] == "聯醫仁愛"
    assert client.get("/api/v1/triage/board?lat=x&lng=y").get_json()["data"].get("mode") is None
    assert client.get("/liff/er?lat=25.03&lng=121.54").status_code == 200
