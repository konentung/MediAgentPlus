from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app import create_app
from app.domain.enums import BindingStatus, Role
from app.domain.principal import Principal
from app.repositories.user_repository import UserRecord


class FakeLineClient:
    """記錄所有 reply / push，不真的呼叫 LINE。"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, list]] = []
        self.pushes: list[tuple[str, list]] = []

    def reply(self, reply_token, messages):
        self.replies.append((reply_token, list(messages)))

    def push(self, to, messages):
        self.pushes.append((to, list(messages)))


@pytest.fixture()
def app():
    app = create_app("testing")
    app.extensions["container"].line_client = FakeLineClient()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def container(app):
    return app.extensions["container"]


@pytest.fixture()
def make_user(container):
    def _make(
        role: Role = Role.GUEST,
        binding: BindingStatus = BindingStatus.UNBOUND,
        hospital_id=None,
        line_user_id=None,
    ):
        user = UserRecord(
            id=UserRecord.new_id(),
            line_user_id=line_user_id or f"U{UserRecord.new_id()[:8]}",
            role=role,
            binding_status=binding,
            hospital_id=hospital_id,
        )
        return container.user_repo.save(user)

    return _make


@pytest.fixture()
def auth_header(container):
    def _header(user) -> dict[str, str]:
        principal = container.users.to_principal(user)
        return {"Authorization": f"Bearer {container.jwt.issue_pair(principal).access_token}"}

    return _header


@pytest.fixture()
def line_signature(app):
    def _sign(body: str) -> str:
        secret = app.config["LINE_CHANNEL_SECRET"].encode()
        return base64.b64encode(hmac.new(secret, body.encode(), hashlib.sha256).digest()).decode()

    return _sign


@pytest.fixture()
def post_line_event(client, line_signature):
    def _post(event: dict):
        body = json.dumps({"destination": "Uxxx", "events": [event]})
        return client.post(
            "/line/webhook",
            data=body,
            headers={"X-Line-Signature": line_signature(body), "Content-Type": "application/json"},
        )

    return _post


@pytest.fixture()
def principal_factory():
    def _make(role: Role, **kw) -> Principal:
        return Principal(user_id="u1", role=role, **kw)

    return _make
