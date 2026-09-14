"""LINE Login（OAuth 2.0 Authorization Code + OpenID Connect）。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from app.auth.line_identity import LineIdentity, LineIdentityVerifier
from app.auth.state_store import OAuthStateStore
from app.core.exceptions import AuthenticationError, ExternalServiceError

LINE_AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"


@dataclass(frozen=True)
class LineTokenResponse:
    access_token: str
    id_token: str | None
    refresh_token: str | None
    expires_in: int
    scope: str


@dataclass(frozen=True)
class LineLoginResult:
    identity: LineIdentity
    tokens: LineTokenResponse
    redirect_to: str | None


class LineLoginService:
    def __init__(
        self,
        *,
        channel_id: str,
        channel_secret: str,
        redirect_uri: str,
        scope: str,
        state_store: OAuthStateStore,
        state_ttl_seconds: int,
        verifier: LineIdentityVerifier,
        session: requests.Session | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._channel_id = channel_id
        self._channel_secret = channel_secret
        self._redirect_uri = redirect_uri
        self._scope = scope
        self._states = state_store
        self._state_ttl = state_ttl_seconds
        self._verifier = verifier
        self._session = session or requests.Session()
        self._timeout = timeout

    def build_authorize_url(
        self, redirect_to: str | None = None, bot_prompt: str | None = "aggressive"
    ) -> str:
        """產生導向 LINE 授權頁的網址；bot_prompt 讓使用者登入時順便加官方帳號好友。"""
        state = self._states.issue(self._state_ttl, redirect_to=redirect_to)
        params = {
            "response_type": "code",
            "client_id": self._channel_id,
            "redirect_uri": self._redirect_uri,
            "state": state.state,
            "scope": self._scope,
            "nonce": state.nonce,
        }
        if bot_prompt:
            params["bot_prompt"] = bot_prompt
        return f"{LINE_AUTHORIZE_URL}?{urlencode(params)}"

    def handle_callback(
        self, code: str | None, state: str | None, error: str | None = None
    ) -> LineLoginResult:
        if error:
            raise AuthenticationError(f"LINE 登入被拒絕: {error}")
        if not code or not state:
            raise AuthenticationError("缺少 code 或 state")
        record = self._states.consume(state)
        if record is None:
            raise AuthenticationError("state 無效或已過期，請重新登入")

        tokens = self.exchange_code(code)
        if tokens.id_token:
            identity = self._verifier.verify_id_token(tokens.id_token, nonce=record.nonce)
        else:
            identity = self._verifier.fetch_profile(tokens.access_token)
        return LineLoginResult(identity=identity, tokens=tokens, redirect_to=record.redirect_to)

    def exchange_code(self, code: str) -> LineTokenResponse:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._channel_id,
            "client_secret": self._channel_secret,
        }
        try:
            resp = self._session.post(LINE_TOKEN_URL, data=data, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ExternalServiceError("無法連線 LINE token 端點") from exc
        if resp.status_code != 200:
            raise AuthenticationError("授權碼交換失敗")
        body = resp.json()
        return LineTokenResponse(
            access_token=body["access_token"],
            id_token=body.get("id_token"),
            refresh_token=body.get("refresh_token"),
            expires_in=int(body.get("expires_in", 0)),
            scope=body.get("scope", ""),
        )
