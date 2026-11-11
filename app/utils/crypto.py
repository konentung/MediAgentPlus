"""欄位級加密：身分證字號等高敏感欄位以 Fernet 加密儲存，另存雜湊供搜尋 / 重複檢查。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


class FieldCipher:
    """對單一字串欄位做加密 / 解密 / 雜湊。

    - `encrypt` / `decrypt`：Fernet（AES-128-CBC + HMAC），輸出 urlsafe base64 字串。
    - `fingerprint`：HMAC-SHA256，供資料庫比對「同一個身分證是否已被綁定」而不需解密。
    """

    def __init__(self, key: str | None, *, fallback_secret: str) -> None:
        if key:
            fernet_key = key.encode()
        else:
            # 開發環境未設定 PATIENT_DATA_KEY：由 SECRET_KEY 衍生固定金鑰，重啟後仍可解密
            fernet_key = base64.urlsafe_b64encode(hashlib.sha256(fallback_secret.encode()).digest())
        self._fernet = Fernet(fernet_key)
        self._hmac_key = hashlib.sha256(b"fingerprint:" + fernet_key).digest()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            logger.error("Field decryption failed; PATIENT_DATA_KEY may have changed")
            raise AppError("資料解密失敗", status_code=500, error_code="decrypt_failed") from exc

    def encrypt_bytes(self, data: bytes) -> bytes:
        """二進位內容（處方箋照片）落地前加密。"""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            logger.error("Blob decryption failed; PATIENT_DATA_KEY may have changed")
            raise AppError("資料解密失敗", status_code=500, error_code="decrypt_failed") from exc

    def fingerprint(self, plaintext: str) -> str:
        return hmac.new(self._hmac_key, plaintext.upper().encode(), hashlib.sha256).hexdigest()
