"""LIFF 登入：前端以 liff.getIDToken() 取得 id_token，後端驗證後換發自家 JWT。"""

from __future__ import annotations

from app.auth.line_identity import LineIdentity, LineIdentityVerifier
from app.core.exceptions import ValidationError


class LiffAuthService:
    def __init__(self, verifier: LineIdentityVerifier, liff_id: str) -> None:
        self._verifier = verifier
        self._liff_id = liff_id

    @property
    def liff_id(self) -> str:
        return self._liff_id

    def authenticate(
        self, id_token: str | None = None, access_token: str | None = None
    ) -> LineIdentity:
        """優先使用 id_token（含 profile claims）；退而求其次用 access_token 反查 profile。"""
        if id_token:
            return self._verifier.verify_id_token(id_token)
        if access_token:
            self._verifier.verify_access_token(access_token)
            return self._verifier.fetch_profile(access_token)
        raise ValidationError("需提供 id_token 或 access_token")
