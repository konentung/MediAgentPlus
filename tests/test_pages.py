"""首頁與登入後導向。"""

import re
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.v1.auth import _safe_redirect_to
from app.auth.line_identity import LineIdentity


def test_home_page_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "MediAgent+" in resp.get_data(as_text=True)
    assert "/api/v1/auth/line/login" in resp.get_data(as_text=True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/settings", "/settings"),
        ("//evil.com", "/"),
        ("https://evil.com", "/"),
        ("/\\evil.com", "/"),
        ("/\\\\evil.com", "/"),
    ],
)
def test_safe_redirect_to(value, expected):
    assert _safe_redirect_to(value) == expected


def test_login_without_redirect_lands_on_home(client, container, monkeypatch):
    location = client.get("/api/v1/auth/line/login").headers["Location"]
    state = parse_qs(urlparse(location).query)["state"][0]

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
        lambda id_token, nonce=None: LineIdentity(line_user_id="Uhome", display_name="小明"),
    )
    resp = client.get(f"/api/v1/auth/line/callback?code=abc&state={state}")
    assert resp.status_code == 200
    assert re.search(r'var redirectTo = "/";', resp.get_data(as_text=True))
