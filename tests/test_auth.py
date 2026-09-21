import json
import re
from datetime import timedelta

import pytest

from app.auth.jwt_service import JwtService
from app.auth.line_identity import LineIdentity
from app.core.exceptions import AuthenticationError
from app.domain.enums import Role
from app.domain.principal import Principal


def test_jwt_roundtrip():
    svc = JwtService("s", "HS256", timedelta(minutes=5), timedelta(days=1))
    pair = svc.issue_pair(Principal(user_id="u1", role=Role.PATIENT, line_user_id="L1"))
    claims = svc.decode(pair.access_token)
    assert claims["sub"] == "u1" and claims["role"] == "patient"
    with pytest.raises(AuthenticationError):
        svc.decode(pair.refresh_token, expected_type="access")


def test_jwt_expired():
    svc = JwtService("s", "HS256", timedelta(seconds=-1), timedelta(days=1))
    pair = svc.issue_pair(Principal(user_id="u1", role=Role.GUEST))
    with pytest.raises(AuthenticationError):
        svc.decode(pair.access_token)


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    )


def test_line_login_redirects_to_line(client):
    resp = client.get("/api/v1/auth/line/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://access.line.me/oauth2/v2.1/authorize?")
    assert "state=" in resp.headers["Location"] and "nonce=" in resp.headers["Location"]


def test_line_callback_rejects_bad_state(client):
    resp = client.get("/api/v1/auth/line/callback?code=abc&state=forged")
    assert resp.status_code == 401


def test_line_callback_issues_jwt(client, container, monkeypatch):
    state = container.state_store.issue(600)
    monkeypatch.setattr(
        container.line_login,
        "exchange_code",
        lambda code: type(
            "T",
            (),
            {
                "access_token": "a",
                "id_token": "i",
                "refresh_token": None,
                "expires_in": 1,
                "scope": "",
            },
        )(),
    )
    monkeypatch.setattr(
        container.line_login._verifier,
        "verify_id_token",
        lambda id_token, nonce=None: LineIdentity(line_user_id="Uabc", display_name="小明"),
    )
    resp = client.get(f"/api/v1/auth/line/callback?code=abc&state={state.state}")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/html")
    assert "小明" in resp.get_data(as_text=True)
    match = re.search(
        r'<script id="auth-tokens" type="application/json">(.*?)</script>',
        resp.get_data(as_text=True),
        re.DOTALL,
    )
    token = json.loads(match.group(1))["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.get_json()["data"]["line_user_id"] == "Uabc"
    assert me.get_json()["data"]["role"] == "guest"


def test_liff_auth_issues_jwt(client, container, monkeypatch):
    monkeypatch.setattr(
        container.liff_auth._verifier,
        "verify_id_token",
        lambda id_token, nonce=None: LineIdentity(line_user_id="Uliff", display_name="LIFF 使用者"),
    )
    resp = client.post("/api/v1/liff/auth", json={"id_token": "x"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["principal"]["line_user_id"] == "Uliff"
    assert "access_token" in data["tokens"]
    assert client.post("/api/v1/liff/auth", json={}).status_code == 422
