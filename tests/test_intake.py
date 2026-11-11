"""病人自填表：schema 驗證、LIFF API、送出後狀態轉換、身分證加密。"""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.domain.enums import BindingStatus, Role
from app.domain.intake_schema import TOTAL_STEPS, get_step, is_valid_national_id, validate_step
from app.extensions import db
from app.models.patient import PatientProfileModel

VALID_STEPS = {
    1: {
        "filler_relation": "self",
        "full_name": "王小明",
        "national_id": "A123456789",
        "birth_date": "1990-01-01",
        "sex": "male",
    },
    2: {
        "phone": "0912345678",
        "address_city": "台北市",
        "address_district": "大安區",
        "address_street": "忠孝東路一段 1 號",
        "emergency_contacts": [{"name": "王媽媽", "relationship": "parent", "phone": "0911222333"}],
    },
    3: {"height_cm": 170, "weight_kg": 65.5, "smoking": "never"},
    4: {
        "drug_allergies": [{"substance": "Penicillin", "reaction": "rash", "severity": "severe"}],
        "food_allergies": [],
        "food_allergies_none": True,
        "chronic_conditions": ["diabetes"],
        "family_history": ["cancer"],
    },
    5: {
        "has_current_medications": "yes",
        "current_medications": [{"drug_name": "Metformin", "dose": "500mg", "frequency": "bid"}],
    },
    6: {
        "consent_data_collection": True,
        "consent_share_with_hospitals": True,
        "consent_push_notification": False,
        "nhi_status": "insured",
    },
}


def add_hospital(client, headers, hospital_id: str):
    """病人在 LIFF「我的就醫院所」加入一家院所（就醫院所獨立於基本資料表單）。"""
    return client.post(
        "/api/v1/liff/hospitals/mine", json={"hospital_id": hospital_id}, headers=headers
    )


def fill_all(client, headers, steps=VALID_STEPS):
    for number, payload in steps.items():
        resp = client.put(f"/api/v1/liff/intake/steps/{number}", json=payload, headers=headers)
        assert resp.status_code == 200, resp.get_json()
    return client.post("/api/v1/liff/intake/submit", json={}, headers=headers)


@pytest.mark.parametrize(
    ("city", "district", "field_in_error"),
    [
        ("台北市", "大安區", None),
        ("臺北市", "大安區", None),  # 「臺」正規化成「台」
        ("火星市", "大安區", "address_city"),
        ("台北市", "鳳山區", "address_district"),  # 鄉鎮市區不屬於該縣市
    ],
)
def test_address_city_district_must_exist(city, district, field_in_error):
    """縣市 / 鄉鎮市區是下拉選單：值必須在 tw_districts.csv 裡，鄉鎮市區要屬於選的縣市。"""
    payload = {**VALID_STEPS[2], "address_city": city, "address_district": district}
    if field_in_error is None:
        cleaned = validate_step(get_step(2), payload)
        assert cleaned["address_city"] == "台北市"
        return
    with pytest.raises(ValidationError) as exc:
        validate_step(get_step(2), payload)
    assert field_in_error in exc.value.details["fields"]


# ---- schema ----
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("A123456789", True),
        ("A123456780", True),
        ("A800000014", True),
        ("ab12345678", True),
        ("abc", False),
        ("1234567890", False),
        ("A12345678", False),
    ],
)
def test_national_id_format(value, expected):
    assert is_valid_national_id(value) is expected


def test_step1_family_requires_filler_fields():
    with pytest.raises(ValidationError) as exc:
        validate_step(get_step(1), {**VALID_STEPS[1], "filler_relation": "family"})
    assert {"filler_name", "filler_relationship", "filler_phone"} <= set(
        exc.value.details["fields"]
    )


def test_step4_none_flag_distinguishes_empty_from_unfilled():
    with pytest.raises(ValidationError):
        validate_step(
            get_step(4), {"drug_allergies": [], "food_allergies": [], "chronic_conditions": []}
        )
    cleaned = validate_step(
        get_step(4),
        {
            "drug_allergies": [],
            "drug_allergies_none": True,
            "food_allergies": [],
            "food_allergies_none": True,
            "chronic_conditions": [],
            "chronic_conditions_none": True,
        },
    )
    assert cleaned["drug_allergies"] == []


def test_partial_save_skips_required_but_validates_format():
    cleaned = validate_step(get_step(2), {"phone": "0912345678"}, partial=True)
    assert cleaned["phone"] == "0912345678"
    with pytest.raises(ValidationError):
        validate_step(get_step(2), {"phone": "12345"}, partial=True)


# ---- API ----
def test_intake_requires_login(client):
    assert client.get("/api/v1/liff/intake").status_code == 401


def test_guest_can_fill_and_submit(client, container, make_user, auth_header):
    user = make_user(Role.GUEST, line_user_id="Uintake")
    headers = auth_header(user)

    state = client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]
    assert len(state["steps"]) == TOTAL_STEPS and state["completed_steps"] == []

    resp = fill_all(client, headers)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["binding_status"] == "pending"
    assert container.user_repo.get_by_id(user.id).binding_status is BindingStatus.PENDING
    # 送出後推播通知病人
    assert container.line_client.pushes[-1][0] == "Uintake"

    # 身分證不落明碼、回前端只給遮罩
    row = db.session.get(PatientProfileModel, user.id)
    assert row.national_id_fingerprint and "A123456789" not in row.national_id_fingerprint
    assert row.national_id_last4 == "6789"
    profile = client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]["data"]
    assert profile["national_id"] == "******6789"
    assert profile["drug_allergies"][0]["substance"] == "Penicillin"
    assert profile["consent_data_collection"] is True


def test_step_validation_errors_are_reported_per_field(client, make_user, auth_header):
    headers = auth_header(make_user())
    resp = client.put(
        "/api/v1/liff/intake/steps/1", json={"full_name": "王", "national_id": "X"}, headers=headers
    )
    assert resp.status_code == 422
    fields = resp.get_json()["error"]["details"]["fields"]
    assert fields["national_id"] and "birth_date" in fields


def test_submit_rejects_incomplete_form(client, make_user, auth_header):
    headers = auth_header(make_user())
    client.put("/api/v1/liff/intake/steps/1", json=VALID_STEPS[1], headers=headers)
    resp = client.post("/api/v1/liff/intake/submit", json={}, headers=headers)
    assert resp.status_code == 422


def test_masked_national_id_keeps_existing_value(client, container, make_user, auth_header):
    user = make_user()
    headers = auth_header(user)
    client.put("/api/v1/liff/intake/steps/1", json=VALID_STEPS[1], headers=headers)
    before = db.session.get(PatientProfileModel, user.id).national_id_fingerprint
    resp = client.put(
        "/api/v1/liff/intake/steps/1",
        json={**VALID_STEPS[1], "national_id": "******6789"},
        headers=headers,
    )
    assert resp.status_code == 200
    db.session.expire_all()
    assert db.session.get(PatientProfileModel, user.id).national_id_fingerprint == before


def test_duplicate_national_id_conflicts_without_locking(client, container, make_user, auth_header):
    """同一身分證出現在第二個帳號只回 409，不自動鎖定（家屬與本人可能各自填）。"""
    first, second = make_user(), make_user()
    client.put("/api/v1/liff/intake/steps/1", json=VALID_STEPS[1], headers=auth_header(first))
    resp = client.put(
        "/api/v1/liff/intake/steps/1", json=VALID_STEPS[1], headers=auth_header(second)
    )
    assert resp.status_code == 409
    assert container.user_repo.get_by_id(second.id).binding_status is BindingStatus.UNBOUND
    # 被院方鎖定後才不能填
    container.user_repo.update(second.id, binding_status=BindingStatus.LOCKED)
    assert (
        client.put(
            "/api/v1/liff/intake/steps/2",
            json=VALID_STEPS[2],
            headers=auth_header(container.user_repo.get_by_id(second.id)),
        ).status_code
        == 403
    )


def test_submitted_form_is_read_only_until_rejected(client, container, make_user, auth_header):
    """送出後（PENDING / VERIFIED）病人端不能再存、不能再送；院方退回後才可編輯。"""
    user = make_user(Role.GUEST, line_user_id="Uro")
    headers = auth_header(user)
    assert fill_all(client, headers).status_code == 200
    headers = auth_header(container.user_repo.get_by_id(user.id))  # PENDING
    state = client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]
    assert state["read_only"] is True and state["binding_status"] == "pending"
    resp = client.put(
        "/api/v1/liff/intake/steps/2", json={**VALID_STEPS[2], "partial": True}, headers=headers
    )
    assert resp.status_code == 403 and resp.get_json()["error"]["details"]["read_only"] is True
    assert client.post("/api/v1/liff/intake/submit", json={}, headers=headers).status_code == 403

    container.user_repo.update(user.id, binding_status=BindingStatus.VERIFIED, role=Role.PATIENT)
    headers = auth_header(container.user_repo.get_by_id(user.id))
    assert (
        client.put("/api/v1/liff/intake/steps/1", json=VALID_STEPS[1], headers=headers).status_code
        == 403
    )
    assert (
        client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]["read_only"] is True
    )

    container.user_repo.update(user.id, binding_status=BindingStatus.UNBOUND)  # 等同院方退回
    headers = auth_header(container.user_repo.get_by_id(user.id))
    assert (
        client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]["read_only"] is False
    )
    assert (
        client.put(
            "/api/v1/liff/intake/steps/2", json={**VALID_STEPS[2], "partial": True}, headers=headers
        ).status_code
        == 200
    )


def test_profile_endpoint_requires_verified(client, make_user, auth_header):
    guest = make_user()
    assert client.get("/api/v1/liff/profile", headers=auth_header(guest)).status_code == 403
    verified = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    resp = client.get("/api/v1/liff/profile", headers=auth_header(verified))
    assert resp.status_code == 200


# ---- 頁面 ----
def test_liff_pages_render(client):
    resp = client.get("/liff/intake?hospital=H1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "基本資料填寫" in html and "填表人與病人身份" in html
    assert client.get("/liff/settings").status_code == 200


def test_liff_endpoint_root_never_redirects(client):
    """LIFF 瀏覽器開 Endpoint URL 時若收到 3xx 會改用外部瀏覽器，/liff 必須直接 200。"""
    for path in ("/liff", "/liff/", "/liff?liff.state=%2Fintake"):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_draft_save_records_consents_and_submit_reports_missing_fields(
    client, make_user, auth_header
):
    """前端「下一頁」一律走草稿模式：必填可先跳過，送出時才整體檢查並逐欄回報。"""
    headers = auth_header(make_user())
    for number, payload in VALID_STEPS.items():
        body = {**payload, "partial": True}
        if number == 2:
            body.pop("phone")  # 故意漏掉必填
        assert (
            client.put(
                f"/api/v1/liff/intake/steps/{number}", json=body, headers=headers
            ).status_code
            == 200
        )
    state = client.get("/api/v1/liff/intake", headers=headers).get_json()["data"]
    assert state["data"]["consent_data_collection"] is True  # 草稿也記錄同意
    resp = client.post("/api/v1/liff/intake/submit", json={}, headers=headers)
    assert resp.status_code == 422
    assert set(resp.get_json()["error"]["details"]["fields"]) == {"phone"}
    client.put(
        "/api/v1/liff/intake/steps/2", json={**VALID_STEPS[2], "partial": True}, headers=headers
    )
    assert client.post("/api/v1/liff/intake/submit", json={}, headers=headers).status_code == 200


def test_none_flag_overrides_leftover_list_items():
    cleaned = validate_step(
        get_step(4),
        {
            "drug_allergies": [{"substance": "殘留資料"}],
            "drug_allergies_none": True,
            "food_allergies": [],
            "food_allergies_none": True,
            "chronic_conditions": ["diabetes"],
            "chronic_conditions_none": True,
        },
    )
    assert cleaned["drug_allergies"] == [] and cleaned["chronic_conditions"] == []


def test_rate_limit_blocks_excessive_calls(app, client):
    app.config["RATELIMIT_ENABLED"] = True
    try:
        statuses = [client.post("/api/v1/liff/auth", json={}).status_code for _ in range(31)]
    finally:
        app.config["RATELIMIT_ENABLED"] = False
    assert statuses[0] != 429 and statuses[-1] == 429


# ---- 就醫院所：獨立的 LIFF 表單，每家院所一筆 ----
def test_patient_manages_own_hospitals(client, container, make_user, auth_header):
    headers = auth_header(make_user(Role.GUEST))
    mine = "/api/v1/liff/hospitals/mine"

    # 還沒送出基本資料也能先加院所，但頁面會提示要先送出
    data = client.get(mine, headers=headers).get_json()["data"]
    assert data["hospitals"] == [] and data["submitted"] is False

    resp = add_hospital(client, headers, "H1")
    assert resp.status_code == 201, resp.get_json()
    link = resp.get_json()["data"]
    assert (link["hospital_id"], link["hospital_name"]) == ("H1", "第一醫院")
    assert link["confirmed"] is True  # 本人自己加的就算確認，院所可直接核對建檔
    assert link["source"] == "patient" and link["filed"] is False
    assert add_hospital(client, headers, "H1").status_code == 201  # 重複加入不報錯、不重複
    assert add_hospital(client, headers, "H2").status_code == 201
    ids = [
        h["hospital_id"] for h in client.get(mine, headers=headers).get_json()["data"]["hospitals"]
    ]
    assert ids == ["H1", "H2"]

    # 移除還沒建檔的院所
    assert client.delete(f"{mine}/H2", headers=headers).status_code == 200
    assert client.delete(f"{mine}/H2", headers=headers).status_code == 404
    ids = [
        h["hospital_id"] for h in client.get(mine, headers=headers).get_json()["data"]["hospitals"]
    ]
    assert ids == ["H1"]

    # 兩個帳號各自有自己的院所
    other = auth_header(make_user(Role.GUEST))
    assert client.get(mine, headers=other).get_json()["data"]["hospitals"] == []


@pytest.mark.parametrize(
    "hospital_id", ["NOPE", "不是代碼", "", None, "H1'; DROP TABLE hospitals;--"]
)
def test_unknown_hospital_cannot_be_added(client, make_user, auth_header, hospital_id):
    resp = add_hospital(client, auth_header(make_user(Role.GUEST)), hospital_id)
    assert resp.status_code in (404, 422), resp.get_json()


def test_inactive_hospital_cannot_be_added(client, container, make_user, auth_header):
    container.hospital_repo.update("H2", active=False)
    resp = add_hospital(client, auth_header(make_user(Role.GUEST)), "H2")
    assert resp.status_code in (404, 422)


def test_intake_form_has_no_hospital_field(client, make_user, auth_header):
    """就醫院所已從基本資料表單拉出來：送 `preferred_hospital_id` 會被當成不認得的欄位忽略。"""
    headers = auth_header(make_user(Role.GUEST))
    resp = client.put(
        "/api/v1/liff/intake/steps/6",
        json={**VALID_STEPS[6], "preferred_hospital_id": "H1"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert "preferred_hospital_id" not in resp.get_json()["data"]["profile"]
    assert (
        client.get("/api/v1/liff/hospitals/mine", headers=headers).get_json()["data"]["hospitals"]
        == []
    )


def test_hospital_search_for_my_hospitals(client, container):
    container.hospital_repo.create(
        id="C9", name="臺安診所", region="台北市", capacity_tier="clinic", address="臺北市大安區"
    )
    container.hospital_repo.create(id="X1", name="停用醫院", region="台北市", active=False)

    def search(keyword: str) -> list[str]:
        resp = client.get("/api/v1/liff/hospitals", query_string={"q": keyword})
        assert resp.status_code == 200
        return [h["id"] for h in resp.get_json()["data"]]

    assert search("第一") == ["H1"]
    assert search("台安") == ["C9"]  # 含診所；「台」比對得到主檔的「臺」
    assert search("停用") == []  # 停用的院所不能選
    assert search("第") == []  # 關鍵字太短不查
    first = client.get("/api/v1/liff/hospitals", query_string={"q": "第一"}).get_json()["data"][0]
    assert set(first) == {"id", "name", "region", "address"}  # 只回公開欄位
    # 不帶 q：維持原本的醫院層級選單（不含診所）
    listed = [h["id"] for h in client.get("/api/v1/liff/hospitals").get_json()["data"]]
    assert "C9" not in listed and "H1" in listed
