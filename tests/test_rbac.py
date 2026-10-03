from app.domain.enums import BindingStatus, Permission, Role
from app.domain.rbac import ROLE_PERMISSIONS, AccessPolicy, permissions_for


def test_every_role_has_permissions_defined():
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_guest_only_has_public_permissions(principal_factory):
    guest = principal_factory(Role.GUEST)
    assert AccessPolicy.has_permission(guest, Permission.CONSULT_SYMPTOM)
    assert AccessPolicy.has_permission(guest, Permission.FEEDBACK_SUBMIT)
    # 服藥提醒與處方辨識開放給所有人（不需先填基本資料）
    assert AccessPolicy.has_permission(guest, Permission.REMINDER_MANAGE_OWN)
    assert AccessPolicy.has_permission(guest, Permission.PRESCRIPTION_MANAGE_OWN)
    assert not AccessPolicy.has_permission(guest, Permission.EDUCATION_PERSONAL)
    assert not AccessPolicy.has_permission(guest, Permission.PATIENT_READ)


def test_patient_and_family_share_bound_user_permissions():
    assert permissions_for(Role.PATIENT) == permissions_for(Role.FAMILY)
    assert Permission.EDUCATION_PERSONAL in permissions_for(Role.PATIENT)


def test_staff_cannot_use_end_user_personal_features():
    assert Permission.EDUCATION_PERSONAL not in permissions_for(Role.HOSPITAL_STAFF)
    assert Permission.STAFF_MANAGE not in permissions_for(Role.HOSPITAL_STAFF)
    assert Permission.FEEDBACK_MANAGE in permissions_for(Role.HOSPITAL_STAFF)


def test_hospital_admin_is_superset_of_cs_and_medical():
    admin = permissions_for(Role.HOSPITAL_ADMIN)
    assert permissions_for(Role.HOSPITAL_STAFF) <= admin
    assert permissions_for(Role.MEDICAL_STAFF) <= admin
    assert Permission.SYSTEM_MANAGE not in admin


def test_super_admin_has_everything():
    assert permissions_for(Role.SUPER_ADMIN) == frozenset(Permission)


def test_hospital_scope(principal_factory):
    admin = principal_factory(Role.HOSPITAL_ADMIN, hospital_id="H1")
    assert AccessPolicy.can_access_hospital(admin, "H1")
    assert not AccessPolicy.can_access_hospital(admin, "H2")
    assert AccessPolicy.can_access_hospital(principal_factory(Role.SUPER_ADMIN), "H2")


def test_role_assignment_limits(principal_factory):
    hospital_admin = principal_factory(Role.HOSPITAL_ADMIN, hospital_id="H1")
    assert AccessPolicy.can_assign_role(hospital_admin, Role.HOSPITAL_STAFF)
    assert not AccessPolicy.can_assign_role(hospital_admin, Role.SUPER_ADMIN)
    assert not AccessPolicy.can_assign_role(
        principal_factory(Role.HOSPITAL_STAFF), Role.HOSPITAL_STAFF
    )


# ---- 透過 API 驗證裝飾器 ----
def test_admin_endpoint_requires_login(client):
    assert client.get("/api/v1/admin/permissions").status_code == 401


def test_admin_endpoint_rejects_end_user(client, make_user, auth_header):
    patient = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    assert client.get("/api/v1/admin/permissions", headers=auth_header(patient)).status_code == 403


def test_admin_endpoint_allows_staff(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    resp = client.get("/api/v1/admin/permissions", headers=auth_header(admin))
    assert resp.status_code == 200
    assert "hospital_staff" in resp.get_json()["data"]


def test_backoffice_page_visibility_by_role(client, make_user, auth_header):
    """院內人員：AI 知識庫 / 藥局 / 權限表都看不到；醫護只看得到藥局；院所管理員全部可看。"""
    agent = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    nurse = make_user(Role.MEDICAL_STAFF, hospital_id="H1")
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    expected = {
        "/api/v1/admin/permissions": (403, 403, 200),
        "/api/v1/admin/knowledge": (403, 403, 200),
        "/api/v1/admin/knowledge/search?q=FAQ": (403, 403, 200),
        "/api/v1/admin/pharmacies": (403, 200, 200),
    }
    for path, codes in expected.items():
        got = tuple(
            client.get(path, headers=auth_header(u)).status_code for u in (agent, nurse, admin)
        )
        assert got == codes, path


def test_set_role_respects_hospital_boundary(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    other_staff = make_user(Role.HOSPITAL_STAFF, hospital_id="H2")
    resp = client.put(
        f"/api/v1/admin/users/{other_staff.id}/role",
        json={"role": "medical_staff"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 403

    guest = make_user(Role.GUEST)
    resp = client.put(
        f"/api/v1/admin/users/{guest.id}/role",
        json={"role": "hospital_staff"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["role"] == "hospital_staff"
    assert resp.get_json()["data"]["hospital_id"] == "H1"


def test_verified_only_endpoint(client, make_user, auth_header):
    guest = make_user(Role.GUEST)
    assert client.get("/api/v1/liff/profile", headers=auth_header(guest)).status_code == 403
    patient = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    assert client.get("/api/v1/liff/profile", headers=auth_header(patient)).status_code == 200


def test_every_permission_has_chinese_description():
    from app.admin_pages import PERMISSION_DESCRIPTIONS

    assert set(PERMISSION_DESCRIPTIONS) == set(Permission)
    assert all(desc.strip() for desc in PERMISSION_DESCRIPTIONS.values())


def test_system_admin_never_belongs_to_a_hospital(client, make_user, auth_header):
    """升為系統管理員時清掉院所；院所管理員的人員清單不列系統方帳號（否則會被顯示成訪客）。"""
    superadmin = make_user(Role.SUPER_ADMIN)
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    staff = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    resp = client.put(
        f"/api/v1/admin/users/{staff.id}/role",
        json={"role": "super_admin", "hospital_id": "H1"},
        headers=auth_header(superadmin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["hospital_id"] is None

    # 舊資料：仍掛著院所的系統管理員也不出現在院所管理員的清單
    legacy = make_user(Role.SUPER_ADMIN, hospital_id="H1")
    rows = client.get("/api/v1/admin/users", headers=auth_header(admin)).get_json()["data"]
    assert legacy.id not in {r["user_id"] for r in rows}
    assert all(r["role"] != "super_admin" for r in rows)
