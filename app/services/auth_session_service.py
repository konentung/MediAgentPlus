"""登入工作階段：簽發、輪替、撤銷自家 refresh token，並跟著 LINE 的 token 生命週期走。

流程（對照 LINE Login v2.1）：
- 網頁版 LINE Login callback 拿到 LINE 的 access / refresh token → 加密存進 auth_sessions，
  發自家 token pair（refresh token 的 jti = session id）。
- POST /auth/refresh：驗自家 refresh token → 若 session 有 LINE refresh token，先向 LINE
  換新 access token（`grant_type=refresh_token`）；LINE 回 invalid_grant 表示使用者已解除授權，
  整個 family 撤銷並要求重新登入 → 通過後輪替：舊 session 標記已用、新 session 接手。
- 舊 refresh token 再被使用（重用偵測）→ 整個 family 撤銷。
- POST /auth/logout：撤銷 family，並向 LINE 撤銷 access token（LINE 會連同 refresh token 失效）。
- LIFF 登入沒有 LINE refresh token（token 由 LIFF SDK 在客戶端管理），只做自家輪替。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth.jwt_service import JwtService, TokenPair
from app.auth.line_login import LineLoginService, LineTokenResponse
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.domain.principal import Principal
from app.repositories.auth_session_repository import AuthSessionRepository
from app.utils.crypto import FieldCipher

logger = logging.getLogger(__name__)


class AuthSessionService:
    def __init__(
        self,
        sessions: AuthSessionRepository,
        jwt: JwtService,
        line_login: LineLoginService,
        cipher: FieldCipher,
        refresh_expires: timedelta,
    ) -> None:
        self._sessions = sessions
        self._jwt = jwt
        self._line_login = line_login
        self._cipher = cipher
        self._refresh_expires = refresh_expires

    def start(
        self,
        principal: Principal,
        *,
        provider: str,
        line_tokens: LineTokenResponse | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        """登入成功後建立新的 family 並簽發 token pair。"""
        session = self._create_session(
            principal,
            family_id=None,
            provider=provider,
            line_tokens=line_tokens,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return self._jwt.issue_pair(principal, refresh_jti=session["id"])

    def refresh(self, refresh_token: str, *, load_principal) -> TokenPair:
        """輪替 refresh token。`load_principal(user_id)` 由呼叫端提供，重新從 DB 載入使用者。"""
        claims = self._jwt.decode(refresh_token, expected_type="refresh")
        session = self._sessions.get(claims.get("jti") or "")
        if session is None or session["user_id"] != claims.get("sub"):
            raise AuthenticationError("憑證無效，請重新登入")
        if session["revoked_at"] is not None:
            raise AuthenticationError("登入已登出或被撤銷，請重新登入")
        if session["used_at"] is not None:
            # 舊 refresh token 再被使用：可能外洩，整個 family 一起撤銷
            self._sessions.revoke_family(session["family_id"])
            logger.warning("Refresh token reuse detected for user %s", session["user_id"])
            raise AuthenticationError("憑證已被重複使用，為安全起見已登出所有裝置，請重新登入")

        principal = load_principal(session["user_id"])
        line_tokens = self._refresh_line_tokens(session)
        new_session = self._create_session(
            principal,
            family_id=session["family_id"],
            provider=session["provider"],
            line_tokens=line_tokens,
            user_agent=session.get("user_agent"),
            ip_address=session.get("ip_address"),
        )
        self._sessions.mark_rotated(session["id"], replaced_by=new_session["id"])
        return self._jwt.issue_pair(principal, refresh_jti=new_session["id"])

    def logout(self, refresh_token: str | None) -> None:
        """撤銷這次登入的所有 token；有 LINE token 就一併向 LINE 撤銷。缺 token 時視為已登出。"""
        if not refresh_token:
            return
        try:
            claims = self._jwt.decode(refresh_token, expected_type="refresh")
        except AuthenticationError:
            return
        session = self._sessions.get(claims.get("jti") or "")
        if session is None:
            return
        self._sessions.revoke_family(session["family_id"])
        line_access = self._decrypt(session.get("line_access_token_encrypted"))
        if line_access:
            try:
                self._line_login.revoke_token(line_access)
            except ExternalServiceError:
                logger.warning("LINE token revoke failed for user %s", session["user_id"])

    def revoke_all(self, user_id: str) -> int:
        """例如帳號被鎖定時，讓所有裝置的 refresh token 立即失效。"""
        return self._sessions.revoke_all_for_user(user_id)

    def _refresh_line_tokens(self, session: dict[str, Any]) -> LineTokenResponse | None:
        line_refresh = self._decrypt(session.get("line_refresh_token_encrypted"))
        if not line_refresh:
            return None
        try:
            return self._line_login.refresh_tokens(line_refresh)
        except AuthenticationError:
            # LINE 判定 refresh token 無效（使用者解除授權、超過 90 天）：自家 session 也失效
            self._sessions.revoke_family(session["family_id"])
            raise AuthenticationError("LINE 授權已失效，請重新登入") from None

    def _create_session(
        self,
        principal: Principal,
        *,
        family_id: str | None,
        provider: str,
        line_tokens: LineTokenResponse | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        values: dict[str, Any] = {
            "id": session_id,
            "family_id": family_id or session_id,
            "user_id": principal.user_id,
            "provider": provider,
            "expires_at": datetime.now(UTC) + self._refresh_expires,
            "user_agent": (user_agent or "")[:255] or None,
            "ip_address": ip_address,
        }
        if line_tokens is not None:
            values["line_access_token_encrypted"] = self._cipher.encrypt(line_tokens.access_token)
            if line_tokens.refresh_token:
                values["line_refresh_token_encrypted"] = self._cipher.encrypt(
                    line_tokens.refresh_token
                )
            if line_tokens.expires_in:
                values["line_token_expires_at"] = datetime.now(UTC) + timedelta(
                    seconds=line_tokens.expires_in
                )
        return self._sessions.create(**values)

    def _decrypt(self, value: str | None) -> str | None:
        return self._cipher.decrypt(value) if value else None
