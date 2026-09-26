"""將例外統一轉為回應：API / webhook 回 JSON，瀏覽器請求 render 錯誤頁。"""

from __future__ import annotations

from flask import Flask, Response, current_app, render_template, request
from werkzeug.exceptions import HTTPException

from app.core.exceptions import AppError
from app.core.responses import fail

# 這些路徑前綴一律回 JSON，不受 Accept header 影響
JSON_PATH_PREFIXES = ("/api/", "/line/")

# HTML 錯誤頁的標題與說明；不在表內的狀態碼使用預設文案
ERROR_PAGE_TEXT: dict[int, tuple[str, str]] = {
    400: ("請求格式錯誤", "伺服器無法理解這個請求，請確認後再試一次。"),
    401: ("請先登入", "這個頁面需要登入後才能存取。"),
    403: ("沒有存取權限", "你的帳號沒有權限檢視這個頁面。"),
    404: ("找不到頁面", "你要找的頁面不存在，或網址已經變更。"),
    405: ("不支援的請求方式", "這個網址不接受目前的請求方式。"),
    409: ("資料衝突", "操作與目前的資料狀態衝突，請重新整理後再試。"),
    422: ("資料驗證失敗", "送出的資料不符合格式要求。"),
    429: ("請求過於頻繁", "請稍後再試。"),
    500: ("伺服器發生錯誤", "我們已經記錄這個問題，請稍後再試。"),
    502: ("外部服務異常", "連線到外部服務時發生錯誤，請稍後再試。"),
    503: ("服務暫時無法使用", "系統維護或負載過高，請稍後再試。"),
}
DEFAULT_ERROR_PAGE_TEXT = ("發生錯誤", "處理請求時發生問題，請稍後再試。")


def _wants_json() -> bool:
    """API / webhook 路徑，或 Accept 明確偏好 JSON 而非 HTML 時回 JSON。"""
    if request.path.startswith(JSON_PATH_PREFIXES) or request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] > accept["text/html"]


def _respond(code: str, message: str | None, status: int, details: dict | None = None) -> Response:
    if _wants_json():
        return fail(code, message or code, status, details)
    title, hint = ERROR_PAGE_TEXT.get(status, DEFAULT_ERROR_PAGE_TEXT)
    # AppError 的 message 已是給使用者看的中文；HTTPException 的 description 是英文，改用表內文案
    body = render_template("errors/error.html", status=status, title=title, message=message or hint)
    return Response(body, status=status, mimetype="text/html")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def handle_app_error(err: AppError):
        return _respond(err.error_code, err.message, err.status_code, err.details)

    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        status = err.code or 500
        code = err.name.lower().replace(" ", "_")
        message = err.description if _wants_json() else None
        return _respond(code, message, status)

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        # 只記型別與 traceback；不把 str(err) 帶進 log，SQLAlchemy 例外會夾帶含個資的 SQL 參數
        current_app.logger.error(
            "Unhandled %s at %s %s", type(err).__name__, request.method, request.path, exc_info=True
        )
        return _respond("internal_error", "伺服器發生未預期的錯誤", 500)
