"""急診動態分流：規則、API 解析、快照去重、建議生命週期與通知、LINE 找醫院、後台 / 公開 API。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from linebot.v3.messaging import FlexMessage, TextMessage

from app.domain import triage_rules as rules
from app.domain.enums import Intent, Role
from app.integrations.nhi_er import parse_feed
from app.line.intents import IntentRequest, postback_data
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


def test_nearby_clinics_by_district(container):
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
    result = container.triage.nearby_clinics("台北市", "大安區")
    assert [h["name"] for h in result["hospitals"]] == ["大安診所"] and result[
        "scope"
    ] == "district"
    result = container.triage.nearby_clinics("台北市", "不存在區")
    assert result["scope"] == "city" and len(result["hospitals"]) == 2


# ---- 分流建議生命週期與通知 ----


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
    assert labels == ["📍 分享位置"]
    state = container.conversation_state.peek("U1")
    assert state and state.action == "await_location" and state.payload["for"] == "hospital"


def test_emergency_flow_adds_119_button(post_line_event, container):
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL, emergency="1")})
    )
    _, messages = container.line_client.replies[-1]
    assert "119" in messages[0].text
    assert messages[0].quick_reply.items[0].action.label == "🚨 撥打 119"
    state = container.conversation_state.peek("U1")
    assert state.action == "await_location" and state.payload["emergency"] == "1"


def test_public_board_by_area_and_areas_list(client, container):
    container.triage.poll()
    resp = client.get("/api/v1/triage/board?area=01")
    assert resp.status_code == 200 and resp.get_json()["data"]["area_name"] == "台北市"
    assert client.get("/api/v1/triage/board?area=zz").status_code == 422
    assert "areas" in client.get("/api/v1/triage/areas").get_json()["data"]


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


def test_find_clinics_without_location_asks_then_uses_shared_position(post_line_event, container):
    """AI 的「附近診所」按鈕沒有縣市：先請分享位置（記下科別），位置訊息回來再依科別找。"""
    container.hospital_repo.create(
        id="C1",
        name="大安家醫診所",
        region="台北市",
        address="台北市大安區信義路三段1號",
        capacity_tier="clinic",
        departments="家庭醫學科",
    )
    container.hospital_repo.create(
        id="C2",
        name="大安牙醫診所",
        region="台北市",
        address="台北市大安區信義路三段2號",
        capacity_tier="clinic",
        departments="牙醫一般科",
    )
    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(Intent.FIND_HOSPITAL, action="clinics", department="家醫科")
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "有家醫科的診所" in messages[0].text
    assert messages[0].quick_reply.items[0].action.type == "location"
    state = container.conversation_state.peek("U1")
    assert state.action == "await_location" and state.payload == {
        "for": "clinics",
        "department": "家醫科",
    }
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "我的位置",
                "address": "台北市大安區信義路三段10號",
                "latitude": 25.033,
                "longitude": 121.543,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    text = str(messages[0].contents.to_dict())
    assert "大安家醫診所" in text and "大安牙醫診所" not in text and "有家醫科" in text
    assert container.conversation_state.peek("U1") is None


# ---- 後台與公開 API ----


def test_pages_render(client):
    assert client.get("/liff/er?area=01").status_code == 200
    assert "急診即時看板" in client.get("/liff/er").get_data(as_text=True)


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
    container.hospital_repo.create(
        id="R1",
        name="隔壁區域醫院",
        region="台北市",
        address="台北市大安區w",
        capacity_tier="regional",
        lat=25.0335,
        lng=121.5435,
    )
    # 同區的診所（不管有沒有座標）都要列；診所排在區域醫院前面、有距離的排前面；遠方不列
    result = container.triage.nearby_clinics("台北市", "大安區", lat=25.033, lng=121.543)
    assert result["scope"] == "nearby" and [h["name"] for h in result["hospitals"]] == [
        "隔壁診所",
        "無座標診所",
        "隔壁區域醫院",
    ]
    assert (
        result["hospitals"][0]["distance_km"] == 0.2 and "distance_km" not in result["hospitals"][1]
    )
    # 座標離很遠但地址在大安區：仍以鄉鎮市區為準
    result = container.triage.nearby_clinics("台北市", "大安區", lat=23.0, lng=120.2)
    assert [h["name"] for h in result["hospitals"]] == ["隔壁診所", "無座標診所", "隔壁區域醫院"]


def test_nearby_clinics_filters_by_department(container):
    container.hospital_repo.create(
        id="C1",
        name="家醫診所",
        region="台北市",
        address="台北市大安區a",
        capacity_tier="clinic",
        departments="家庭醫學科,內科",
    )
    container.hospital_repo.create(
        id="C2",
        name="一般科診所",
        region="台北市",
        address="台北市大安區b",
        capacity_tier="clinic",
        departments="西醫一般科",
    )
    container.hospital_repo.create(
        id="C3",
        name="牙醫診所",
        region="台北市",
        address="台北市大安區c",
        capacity_tier="clinic",
        departments="牙醫一般科",
    )
    # AI 說「家醫科」→ 對應衛福部用詞「家庭醫學科」，一般科診所也能看但排後面
    result = container.triage.nearby_clinics("台北市", "大安區", department="家醫科")
    assert result["department_matched"] is True
    assert [h["name"] for h in result["hospitals"]] == ["家醫診所", "一般科診所"]
    assert result["department_terms"] == ["家庭醫學科", "西醫一般科"]
    # 沒有該科 → 放寬成不限科別、標示沒對到
    result = container.triage.nearby_clinics("台北市", "大安區", department="皮膚科")
    assert result["department_matched"] is False and len(result["hospitals"]) == 3
    assert container.triage.nearby_clinics("台北市", "大安區")["department_matched"] is False


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


# ---- 急診資料抓不到 / 位置訊息沒有中文地址 ----
def test_container_builds_er_client_with_api_url():
    """回歸：曾把 timeout 當成 URL 傳給 NhiErClient，導致永遠抓不到急診資料。"""
    from app import create_app

    er_client = create_app("testing").extensions["container"].er_client
    assert er_client.url.startswith("https://")


def test_location_message_when_er_feed_unavailable(post_line_event, container):
    from app.core.exceptions import ExternalServiceError

    _seed_er_hospitals(container)
    container.er_client.error = ExternalServiceError("down")
    post_line_event(_event("postback", postback={"data": postback_data(Intent.FIND_HOSPITAL)}))
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "Home",
                "address": "No. 1, Sec. 3, Xinyi Rd, Da’an District, Taipei City, Taiwan",
                "latitude": 25.0330,
                "longitude": 121.5430,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "暫時無法取得" in messages[0].text
    labels = [item.action.label for item in messages[0].quick_reply.items]
    assert labels == ["🚨 撥打 119", "🩺 找附近診所", "📍 再試一次"]
    # 英文地址也能靠座標反推縣市 / 鄉鎮市區給「找附近診所」用
    clinics = IntentRequest.from_postback(messages[0].quick_reply.items[1].action.data).params
    assert clinics["city"] == "台北市" and clinics["district"] == "大安區"


def test_board_for_location_resolves_district_from_coordinates(container):
    _seed_er_hospitals(container)
    board = container.triage.board_for_location(
        25.0405, 121.5190, address="No. 7, Zhongshan S. Rd, Zhongzheng District, Taipei City"
    )
    assert board["unavailable"] is False
    assert (board["area_name"], board["district"]) == ("台北市", "中正區")
