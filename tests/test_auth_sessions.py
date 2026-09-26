"""登入工作階段：refresh token 輪替、重用偵測、登出撤銷、LINE token 同步；Redis 版 state / rate limit。"""

from __future__ import annotations

import time

import pytest

from app.auth.line_login import LineTokenResponse
from app.auth.state_store import RedisOAuthStateStore
from app.core.exceptions import AuthenticationError
from app.core.ratelimit import RedisRateLimiter
from app.domain.enums import BindingStatus, Role


class FakeLineLogin:
    """記錄對 LINE token 端點的呼叫；`fail_refresh=True` 模擬 LINE 回 invalid_grant。"""

    def __init__(self) -> None:
        self.refreshed: list[str] = []
        self.revoked: list[str] = []
        self.fail_refresh = False

    def refresh_tokens(self, refresh_token: str) -> LineTokenResponse:
        self.refreshed.append(refresh_token)
        if self.fail_refresh:
            raise AuthenticationError("LINE refresh token 已失效")
        return LineTokenResponse(
            access_token="line-access-2",
            id_token=None,
            refresh_token="line-refresh-2",
            expires_in=2592000,
            scope="profile openid",
        )

    def revoke_token(self, access_token: str) -> None:
        self.revoked.append(access_token)


@pytest.fixture()
def fake_line(container):
    fake = FakeLineLogin()
    container.auth_sessions._line_login = fake
    return fake


def _refresh(client, token):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": token})


def test_refresh_rotates_and_detects_reuse(client, container, make_user):
    user = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    principal = container.users.to_principal(user)
    pair = container.auth_sessions.start(principal, provider="liff")

    first = _refresh(client, pair.refresh_token)
    assert first.status_code == 200
    new_pair = first.get_json()["data"]
    assert new_pair["refresh_token"] != pair.refresh_token
    # 新 token 可用
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer " + new_pair["access_token"]}
        ).status_code
        == 200
    )

    # 舊 token 再用一次：重用偵測 → 整個 family 撤銷，連新 token 也失效
    assert _refresh(client, pair.refresh_token).status_code == 401
    assert _refresh(client, new_pair["refresh_token"]).status_code == 401


def test_logout_revokes_family_and_line_token(client, container, make_user, fake_line):
    user = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    principal = container.users.to_principal(user)
    line_tokens = LineTokenResponse("line-access-1", None, "line-refresh-1", 100, "profile")
    pair = container.auth_sessions.start(principal, provider="line_login", line_tokens=line_tokens)

    assert (
        client.post("/api/v1/auth/logout", json={"refresh_token": pair.refresh_token}).status_code
        == 200
    )
    assert fake_line.revoked == ["line-access-1"]
    assert _refresh(client, pair.refresh_token).status_code == 401
    # 沒帶 token / 亂填也回 200（視為已登出，不洩漏 token 是否存在）
    assert client.post("/api/v1/auth/logout", json={"refresh_token": "nope"}).status_code == 200


def test_refresh_syncs_with_line_and_fails_when_line_revokes(
    client, container, make_user, fake_line
):
    user = make_user(Role.PATIENT, BindingStatus.VERIFIED)
    principal = container.users.to_principal(user)
    line_tokens = LineTokenResponse("line-access-1", None, "line-refresh-1", 100, "profile")
    pair = container.auth_sessions.start(principal, provider="line_login", line_tokens=line_tokens)

    resp = _refresh(client, pair.refresh_token)
    assert resp.status_code == 200 and fake_line.refreshed == ["line-refresh-1"]
    second = resp.get_json()["data"]["refresh_token"]

    # 使用者在 LINE 端解除授權：LINE refresh 失敗 → 自家 session 也失效
    fake_line.fail_refresh = True
    resp = _refresh(client, second)
    assert resp.status_code == 401 and fake_line.refreshed[-1] == "line-refresh-2"
    fake_line.fail_refresh = False
    assert _refresh(client, second).status_code == 401  # 已被撤銷，不會再打 LINE


def test_line_tokens_are_encrypted_at_rest(container, make_user):
    user = make_user()
    principal = container.users.to_principal(user)
    line_tokens = LineTokenResponse("line-access-1", None, "line-refresh-1", 100, "profile")
    pair = container.auth_sessions.start(principal, provider="line_login", line_tokens=line_tokens)
    jti = container.jwt.decode(pair.refresh_token, expected_type="refresh")["jti"]
    row = container.auth_session_repo.get(jti)
    assert "line-refresh-1" not in row["line_refresh_token_encrypted"]
    assert container.cipher.decrypt(row["line_refresh_token_encrypted"]) == "line-refresh-1"


def test_locking_patient_revokes_all_sessions(client, container, make_user, auth_header):
    from tests.test_intake import fill_all

    patient = make_user(Role.GUEST, line_user_id="Ulock")
    assert fill_all(client, auth_header(patient)).status_code == 200
    principal = container.users.to_principal(container.user_repo.get_by_id(patient.id))
    pair = container.auth_sessions.start(principal, provider="liff")
    staff = make_user(Role.MEDICAL_STAFF, hospital_id="H1")
    assert (
        client.post(
            f"/api/v1/admin/patients/{patient.id}/lock",
            json={"reason": "x"},
            headers=auth_header(staff),
        ).status_code
        == 200
    )
    assert _refresh(client, pair.refresh_token).status_code == 401


def test_old_style_refresh_token_without_jti_is_rejected(client, container, make_user):
    user = make_user()
    pair = container.jwt.issue_pair(container.users.to_principal(user))  # 沒有 jti
    assert _refresh(client, pair.refresh_token).status_code == 401


# ---- Redis 實作（用假的 client 驗證指令用法）----
class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}

    def _alive(self, key):
        item = self.store.get(key)
        if item is None:
            return None
        if item[1] is not None and item[1] < time.time():
            del self.store[key]
            return None
        return item

    def set(self, key, value, ex=None):
        self.store[key] = (value, time.time() + ex if ex else None)

    def getdel(self, key):
        item = self._alive(key)
        if item is None:
            return None
        del self.store[key]
        return item[0]

    def incr(self, key):
        item = self._alive(key)
        value = int(item[0]) + 1 if item else 1
        self.store[key] = (str(value), item[1] if item else None)
        return value

    def expire(self, key, seconds):
        item = self._alive(key)
        if item:
            self.store[key] = (item[0], time.time() + seconds)


def test_redis_state_store_is_single_use():
    store = RedisOAuthStateStore(FakeRedis())
    record = store.issue(60, redirect_to="/admin")
    consumed = store.consume(record.state)
    assert (
        consumed is not None and consumed.nonce == record.nonce and consumed.redirect_to == "/admin"
    )
    assert store.consume(record.state) is None


def test_redis_rate_limiter_counts_and_expires():
    fake = FakeRedis()
    limiter = RedisRateLimiter(fake)
    assert [limiter.hit("k", 2, 60) for _ in range(3)] == [True, True, False]
    assert fake.store["ratelimit:k"][1] is not None  # 第一次命中時設定了過期
