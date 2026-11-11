"""應用層例外階層：所有可預期的錯誤都轉換為 AppError，由統一的錯誤處理器輸出 JSON。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有業務例外的基底類別。"""

    status_code: int = 400
    error_code: str = "app_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.error_code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"


class AuthenticationError(AppError):
    status_code = 401
    error_code = "unauthenticated"


class AuthorizationError(AppError):
    status_code = 403
    error_code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"


class ExternalServiceError(AppError):
    """呼叫 LINE 等外部服務失敗。"""

    status_code = 502
    error_code = "external_service_error"
