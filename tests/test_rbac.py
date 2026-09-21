from app.domain.enums import BindingStatus, Permission, Role
from app.domain.rbac import ROLE_PERMISSIONS, AccessPolicy, permissions_for


def test_every_role_has_permissions_defined():
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_guest_only_has_public_permissions(principal_factory):
    guest = principal_factory(Role.GUEST)
    assert AccessPolicy.has_permission(guest, Permission.CONSULT_SYMPTOM)
    assert AccessPolicy.has_permission(guest, Permission.HANDOFF_REQUEST)
    assert not AccessPolicy.has_permission(guest, Permission.REMINDER_MANAGE_OWN)
    assert not AccessPolicy.has_permission(guest, Permission.PATIENT_READ)


def test_patient_and_family_share_bound_user_permissions():
    assert permissions_for(Role.PATIENT) == permissions_for(Role.FAMILY)
    assert Permission.EDUCATION_PERSONAL in permissions_for(Role.PATIENT)


def test_staff_cannot_use_end_user_personal_features():
    assert Permission.REMINDER_MANAGE_OWN not in permissions_for(Role.CS_AGENT)
    assert Permission.STAFF_MANAGE not in permissions_for(Role.CS_AGENT)
    assert Permission.HANDOFF_ACCEPT in permissions_for(Role.CS_AGENT)


def test_hospital_admin_is_superset_of_cs_and_medical():
    admin = permissions_for(Role.HOSPITAL_ADMIN)
    assert permissions_for(Role.CS_AGENT) <= admin
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
    assert AccessPolicy.can_assign_role(hospital_admin, Role.CS_AGENT)
    assert not AccessPolicy.can_assign_role(hospital_admin, Role.SUPER_ADMIN)
    assert not AccessPolicy.can_assign_role(principal_factory(Role.CS_AGENT), Role.CS_AGENT)


# ---- 透過 API 驗證裝飾器 ----
def test_admin_endpoint_requires_login(client):
    assert client.get("/api/v1/admin/permissions").status_code == 401


def test_admin_endpoint_rejects_end_user(client, make_user, auth_header):
    patient = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    assert client.get("/api/v1/admin/permissions", headers=auth_header(patient)).status_code == 403


def test_admin_endpoint_allows_staff(client, make_user, auth_header):
    agent = make_user(Role.CS_AGENT, hospital_id="H1")
    resp = client.get("/api/v1/admin/permissions", headers=auth_header(agent))
    assert resp.status_code == 200
    assert "cs_agent" in resp.get_json()["data"]


def test_set_role_respects_hospital_boundary(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    other_staff = make_user(Role.CS_AGENT, hospital_id="H2")
    resp = client.put(
        f"/api/v1/admin/users/{other_staff.id}/role",
        json={"role": "medical_staff"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 403

    guest = make_user(Role.GUEST)
    resp = client.put(
        f"/api/v1/admin/users/{guest.id}/role",
        json={"role": "cs_agent"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["role"] == "cs_agent"
    assert resp.get_json()["data"]["hospital_id"] == "H1"


def test_verified_only_endpoint(client, make_user, auth_header):
    guest = make_user(Role.GUEST)
    assert client.get("/api/v1/liff/profile", headers=auth_header(guest)).status_code == 403
    patient = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    assert client.get("/api/v1/liff/profile", headers=auth_header(patient)).status_code == 200
