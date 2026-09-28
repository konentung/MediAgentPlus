"""自家 API 的 JWT 簽發 / 驗證（HS256）。

只有 access token，沒有 refresh token：時間到就重新登入（LIFF 由 `liff.init()` 自動換新、
後台導回登入頁按一下 LINE 登入）。院方角色的效期較長（`JWT_STAFF_ACCESS_EXPIRES_SECONDS`），
讓一個工作天不必重登；角色 / 綁定狀態每次請求都重新從資料庫讀，不用 token 裡的舊值。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.exceptions import AuthenticationError
from app.domain.principal import Principal


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int  # 秒
    expires_at: str  # ISO 8601（UTC），前端可據此提前提醒重登
    token_type: str = "Bearer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "expires_at": self.expires_at,
        }


class JwtService:
    def __init__(
        self,
        secret: str,
        algorithm: str,
        access_expires: timedelta,
        staff_access_expires: timedelta | None = None,
    ) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._access_expires = access_expires
        self._staff_access_expires = staff_access_expires or access_expires

    def issue(self, principal: Principal) -> IssuedToken:
        expires = self._staff_access_expires if principal.role.is_staff else self._access_expires
        now = datetime.now(UTC)
        exp = now + expires
        # 只放識別資訊；角色 / 綁定狀態在每次請求時重新從資料庫載入，避免舊憑證帶著舊權限
        claims = {
            "sub": principal.user_id,
            "lid": principal.line_user_id,
            "role": principal.role.value,
            "typ": "access",
            "iat": now,
            "exp": exp,
        }
        return IssuedToken(
            access_token=jwt.encode(claims, self._secret, algorithm=self._algorithm),
            expires_in=int(expires.total_seconds()),
            expires_at=exp.isoformat(),
        )

    def decode(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("登入已過期，請重新登入") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("憑證無效") from exc
        if claims.get("typ") != "access":
            raise AuthenticationError("憑證類型錯誤")
        return claims
