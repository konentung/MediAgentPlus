"""院方後台：病人列表 / 核對建檔 / 退回 / 修正 / 就診紀錄，以及院所隔離與權限。"""

from __future__ import annotations

import pytest

from app.domain.enums import BindingStatus, Intent, Permission, Role
from app.domain.rbac import permissions_for
from app.line.intents import postback_data
from tests.test_intake import fill_all
from tests.test_webhook import _event

H1, H2 = "H1", "H2"


@pytest.fixture()
def staff(make_user):
    return make_user(Role.HOSPITAL_STAFF, BindingStatus.UNBOUND, hospital_id=H1)


@pytest.fixture()
def submitted_patient(client, make_user, auth_header, staff, post_line_event):
    """一位填完表、已送出，且 H1 院方在後台「加入本院」、病人按「是」確認過的 GUEST。"""
    user = make_user(Role.GUEST, line_user_id="Upatient")
    assert fill_all(client, auth_header(user)).status_code == 200
    resp = client.post(
        f"/api/v1/admin/patients/{user.id}/request-hospital-confirmation",
        json={},
        headers=auth_header(staff),
    )
    assert resp.status_code == 200, resp.get_json()
    post_line_event(
        _event(
            "postback",
            user_id="Upatient",
            postback={"data": postback_data(Intent.HOSPITAL_CONFIRM, hospital_id=H1, answer="yes")},
        )
    )
    return user


# ---- RBAC ----
def test_new_permissions_role_matrix():
    assert Permission.INTAKE_WRITE_OWN in permissions_for(Role.GUEST)
    assert Permission.PATIENT_VERIFY in permissions_for(Role.HOSPITAL_STAFF)
    assert Permission.PATIENT_VERIFY not in permissions_for(Role.MEDICAL_STAFF)
    assert Permission.PATIENT_WRITE not in permissions_for(Role.MEDICAL_STAFF)
    assert Permission.PATIENT_READ not in permissions_for(Role.PATIENT)


def test_medical_staff_can_read_but_not_verify(client, submitted_patient, make_user, auth_header):
    agent = make_user(Role.MEDICAL_STAFF, hospital_id=H1)
    headers = auth_header(agent)
    assert (
        client.get(f"/api/v1/admin/patients/{submitted_patient.id}", headers=headers).status_code
        == 200
    )
    resp = client.post(
        f"/api/v1/admin/patients/{submitted_patient.id}/verify", json={}, headers=headers
    )
    assert resp.status_code == 403


# ---- 列表 / 詳情 ----
def test_pending_list_scoped_to_preferred_hospital(
    client, submitted_patient, staff, make_user, auth_header
):
    rows = client.get(
        "/api/v1/admin/patients?status=pending", headers=auth_header(staff)
    ).get_json()
    assert [r["user_id"] for r in rows["data"]] == [submitted_patient.id]
    assert rows["data"][0]["national_id_last4"] == "6789"

    other = make_user(Role.MEDICAL_STAFF, hospital_id=H2)
    assert (
        client.get("/api/v1/admin/patients?status=all", headers=auth_header(other)).get_json()[
            "data"
        ]
        == []
    )
    assert (
        client.get(
            f"/api/v1/admin/patients/{submitted_patient.id}", headers=auth_header(other)
        ).status_code
        == 403
    )
    superadmin = make_user(Role.SUPER_ADMIN)
    assert (
        len(
            client.get("/api/v1/admin/patients", headers=auth_header(superadmin)).get_json()["data"]
        )
        == 1
    )


def test_staff_without_hospital_is_refused(client, submitted_patient, make_user, auth_header):
    unassigned = make_user(Role.MEDICAL_STAFF, hospital_id=None)
    assert client.get("/api/v1/admin/patients", headers=auth_header(unassigned)).status_code == 403


def test_detail_masks_national_id_and_search_by_last4(
    client, submitted_patient, staff, auth_header
):
    headers = auth_header(staff)
    detail = client.get(
        f"/api/v1/admin/patients/{submitted_patient.id}", headers=headers
    ).get_json()["data"]
    assert detail["profile"]["national_id"] == "******6789"
    assert "national_id_encrypted" not in detail["profile"]
    assert detail["record"] is None and detail["submissions"][0]["status"] == "submitted"
    hits = client.get("/api/v1/admin/patients?status=all&q=6789", headers=headers).get_json()[
        "data"
    ]
    assert len(hits) == 1


# ---- 核對 / 退回 ----
def test_verify_creates_record_and_promotes_user(
    client, container, submitted_patient, staff, make_user, auth_header
):
    headers = auth_header(staff)
    resp = client.post(
        f"/api/v1/admin/patients/{submitted_patient.id}/verify",
        json={"medical_record_no": "MR-001", "identity_method": "nhi_card"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    record = resp.get_json()["data"]
    assert record["hospital_id"] == H1 and record["medical_record_no"] == "MR-001"

    user = container.user_repo.get_by_id(submitted_patient.id)
    assert user.binding_status is BindingStatus.VERIFIED and user.role is Role.PATIENT
    assert container.line_client.pushes[-1][0] == "Upatient"

    detail = client.get(
        f"/api/v1/admin/patients/{submitted_patient.id}", headers=headers
    ).get_json()["data"]
    assert detail["submissions"][0]["status"] == "reviewed"
    # 建檔後出現在「已建檔」清單，且驗證後病人可讀自己的 profile
    active = client.get("/api/v1/admin/patients?status=active", headers=headers).get_json()["data"]
    assert active[0]["medical_record_no"] == "MR-001"
    assert client.get("/api/v1/liff/profile", headers=auth_header(user)).status_code == 200

    # 身分證不存明碼也不存密文：任何角色（含 HOSPITAL_ADMIN）都只拿得到遮罩
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    detail = client.get(
        f"/api/v1/admin/patients/{submitted_patient.id}", headers=auth_header(admin)
    ).get_json()["data"]
    assert detail["profile"]["national_id"] == "******6789"
    assert "national_id_fingerprint" not in detail["profile"]


def test_verify_requires_submission(client, make_user, auth_header, staff):
    user = make_user()
    client.put(
        "/api/v1/liff/intake/steps/6",
        json={"preferred_hospital_id": H1, "partial": True},
        headers=auth_header(user),
    )
    # 草稿對院所不可見（403）；SUPER_ADMIN 看得到但仍不能核對（422）
    resp = client.post(
        f"/api/v1/admin/patients/{user.id}/verify", json={}, headers=auth_header(staff)
    )
    assert resp.status_code == 403
    superadmin = make_user(Role.SUPER_ADMIN)
    resp = client.post(
        f"/api/v1/admin/patients/{user.id}/verify",
        json={"hospital_id": H1},
        headers=auth_header(superadmin),
    )
    assert resp.status_code == 422


def test_reject_returns_to_unbound_and_notifies(
    client, container, submitted_patient, staff, auth_header
):
    assert (
        client.post(
            f"/api/v1/admin/patients/{submitted_patient.id}/reject",
            json={"reason": ""},
            headers=auth_header(staff),
        ).status_code
        == 422
    )
    resp = client.post(
        f"/api/v1/admin/patients/{submitted_patient.id}/reject",
        json={"reason": "身分證字號與證件不符"},
        headers=auth_header(staff),
    )
    assert resp.status_code == 200
    assert (
        container.user_repo.get_by_id(submitted_patient.id).binding_status is BindingStatus.UNBOUND
    )
    assert "身分證字號與證件不符" in container.line_client.pushes[-1][1][0].text
    # 退回後 submitted_at 清空：院所看不到、也不能核對，直到病人重新送出
    assert (
        client.get(
            f"/api/v1/admin/patients/{submitted_patient.id}", headers=auth_header(staff)
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/admin/patients?status=all", headers=auth_header(staff)).get_json()[
            "data"
        ]
        == []
    )


# ---- 修正 / 就診 / 風險 / 追蹤 / 備註 ----
@pytest.fixture()
def verified_patient(client, submitted_patient, staff, auth_header):
    client.post(
        f"/api/v1/admin/patients/{submitted_patient.id}/verify", json={}, headers=auth_header(staff)
    )
    return submitted_patient


def test_correct_profile_records_corrections(client, verified_patient, staff, auth_header):
    headers = auth_header(staff)
    url = f"/api/v1/admin/patients/{verified_patient.id}"
    resp = client.patch(
        url + "/profile",
        json={"changes": {"phone": "0987654321"}, "note": "電話更正"},
        headers=headers,
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["profile"]["phone"] == "0987654321"
    detail = client.get(url, headers=headers).get_json()["data"]
    assert detail["reviews"][0]["corrections"]["phone"] == {
        "from": "0912345678",
        "to": "0987654321",
    }
    # 身分證與同意事項不可由院方修改；格式錯誤會被擋
    assert (
        client.patch(
            url + "/profile", json={"changes": {"national_id": "B123456789"}}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.patch(
            url + "/profile", json={"changes": {"phone": "123"}}, headers=headers
        ).status_code
        == 422
    )


def test_correction_requires_record(client, submitted_patient, staff, auth_header):
    resp = client.patch(
        f"/api/v1/admin/patients/{submitted_patient.id}/profile",
        json={"changes": {"phone": "0987654321"}},
        headers=auth_header(staff),
    )
    assert resp.status_code == 409


def test_encounter_flag_followup_note_flow(client, container, verified_patient, staff, auth_header):
    headers = auth_header(staff)
    url = f"/api/v1/admin/patients/{verified_patient.id}"
    enc = client.post(
        url + "/encounters",
        json={
            "encounter_type": "inpatient",
            "visit_date": "2026-09-01",
            "discharged_at": "2026-09-10",
            "discharge_code": "DM-01",
            "diagnoses": [{"icd10_code": "E11", "description": "第二型糖尿病"}],
        },
        headers=headers,
    )
    assert enc.status_code == 201, enc.get_json()
    enc_id = enc.get_json()["data"]["id"]
    assert (
        client.patch(
            url + f"/encounters/{enc_id}", json={"department": "新陳代謝科"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            url + "/encounters",
            json={"encounter_type": "spa", "visit_date": "2026-09-01"},
            headers=headers,
        ).status_code
        == 422
    )
    assert container.patient_record_repo.latest_discharge_code(verified_patient.id) == "DM-01"

    flag = client.post(url + "/flags", json={"flag_type": "fall", "level": "high"}, headers=headers)
    assert flag.status_code == 201
    assert (
        client.patch(
            url + f"/flags/{flag.get_json()['data']['id']}", json={"active": False}, headers=headers
        ).get_json()["data"]["active"]
        is False
    )

    fu = client.post(
        url + "/follow-ups", json={"kind": "revisit", "due_date": "2026-09-20"}, headers=headers
    )
    assert fu.status_code == 201
    assert (
        client.patch(
            url + f"/follow-ups/{fu.get_json()['data']['id']}",
            json={"status": "done"},
            headers=headers,
        ).status_code
        == 200
    )

    assert (
        client.post(url + "/notes", json={"body": "注意血糖"}, headers=headers).status_code == 201
    )
    assert client.post(url + "/notes", json={"body": "  "}, headers=headers).status_code == 422

    detail = client.get(url, headers=headers).get_json()["data"]
    assert detail["encounters"][0]["department"] == "新陳代謝科"
    assert (
        len(detail["flags"]) == 1 and len(detail["follow_ups"]) == 1 and len(detail["notes"]) == 1
    )

    dash = client.get("/api/v1/admin/dashboard", headers=headers).get_json()["data"]
    assert set(dash) == {"pending_patients", "due_follow_ups", "open_feedback"}


def test_other_hospital_cannot_touch_record(client, verified_patient, make_user, auth_header):
    other = make_user(Role.MEDICAL_STAFF, hospital_id=H2)
    resp = client.post(
        f"/api/v1/admin/patients/{verified_patient.id}/flags",
        json={"flag_type": "fall"},
        headers=auth_header(other),
    )
    assert resp.status_code == 403


# ---- 人員管理 API ----
def test_list_users_scoped_to_hospital(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    make_user(Role.HOSPITAL_STAFF, hospital_id=H1)
    make_user(Role.HOSPITAL_STAFF, hospital_id=H2)
    rows = client.get("/api/v1/admin/users", headers=auth_header(admin)).get_json()["data"]
    assert {r["hospital_id"] for r in rows} == {H1}
    assert (
        client.get(
            "/api/v1/admin/users",
            headers=auth_header(make_user(Role.HOSPITAL_STAFF, hospital_id=H1)),
        ).status_code
        == 403
    )


# ---- 後台頁面 ----
@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/patients",
        "/admin/patients/abc",
        "/admin/feedback",
        "/admin/staff",
        "/admin/settings",
        "/admin/permissions",
        "/admin/audit",
    ],
)
def test_admin_pages_render_shell(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "MediAgent+ 後台" in resp.get_data(as_text=True)


def test_admin_login_page_renders_with_line_button(client):
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "院方後台登入" in html
    assert "/api/v1/auth/line/login?redirect_to=%2Fadmin" in html or "redirect_to=/admin" in html


def test_admin_login_page_keeps_next_and_shows_error(client):
    html = client.get("/admin/login?next=/admin/patients&error=unauthorized").get_data(as_text=True)
    assert "redirect_to=%2Fadmin%2Fpatients" in html or "redirect_to=/admin/patients" in html
    assert "請先登入" in html
    # next 只能是後台路徑，其他一律回 /admin
    html = client.get("/admin/login?next=https://evil.com").get_data(as_text=True)
    assert "evil.com" not in html
    html = client.get("/admin/login?next=/liff/intake").get_data(as_text=True)
    assert "redirect_to=%2Fliff" not in html and "/liff/intake" not in html


# ---- 鎖定 / 解鎖 / 稽核 ----
def test_lock_unlock_and_audit(client, container, submitted_patient, staff, make_user, auth_header):
    url = f"/api/v1/admin/patients/{submitted_patient.id}"
    headers = auth_header(staff)
    assert client.post(url + "/unlock", json={}, headers=headers).status_code == 409
    resp = client.post(url + "/lock", json={"reason": "證件疑似冒用"}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["data"]["binding_status"] == "locked"
    assert (
        container.user_repo.get_by_id(submitted_patient.id).binding_status is BindingStatus.LOCKED
    )
    # 鎖定中：病人不能填、院所不能核對、但列表「已鎖定」看得到
    patient_headers = auth_header(container.user_repo.get_by_id(submitted_patient.id))
    assert (
        client.post("/api/v1/liff/intake/submit", json={}, headers=patient_headers).status_code
        == 403
    )
    assert client.post(url + "/verify", json={}, headers=headers).status_code == 409
    locked = client.get("/api/v1/admin/patients?status=locked", headers=headers).get_json()["data"]
    assert [p["user_id"] for p in locked] == [submitted_patient.id]

    assert client.post(url + "/unlock", json={}, headers=headers).status_code == 200
    assert (
        container.user_repo.get_by_id(submitted_patient.id).binding_status is BindingStatus.UNBOUND
    )

    # 稽核：鎖定 + 解鎖各一筆，只有 AUDIT_READ 能看
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    assert client.get("/api/v1/admin/audit", headers=headers).status_code == 403
    logs = client.get("/api/v1/admin/audit", headers=auth_header(admin)).get_json()["data"]
    assert [entry["action"] for entry in logs] == ["patient.unlock", "patient.lock"]
    # 鎖定 / 解鎖都是 staff 做的
    assert logs[0]["actor_id"] == staff.id and logs[0]["target_user_id"] == submitted_patient.id


def test_verify_twice_only_updates_mrn(client, container, verified_patient, staff, auth_header):
    url = f"/api/v1/admin/patients/{verified_patient.id}/verify"
    pushes_before = len(container.line_client.pushes)
    resp = client.post(url, json={"medical_record_no": "MR-999"}, headers=auth_header(staff))
    assert resp.status_code == 200 and resp.get_json()["data"]["medical_record_no"] == "MR-999"
    assert len(container.line_client.pushes) == pushes_before  # 不再重複推播


def test_draft_not_visible_to_hospital(client, make_user, auth_header, staff):
    user = make_user()
    client.put(
        "/api/v1/liff/intake/steps/6",
        json={"preferred_hospital_id": H1, "partial": True},
        headers=auth_header(user),
    )
    assert (
        client.get(f"/api/v1/admin/patients/{user.id}", headers=auth_header(staff)).status_code
        == 403
    )


def test_super_admin_list_dedupes_multi_hospital_records(
    client, container, verified_patient, make_user, auth_header
):
    other_staff = make_user(Role.MEDICAL_STAFF, hospital_id=H2)
    container.patient_record_repo.create(user_id=verified_patient.id, hospital_id=H2)
    superadmin = make_user(Role.SUPER_ADMIN)
    rows = client.get(
        "/api/v1/admin/patients?status=active", headers=auth_header(superadmin)
    ).get_json()["data"]
    assert [r["user_id"] for r in rows] == [verified_patient.id]
    # 多院所建檔的病人，SUPER_ADMIN 寫入操作要求以院所帳號操作
    assert (
        client.post(
            f"/api/v1/admin/patients/{verified_patient.id}/notes",
            json={"body": "x"},
            headers=auth_header(superadmin),
        ).status_code
        == 422
    )
    assert other_staff.hospital_id == H2


def test_staff_without_hospital_cannot_list_users_or_feedback(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=None)
    assert client.get("/api/v1/admin/users", headers=auth_header(admin)).status_code == 403
    assert client.get("/api/v1/admin/feedback", headers=auth_header(admin)).status_code == 403


def test_hospital_admin_can_find_unassigned_newcomer_and_offboard(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    newcomer = make_user(Role.GUEST)
    other_hospital_user = make_user(Role.HOSPITAL_STAFF, hospital_id=H2)
    rows = client.get("/api/v1/admin/users?staff=0", headers=auth_header(admin)).get_json()["data"]
    ids = {r["user_id"] for r in rows}
    assert newcomer.id in ids and other_hospital_user.id not in ids
    # 指派後歸本院；降回 GUEST（離職）也可以
    resp = client.put(
        f"/api/v1/admin/users/{newcomer.id}/role",
        json={"role": "hospital_staff"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["hospital_id"] == H1
    resp = client.put(
        f"/api/v1/admin/users/{newcomer.id}/role",
        json={"role": "guest"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["role"] == "guest"


def test_role_assignment_guards(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    peer_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id=H1)
    superadmin_in_h1 = make_user(Role.SUPER_ADMIN, hospital_id=H1)
    # 不能改自己
    assert (
        client.put(
            f"/api/v1/admin/users/{admin.id}/role",
            json={"role": "hospital_staff"},
            headers=auth_header(admin),
        ).status_code
        == 403
    )
    # 同院另一位管理員可以調整（在可指派集合內），但 SUPER_ADMIN 不行
    assert (
        client.put(
            f"/api/v1/admin/users/{peer_admin.id}/role",
            json={"role": "medical_staff"},
            headers=auth_header(admin),
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/admin/users/{superadmin_in_h1.id}/role",
            json={"role": "hospital_staff"},
            headers=auth_header(admin),
        ).status_code
        == 403
    )
    # 院方角色必須有院所；SUPER_ADMIN 可清空院所（給 super_admin 用）
    superadmin = make_user(Role.SUPER_ADMIN)
    target = make_user(Role.HOSPITAL_STAFF, hospital_id=H1)
    assert (
        client.put(
            f"/api/v1/admin/users/{target.id}/role",
            json={"role": "hospital_staff", "hospital_id": ""},
            headers=auth_header(superadmin),
        ).status_code
        == 422
    )
    resp = client.put(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "super_admin", "hospital_id": ""},
        headers=auth_header(superadmin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["hospital_id"] is None


def test_correct_profile_cannot_move_patient_to_other_hospital(
    client, verified_patient, staff, auth_header
):
    resp = client.patch(
        f"/api/v1/admin/patients/{verified_patient.id}/profile",
        json={"changes": {"preferred_hospital_id": H2}},
        headers=auth_header(staff),
    )
    assert resp.status_code == 422
