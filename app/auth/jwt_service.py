"""自家 API 的 JWT 簽發 / 驗證（HS256）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.exceptions import AuthenticationError
from app.domain.principal import Principal


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
        }


class JwtService:
    def __init__(
        self, secret: str, algorithm: str, access_expires: timedelta, refresh_expires: timedelta
    ) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._access_expires = access_expires
        self._refresh_expires = refresh_expires

    def issue_pair(self, principal: Principal, *, refresh_jti: str | None = None) -> TokenPair:
        """`refresh_jti` 對應 auth_sessions.id，讓 refresh token 可以被輪替與撤銷。"""
        return TokenPair(
            access_token=self._issue(principal, "access", self._access_expires),
            refresh_token=self._issue(principal, "refresh", self._refresh_expires, jti=refresh_jti),
            expires_in=int(self._access_expires.total_seconds()),
        )

    def decode(self, token: str, expected_type: str = "access") -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("憑證已過期") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("憑證無效") from exc
        if claims.get("typ") != expected_type:
            raise AuthenticationError("憑證類型錯誤")
        return claims

    def _issue(
        self, principal: Principal, token_type: str, expires: timedelta, *, jti: str | None = None
    ) -> str:
        now = datetime.now(UTC)
        # 只放識別資訊；角色 / 綁定狀態在每次請求時重新從資料庫載入，避免舊憑證帶著舊權限
        claims = {
            "sub": principal.user_id,
            "lid": principal.line_user_id,
            "role": principal.role.value,
            "typ": token_type,
            "iat": now,
            "exp": now + expires,
        }
        if jti:
            claims["jti"] = jti
        return jwt.encode(claims, self._secret, algorithm=self._algorithm)
