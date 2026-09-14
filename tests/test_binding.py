"""B1–B9 綁定流程。"""

from app.domain.enums import BindingStatus, Role
from app.repositories.binding_repository import RosterEntry

PROFILE = {
    "name": "王小明",
    "birth_date": "1990-01-01",
    "phone_last4": "5678",
    "hospital_id": "H1",
    "department": "心臟內科",
}


def _walk_to_verify(client, headers, method="sms_otp", **extra):
    assert client.post("/api/v1/liff/binding/start", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/v1/liff/binding/identity", json={"identity_type": "self"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post("/api/v1/liff/binding/profile", json=PROFILE, headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/liff/binding/verification", json={"method": method, **extra}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/liff/binding/consent", json={"accepted": True}, headers=headers
        ).status_code
        == 200
    )


def test_family_requires_relation(client, make_user, auth_header):
    headers = auth_header(make_user())
    client.post("/api/v1/liff/binding/start", headers=headers)
    resp = client.post(
        "/api/v1/liff/binding/identity", json={"identity_type": "family"}, headers=headers
    )
    assert resp.status_code == 422


def test_happy_path_b7(client, container, make_user, auth_header):
    user = make_user()
    headers = auth_header(user)
    container.roster_repo.save(
        RosterEntry(
            id="r1", hospital_id="H1", name="王小明", birth_date="1990-01-01", phone_last4="5678"
        )
    )
    _walk_to_verify(client, headers)
    otp = container.binding_repo.get(user.id).verification_code
    resp = client.post("/api/v1/liff/binding/verify", json={"code": otp}, headers=headers)
    assert resp.get_json()["data"]["outcome"] == "verified"
    updated = container.user_repo.get_by_id(user.id)
    assert updated.binding_status is BindingStatus.VERIFIED
    assert updated.role is Role.PATIENT
    assert updated.hospital_id == "H1"


def test_discharge_code_path(client, container, make_user, auth_header):
    user = make_user()
    headers = auth_header(user)
    container.roster_repo.save(
        RosterEntry(
            id="r1",
            hospital_id="H1",
            name="王小明",
            birth_date="1990-01-01",
            phone_last4="5678",
            discharge_code="DC-999",
        )
    )
    _walk_to_verify(client, headers, method="discharge_code", discharge_code="DC-999")
    resp = client.post("/api/v1/liff/binding/verify", json={"code": "DC-999"}, headers=headers)
    assert resp.get_json()["data"]["outcome"] == "verified"


def test_three_failures_escalate_b8(client, container, make_user, auth_header):
    user = make_user()
    headers = auth_header(user)
    _walk_to_verify(client, headers)  # 名單無此人 → 比對失敗
    outcomes = []
    for _ in range(3):
        resp = client.post("/api/v1/liff/binding/verify", json={"code": "000000"}, headers=headers)
        outcomes.append(resp.get_json()["data"]["outcome"])
        if outcomes[-1] == "mismatch":
            # 退回 B3 重填 → 重新走 B4、B5
            client.post("/api/v1/liff/binding/profile", json=PROFILE, headers=headers)
            client.post(
                "/api/v1/liff/binding/verification", json={"method": "sms_otp"}, headers=headers
            )
            client.post("/api/v1/liff/binding/consent", json={"accepted": True}, headers=headers)
    assert outcomes == ["mismatch", "mismatch", "escalated"]
    assert len(container.handoff_repo.list_open()) == 1
    # 已轉真人後不得再操作
    assert (
        client.post("/api/v1/liff/binding/verify", json={"code": "1"}, headers=headers).status_code
        == 409
    )


def test_bound_by_other_line_locks_b9(client, container, make_user, auth_header):
    user = make_user()
    headers = auth_header(user)
    container.roster_repo.save(
        RosterEntry(
            id="r1",
            hospital_id="H1",
            name="王小明",
            birth_date="1990-01-01",
            phone_last4="5678",
            bound_line_user_id="Uother",
        )
    )
    _walk_to_verify(client, headers)
    otp = container.binding_repo.get(user.id).verification_code
    resp = client.post("/api/v1/liff/binding/verify", json={"code": otp}, headers=headers)
    assert resp.get_json()["data"]["outcome"] == "locked"
    assert container.user_repo.get_by_id(user.id).binding_status is BindingStatus.LOCKED
    assert len(container.handoff_repo.list_open()) == 1


def test_revoke_binding(client, container, make_user, auth_header):
    user = make_user(Role.PATIENT, BindingStatus.VERIFIED, hospital_id="H1")
    resp = client.delete("/api/v1/liff/binding", headers=auth_header(user))
    assert resp.status_code == 200
    updated = container.user_repo.get_by_id(user.id)
    assert updated.role is Role.GUEST and updated.binding_status is BindingStatus.UNBOUND
