"""將例外統一轉為 JSON 回應。"""

from __future__ import annotations

from flask import Flask, current_app
from werkzeug.exceptions import HTTPException

from app.core.exceptions import AppError
from app.core.responses import fail


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def handle_app_error(err: AppError):
        return fail(err.error_code, err.message, err.status_code, err.details)

    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        return fail(
            err.name.lower().replace(" ", "_"), err.description or err.name, err.code or 500
        )

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        current_app.logger.exception("Unhandled exception: %s", err)
        return fail("internal_error", "伺服器發生未預期的錯誤", 500)
