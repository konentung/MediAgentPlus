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
        "preferred_hospital_id": "H1",
    },
}


def fill_all(client, headers, steps=VALID_STEPS):
    for number, payload in steps.items():
        resp = client.put(f"/api/v1/liff/intake/steps/{number}", json=payload, headers=headers)
        assert resp.status_code == 200, resp.get_json()
    return client.post("/api/v1/liff/intake/submit", json={}, headers=headers)


# ---- schema ----
@pytest.mark.parametrize(
    ("value", "expected"),
    [("A123456789", True), ("A123456780", False), ("A800000014", True), ("abc", False)],
)
def test_national_id_checksum(value, expected):
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
    assert row.national_id_encrypted and "A123456789" not in row.national_id_encrypted
    assert row.national_id_last4 == "6789"
    assert container.cipher.decrypt(row.national_id_encrypted) == "A123456789"
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
    assert "基本資料填寫" in html and '"H1"' in html and "填表人與病人身份" in html
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
