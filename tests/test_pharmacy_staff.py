"""藥局人員 / 藥局管理員：帳號歸屬藥局、維護自己藥局的資料、處理病人的領藥需求。"""

from __future__ import annotations

import pytest
from linebot.v3.messaging import FlexMessage

from app.domain.enums import Intent, Role
from app.line.intents import postback_data

P1, P2 = "5901010076", "5901020001"  # 榮星藥局（松山區）、信義藥局（信義區）
NHI_ROWS = [
    {
        "醫事機構代碼": P1,
        "醫事機構名稱": "榮星藥局",
        "醫事機構種類": "藥劑生自營",
        "電話": "(02)27124696",
        "地址": "臺北市松山區南京東路４段１３３巷５弄１號",
        "終止合約或歇業日期": "20991231",
        "固定看診時段": "星期一上午看診、星期二上午看診",
    },
    {
        "醫事機構代碼": P2,
        "醫事機構名稱": "信義藥局",
        "醫事機構種類": "藥師自營",
        "電話": "0227000000",
        "地址": "臺北市信義區松仁路１號",
        "終止合約或歇業日期": "",
        "固定看診時段": "星期日上午看診",
    },
]
ITEMS = [{"drug_name": "Metformin", "dose_text": "1 tab", "frequency_code": "bid", "timing": "pc"}]


@pytest.fixture(autouse=True)
def pharmacies(container):
    container.pharmacies.import_pharmacies(NHI_ROWS)


@pytest.fixture()
def pharmacist(make_user):
    return make_user(Role.PHARMACY_STAFF, pharmacy_id=P1)


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


def _postback(user_id: str = "U1", **params) -> dict:
    return _event(
        "postback", user_id=user_id, postback={"data": postback_data(Intent.MEDICATION, **params)}
    )


def _confirmed_prescription(container, user) -> str:
    return container.prescription_repo.create(
        user_id=user.id, status="confirmed", hospital_name="第一醫院", days_supply=28, items=ITEMS
    )["id"]


def _button_labels(bubble: dict) -> list[str]:
    return [b["action"]["label"] for b in (bubble.get("footer") or {}).get("contents", [])]


# ---- 角色指派：帳號歸屬藥局 ----
def test_super_admin_assigns_pharmacy_roles(client, make_user, auth_header):
    superadmin = make_user(Role.SUPER_ADMIN)
    target = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    url = f"/api/v1/admin/users/{target.id}/role"
    headers = auth_header(superadmin)

    # 藥局人員一定要有藥局，而且藥局要存在
    assert client.put(url, json={"role": "pharmacy_staff"}, headers=headers).status_code == 422
    resp = client.put(url, json={"role": "pharmacy_staff", "pharmacy_id": "NOPE"}, headers=headers)
    assert resp.status_code == 422

    # 從院所人員改成藥局人員：掛上藥局、清掉院所
    resp = client.put(url, json={"role": "pharmacy_staff", "pharmacy_id": P1}, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert (data["pharmacy_id"], data["hospital_id"]) == (P1, None)

    # 單位管理員掛到藥局 = 藥局管理員；再改回院所角色時藥局清掉
    resp = client.put(url, json={"role": "hospital_admin", "pharmacy_id": P1}, headers=headers)
    assert resp.get_json()["data"]["pharmacy_id"] == P1
    resp = client.put(url, json={"role": "clinic_staff", "hospital_id": "H1"}, headers=headers)
    data = resp.get_json()["data"]
    assert (data["pharmacy_id"], data["hospital_id"]) == (None, "H1")

    # 單位管理員沒有任何單位可掛 → 拒絕
    orphan = make_user(Role.GUEST)
    resp = client.put(
        f"/api/v1/admin/users/{orphan.id}/role",
        json={"role": "hospital_admin", "hospital_id": ""},
        headers=headers,
    )
    assert resp.status_code == 422


def test_pharmacy_admin_manages_only_own_pharmacy_staff(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, pharmacy_id=P1)
    headers = auth_header(admin)
    own = make_user(Role.PHARMACY_STAFF, pharmacy_id=P1)
    other = make_user(Role.PHARMACY_STAFF, pharmacy_id=P2)
    hospital_staff = make_user(Role.CLINIC_STAFF, hospital_id="H1")
    newcomer = make_user(Role.GUEST)

    me = client.get("/api/v1/auth/me", headers=headers).get_json()["data"]
    assert me["pharmacy_id"] == P1 and me["pharmacy_name"] == "榮星藥局"
    assert set(me["assignable_roles"]) == {"guest", "pharmacy_staff", "hospital_admin"}
    assert {"pickup:manage", "pharmacy:settings", "staff:manage"} <= set(me["permissions"])
    assert "patient:read" not in me["permissions"]
    assert "education:content_manage" not in me["permissions"]

    # 人員清單只有自己藥局的人；含一般使用者時多出還沒指派單位的新同仁
    rows = client.get("/api/v1/admin/users", headers=headers).get_json()["data"]
    assert {r["user_id"] for r in rows} == {admin.id, own.id}
    assert all(r["pharmacy_name"] == "榮星藥局" for r in rows)
    rows = client.get("/api/v1/admin/users?staff=0", headers=headers).get_json()["data"]
    assert {r["user_id"] for r in rows} == {admin.id, own.id, newcomer.id}

    def assign(user, role, **body):
        return client.put(
            f"/api/v1/admin/users/{user.id}/role", json={"role": role, **body}, headers=headers
        )

    # 新同仁 → 藥局人員：自動掛到單位管理員自己的藥局
    resp = assign(newcomer, "pharmacy_staff")
    assert resp.status_code == 200 and resp.get_json()["data"]["pharmacy_id"] == P1
    # 不能指派院所的角色、不能動別家藥局或院所的人、不能把人指派到別家藥局
    assert assign(newcomer, "clinic_staff").status_code == 403
    assert assign(other, "guest").status_code == 403
    assert assign(hospital_staff, "guest").status_code == 403
    assert assign(own, "pharmacy_staff", pharmacy_id=P2).status_code == 403
    # 離職：降回一般使用者，藥局清掉
    resp = assign(own, "guest")
    assert resp.status_code == 200 and resp.get_json()["data"]["pharmacy_id"] is None

    # 院所管理員反過來也不能指派藥局人員
    hospital_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    resp = client.put(
        f"/api/v1/admin/users/{make_user(Role.GUEST).id}/role",
        json={"role": "pharmacy_staff", "pharmacy_id": P1},
        headers=auth_header(hospital_admin),
    )
    assert resp.status_code == 403


def test_pharmacy_account_cannot_reach_hospital_data(client, make_user, auth_header, pharmacist):
    """藥局帳號（含藥局管理員）碰不到病人、回報、衛教、稽核。"""
    admin = make_user(Role.HOSPITAL_ADMIN, pharmacy_id=P1)
    for user in (pharmacist, admin):
        for path in (
            "/api/v1/admin/patients",
            "/api/v1/admin/feedback",
            "/api/v1/admin/knowledge",
            "/api/v1/admin/audit",
        ):
            assert client.get(path, headers=auth_header(user)).status_code == 403, path
    # 藥局人員不能管人員
    assert client.get("/api/v1/admin/users", headers=auth_header(pharmacist)).status_code == 403


# ---- 後台的院所 / 藥局選取框：輸入名稱或代碼搜尋 ----
def test_org_picker_search_by_name_or_code(client, make_user, auth_header):
    """選取框打的就是這兩支清單 API：名稱關鍵字與代碼都要找得到，每筆帶代碼與名稱。"""
    headers = auth_header(make_user(Role.SUPER_ADMIN))

    def search(kind, q):
        data = client.get(f"/api/v1/admin/{kind}", query_string={"q": q}, headers=headers)
        return [(row["id"], row["name"]) for row in data.get_json()["data"]["items"]]

    assert search("hospitals", "診所") == [("H2", "第二診所")]  # 診所也搜得到，不只醫院層級
    assert search("hospitals", "h1") == [("H1", "第一醫院")]  # 代碼不分大小寫
    assert search("hospitals", "沒有這家") == []
    assert search("pharmacies", "榮星") == [(P1, "榮星藥局")]
    assert search("pharmacies", P2) == [(P2, "信義藥局")]

    # 人員清單附上所屬單位的名稱，選取框才能直接顯示「名稱（代碼）」
    clinic_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H2")
    pharmacy_admin = make_user(Role.HOSPITAL_ADMIN, pharmacy_id=P1)
    rows = client.get("/api/v1/admin/users", headers=headers).get_json()["data"]
    by_id = {row["user_id"]: row for row in rows}
    assert by_id[clinic_admin.id]["hospital_name"] == "第二診所"
    assert by_id[pharmacy_admin.id]["pharmacy_name"] == "榮星藥局"


# ---- 維護自己藥局的資料 ----
def test_pharmacy_staff_updates_own_pharmacy(client, container, make_user, auth_header, pharmacist):
    headers = auth_header(pharmacist)
    mine = client.get("/api/v1/admin/pharmacy", headers=headers).get_json()["data"]
    assert mine["id"] == P1 and mine["opening_hours"] == {"mon": ["am"], "tue": ["am"]}

    body = {
        "phone": "02-1234-5678",
        "address": "臺北市大安區信義路四段 1 號",
        "opening_hours": {"mon": ["night", "am"], "sat": ["pm"], "sun": []},
        "name": "偷改名稱",  # 名稱只有系統管理員能改，藥局人員送了也不會生效
    }
    resp = client.patch(f"/api/v1/admin/pharmacies/{P1}", json=body, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["name"] == "榮星藥局" and data["phone"] == "02-1234-5678"
    # 地址的縣市 / 鄉鎮市區跟著重新解析（病人是用這兩個欄位找藥局）
    assert (data["city"], data["district"]) == ("台北市", "大安區")
    assert data["opening_hours"] == {"mon": ["am", "night"], "sat": ["pm"]}
    assert data["self_managed_at"]

    # 重新匯入開放資料：藥局自己改過的欄位不會被蓋掉，沒改過的藥局照常更新
    container.pharmacies.import_pharmacies(NHI_ROWS)
    row = container.pharmacy_repo.get(P1)
    assert row["phone"] == "02-1234-5678" and row["district"] == "大安區"
    assert row["opening_hours"] == {"mon": ["am", "night"], "sat": ["pm"]}

    def patch(pharmacy_id, payload, user=pharmacist):
        url = f"/api/v1/admin/pharmacies/{pharmacy_id}"
        return client.patch(url, json=payload, headers=auth_header(user))

    # 驗證：地址要認得出縣市、時段代碼要合法；不能改別家藥局；院所帳號不能改藥局
    assert patch(P1, {"address": "不知道在哪裡"}).status_code == 422
    assert patch(P1, {"opening_hours": {"mon": ["midnight"]}}).status_code == 422
    assert patch(P1, {"opening_hours": {"holiday": ["am"]}}).status_code == 422
    assert patch(P2, {"phone": "000"}).status_code == 403
    hospital_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    assert patch(P1, {"phone": "000"}, hospital_admin).status_code == 403
    # 系統管理員可以代改任何藥局，包含名稱與停用
    resp = patch(P2, {"name": "信義健保藥局", "active": False}, make_user(Role.SUPER_ADMIN))
    assert resp.status_code == 200 and resp.get_json()["data"]["active"] is False


# ---- 領藥需求 ----
def test_pickup_button_on_every_recommended_pharmacy(post_line_event, container, pharmacist):
    """從已確認的處方找藥局：每家藥局都有「我要去這裡領藥」，按下去就建立領藥需求；
    有人員帳號的藥局回「已通知備藥」，沒有的回「已記下、請直接前往」。"""
    post_line_event(_postback())
    post_line_event(
        _event(
            "message",
            message={
                "type": "image",
                "id": "img1",
                "contentProvider": {"type": "line"},
                "quoteToken": "q",
            },
        )
    )
    patient = container.user_repo.get_by_line_user_id("U1")
    pid = container.prescription_repo.list_for_user(patient.id)[0]["id"]
    post_line_event(_postback(action="confirm", prescription_id=pid))

    def locate(address):
        post_line_event(
            _event(
                "message",
                message={
                    "type": "location",
                    "id": "loc1",
                    "title": "我的位置",
                    "address": address,
                    "latitude": 25.05,
                    "longitude": 121.55,
                },
            )
        )
        _, messages = container.line_client.replies[-1]
        assert isinstance(messages[1], FlexMessage)
        return messages[1].contents.to_dict()["contents"][0]

    # 松山區的榮星藥局有藥局人員 → 按下去建立需求並回「已通知備藥」
    bubble = locate("台北市松山區南京東路四段")
    assert bubble["body"]["contents"][0]["text"] == "榮星藥局"
    assert _button_labels(bubble)[0] == "我要去這裡領藥"
    assert "我要去這裡領藥" in container.line_client.replies[-1][1][0].text

    post_line_event(_postback(action="pickup", prescription_id=pid, pharmacy_id=P1))
    _, messages = container.line_client.replies[-1]
    pickup = container.pickup_repo.get_for_prescription(pid)
    assert pickup["pharmacy_id"] == P1 and pickup["status"] == "requested"
    assert pickup["user_id"] == patient.id
    assert "已通知 榮星藥局" in messages[0].text and pickup["id"][:6].upper() in messages[0].text

    # 信義藥局沒有人員帳號 → 一樣有按鈕、一樣建立需求（= 換藥局），但提醒要直接前往
    post_line_event(_postback(action="locate_prompt", prescription_id=pid))
    assert _button_labels(locate("台北市信義區松仁路"))[0] == "我要去這裡領藥"
    post_line_event(_postback(action="pickup", prescription_id=pid, pharmacy_id=P2))
    _, messages = container.line_client.replies[-1]
    assert "已記下你要到 信義藥局" in messages[0].text
    assert "還沒有加入線上備藥" in messages[0].text
    pickup = container.pickup_repo.get_for_prescription(pid)
    assert pickup["pharmacy_id"] == P2 and pickup["status"] == "requested"

    # 沒有處方、只是找附近藥局 → 沒有領藥按鈕（領藥需求要對應一張處方）
    post_line_event(_postback(action="locate_prompt"))
    labels = _button_labels(locate("台北市松山區南京東路四段"))
    assert "我要去這裡領藥" not in labels
    assert "我要去這裡領藥" not in container.line_client.replies[-1][1][0].text


def test_pickup_request_rules(container, make_user, pharmacist):
    patient = make_user(Role.GUEST)
    principal = container.users.to_principal(patient)
    from app.core.exceptions import AppError

    # 還沒確認的處方、別人的處方都不能送
    draft = container.prescription_repo.create(user_id=patient.id, status="draft", items=ITEMS)
    others = _confirmed_prescription(container, make_user(Role.GUEST))
    for prescription_id in (draft["id"], others, "nope"):
        with pytest.raises(AppError):
            container.pickups.request(principal, prescription_id, P1)

    pid = _confirmed_prescription(container, patient)
    first = container.pickups.request(principal, pid, P1)
    # 同一張處方再送一次 = 同一筆（不會重複），領過之後不能再送
    assert container.pickups.request(principal, pid, P1)["id"] == first["id"]
    container.pickup_repo.update(first["id"], status="picked_up")
    with pytest.raises(AppError):
        container.pickups.request(principal, pid, P1)


def test_pharmacy_handles_pickups(client, container, make_user, auth_header, pharmacist):
    patient = make_user(Role.GUEST, line_user_id="Upatient")
    pid = _confirmed_prescription(container, patient)
    pickup = container.pickups.request(container.users.to_principal(patient), pid, P1)
    headers = auth_header(pharmacist)

    data = client.get("/api/v1/admin/pickups?status=requested", headers=headers).get_json()["data"]
    assert data["total"] == 1
    row = data["items"][0]
    assert row["id"] == pickup["id"] and row["code"] == pickup["code"]
    assert row["next_statuses"] == ["cancelled", "ready"]
    # 藥局只看得到領藥需要的內容：藥品清單與處方摘要，沒有照片路徑或辨識原始資料
    assert row["prescription"]["hospital_name"] == "第一醫院"
    assert row["prescription"]["items"][0]["drug_name"] == "Metformin"
    assert row["prescription"]["items"][0]["frequency_label"]
    assert not {"image_path", "ocr_raw", "user_id"} & set(row["prescription"])

    stats = client.get("/api/v1/admin/dashboard", headers=headers).get_json()["data"]
    assert stats == {"requested_pickups": 1}

    # 系統管理員看得到全部藥局的需求與任何一家藥局的資料
    superadmin = auth_header(make_user(Role.SUPER_ADMIN))
    data = client.get("/api/v1/admin/pickups", headers=superadmin).get_json()["data"]
    assert data["total"] == 1 and data["items"][0]["pharmacy_name"] == "榮星藥局"
    stats = client.get("/api/v1/admin/dashboard", headers=superadmin).get_json()["data"]
    assert stats["requested_pickups"] == 1 and "pending_patients" in stats
    resp = client.get(f"/api/v1/admin/pharmacy?pharmacy_id={P2}", headers=superadmin)
    assert resp.status_code == 200 and resp.get_json()["data"]["name"] == "信義藥局"

    url = f"/api/v1/admin/pickups/{pickup['id']}"
    # 別家藥局、院所帳號都碰不到這筆
    other = auth_header(make_user(Role.PHARMACY_STAFF, pharmacy_id=P2))
    assert client.get("/api/v1/admin/pickups", headers=other).get_json()["data"]["total"] == 0
    assert client.patch(url, json={"status": "ready"}, headers=other).status_code == 404
    clinic = auth_header(make_user(Role.CLINIC_STAFF, hospital_id="H1"))
    assert client.patch(url, json={"status": "ready"}, headers=clinic).status_code == 403

    # 不能跳過「可領取」直接標已領；備好 → 推播病人（帶領藥編號與藥局說明）
    assert client.patch(url, json={"status": "picked_up"}, headers=headers).status_code == 409
    resp = client.patch(url, json={"status": "ready", "note": "晚上 9 點前可領"}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["data"]["status"] == "ready"
    to, messages = container.line_client.pushes[-1]
    assert to == "Upatient"
    assert pickup["code"] in messages[0].text and "晚上 9 點前可領" in messages[0].text

    pushes = len(container.line_client.pushes)
    resp = client.patch(url, json={"status": "picked_up"}, headers=headers)
    assert resp.get_json()["data"]["status"] == "picked_up"
    assert resp.get_json()["data"]["next_statuses"] == []
    assert len(container.line_client.pushes) == pushes  # 已領取不用再通知
    assert (
        client.patch(url, json={"status": "cancelled", "note": "x"}, headers=headers).status_code
        == 409
    )


def test_pharmacy_cannot_dispense_requires_reason(
    client, container, make_user, auth_header, pharmacist
):
    patient = make_user(Role.GUEST, line_user_id="Upatient")
    pid = _confirmed_prescription(container, patient)
    pickup = container.pickups.request(container.users.to_principal(patient), pid, P1)
    url = f"/api/v1/admin/pickups/{pickup['id']}"
    headers = auth_header(pharmacist)

    assert client.patch(url, json={"status": "cancelled"}, headers=headers).status_code == 422
    # 推播失敗不影響狀態更新
    container.line_client.fail_push = True
    resp = client.patch(url, json={"status": "cancelled", "note": "缺藥"}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["data"]["note"] == "缺藥"

    # 病人改到別家藥局（那家要有人員）：同一筆需求換藥局、回到待備藥
    container.line_client.fail_push = False
    make_user(Role.PHARMACY_STAFF, pharmacy_id=P2)
    again = container.pickups.request(container.users.to_principal(patient), pid, P2)
    assert again["id"] == pickup["id"] and again["status"] == "requested"
    assert again["note"] is None and again["pharmacy_id"] == P2
