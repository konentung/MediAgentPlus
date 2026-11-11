"""LINE 身分驗證共用元件：LINE Login 與 LIFF 都以 id_token 驗證身分。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.core.exceptions import AuthenticationError, ExternalServiceError

LINE_VERIFY_ID_TOKEN_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_VERIFY_ACCESS_TOKEN_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_PROFILE_URL = "https://api.line.me/v2/profile"


@dataclass(frozen=True)
class LineIdentity:
    line_user_id: str
    display_name: str | None = None
    picture_url: str | None = None
    email: str | None = None

    @classmethod
    def from_id_token_claims(cls, claims: dict[str, Any]) -> LineIdentity:
        return cls(
            line_user_id=claims["sub"],
            display_name=claims.get("name"),
            picture_url=claims.get("picture"),
            email=claims.get("email"),
        )


class LineIdentityVerifier:
    """透過 LINE 官方端點驗證 id_token / access_token，避免自行處理 JWK。"""

    def __init__(
        self, channel_id: str, session: requests.Session | None = None, timeout: float = 5.0
    ) -> None:
        self._channel_id = channel_id
        self._session = session or requests.Session()
        self._timeout = timeout

    def verify_id_token(self, id_token: str, nonce: str | None = None) -> LineIdentity:
        data = {"id_token": id_token, "client_id": self._channel_id}
        if nonce:
            data["nonce"] = nonce
        try:
            resp = self._session.post(LINE_VERIFY_ID_TOKEN_URL, data=data, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ExternalServiceError("無法連線 LINE 驗證服務") from exc
        if resp.status_code != 200:
            raise AuthenticationError("LINE id_token 驗證失敗", details=_safe_json(resp))
        claims = resp.json()
        if claims.get("aud") != self._channel_id:
            raise AuthenticationError("id_token audience 不符")
        return LineIdentity.from_id_token_claims(claims)

    def verify_access_token(self, access_token: str) -> None:
        try:
            resp = self._session.get(
                LINE_VERIFY_ACCESS_TOKEN_URL,
                params={"access_token": access_token},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError("無法連線 LINE 驗證服務") from exc
        if resp.status_code != 200 or resp.json().get("client_id") != self._channel_id:
            raise AuthenticationError("LINE access_token 無效")

    def fetch_profile(self, access_token: str) -> LineIdentity:
        try:
            resp = self._session.get(
                LINE_PROFILE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ExternalServiceError("無法取得 LINE 個人資料") from exc
        if resp.status_code != 200:
            raise AuthenticationError("LINE access_token 無效")
        body = resp.json()
        return LineIdentity(
            line_user_id=body["userId"],
            display_name=body.get("displayName"),
            picture_url=body.get("pictureUrl"),
        )


def _safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code}
