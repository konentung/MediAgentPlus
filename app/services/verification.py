"""B4 驗證閘道：出院單一次性代碼 / 手機簡訊 OTP。

正式環境請另外實作 `VerificationGateway`（接簡訊供應商、HIS 出院單代碼查詢）。
"""

from __future__ import annotations

import logging
import secrets
from typing import Protocol

from app.domain.enums import VerificationMethod

logger = logging.getLogger(__name__)


class VerificationGateway(Protocol):
    def issue(
        self, method: VerificationMethod, *, phone_last4: str | None, discharge_code: str | None
    ) -> str:
        """回傳預期的驗證碼（SMS 情境下同時負責發送簡訊）。"""
        ...


class StubVerificationGateway:
    """開發用：OTP 只寫進 log，不真正發送簡訊。"""

    def issue(
        self, method: VerificationMethod, *, phone_last4: str | None, discharge_code: str | None
    ) -> str:
        if method is VerificationMethod.DISCHARGE_CODE:
            # 出院單代碼由院方識別名單提供，這裡直接回傳給比對階段使用
            return discharge_code or ""
        code = f"{secrets.randbelow(10**6):06d}"
        logger.info("[DEV] SMS OTP for ***%s: %s", phone_last4, code)
        return code
