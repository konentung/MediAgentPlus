from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app import create_app
from app.domain.enums import BindingStatus, Role
from app.domain.principal import Principal
from app.extensions import db
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
    # 全測試期間保持一個 app context，讓測試可以在 client 請求之外直接呼叫
    # container 內的 repository（沿用既有測試寫法）。這顆 context 的物件參考
    # 交給 `client` fixture，讓它在每次送出請求前後暫時 pop/push，避免 Flask
    # 沿用同一個 app context 導致 flask.g（含 g.principal）跨請求殘留。
    ctx = app.app_context()
    ctx.push()
    app.extensions["_test_app_ctx"] = ctx
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture()
def client(app):
    ctx = app.extensions["_test_app_ctx"]
    test_client = app.test_client()
    original_open = test_client.open

    def _open_with_fresh_context(*args, **kwargs):
        ctx.pop()
        try:
            return original_open(*args, **kwargs)
        finally:
            ctx.push()

    test_client.open = _open_with_fresh_context
    return test_client


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
