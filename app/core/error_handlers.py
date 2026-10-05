"""將例外統一轉為回應：API / webhook 回 JSON，瀏覽器請求 render 錯誤頁。"""

from __future__ import annotations

from flask import Flask, Response, current_app, render_template, request
from werkzeug.exceptions import ClientDisconnected, HTTPException

from app.core.exceptions import AppError
from app.core.responses import fail

# 這些路徑前綴一律回 JSON，不受 Accept header 影響
JSON_PATH_PREFIXES = ("/api/", "/line/")
# 修改：例外是 LINE Login 的 login / callback —— 雖然掛在 /api/ 下，
# 卻是瀏覽器直接導過來的頁面（成功時 render 登入成功頁），失敗時要顯示錯誤頁而不是一段 JSON；
# 仍尊重 Accept 明確要 JSON 的呼叫端
BROWSER_PATH_PREFIXES = ("/api/v1/auth/line/",)

# HTML 錯誤頁的標題與說明；不在表內的狀態碼使用預設文案
ERROR_PAGE_TEXT: dict[int, tuple[str, str]] = {
    400: ("請求格式錯誤", "伺服器無法理解這個請求，請確認後再試一次。"),
    401: ("請先登入", "這個頁面需要登入後才能存取。"),
    403: ("沒有存取權限", "你的帳號沒有權限檢視這個頁面。"),
    404: ("找不到頁面", "你要找的頁面不存在，或網址已經變更。"),
    405: ("不支援的請求方式", "這個網址不接受目前的請求方式。"),
    409: ("資料衝突", "操作與目前的資料狀態衝突，請重新整理後再試。"),
    413: ("上傳的檔案太大", "上傳的內容超過大小上限，請分批上傳。"),
    422: ("資料驗證失敗", "送出的資料不符合格式要求。"),
    429: ("請求過於頻繁", "請稍後再試。"),
    500: ("伺服器發生錯誤", "我們已經記錄這個問題，請稍後再試。"),
    502: ("外部服務異常", "連線到外部服務時發生錯誤，請稍後再試。"),
    503: ("服務暫時無法使用", "系統維護或負載過高，請稍後再試。"),
}
DEFAULT_ERROR_PAGE_TEXT = ("發生錯誤", "處理請求時發生問題，請稍後再試。")


def _wants_json() -> bool:
    """API / webhook 路徑，或 Accept 明確偏好 JSON 而非 HTML 時回 JSON。"""
    if request.is_json:
        return True
    is_browser_page = request.path.startswith(BROWSER_PATH_PREFIXES)
    if request.path.startswith(JSON_PATH_PREFIXES) and not is_browser_page:
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
        if err.status_code >= 500:
            # 新增：5xx（多半是連不上外部服務）留一行 log，
            # 否則事後只看得到存取紀錄的 502、查不出原因。
            # 只記底層例外的型別（ConnectTimeout / SSLError…），不記內容：
            # requests 的錯誤訊息會帶完整網址，可能含 access_token 之類的查詢參數
            current_app.logger.warning(
                "%s (%s) at %s %s: %s; cause=%s",
                err.error_code,
                err.status_code,
                request.method,
                request.path,
                err.message,
                type(err.__cause__).__name__ if err.__cause__ else "-",
            )
        return _respond(err.error_code, err.message, err.status_code, err.details)

    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        status = err.code or 500
        code = err.name.lower().replace(" ", "_")
        if isinstance(err, ClientDisconnected):
            # 新增：上傳到一半連線就斷了（瀏覽器取消 / 重新整理、經 ngrok 等代理被切斷、
            # 讀不到要上傳的檔案）。存取紀錄只會留下一行 400，看不出原因，這裡補一行說明
            current_app.logger.warning(
                "Upload interrupted at %s %s: connection closed before the full body arrived "
                "(Content-Length %s)",
                request.method,
                request.path,
                request.content_length,
            )
        message = err.description if _wants_json() else None
        if status == 413:
            # 新增：werkzeug 的英文說明看不出上限是多少；直接講清楚上限與做法（JSON 與錯誤頁都用）
            # 個別端點可能放寬過上限（衛教 PDF 匯入），以這個請求實際套用的為準
            limit_mb = (request.max_content_length or 0) // (1024 * 1024)
            message = f"上傳的檔案太大：一次上傳的總大小不可超過 {limit_mb} MB，請分批上傳"
        return _respond(code, message, status)

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        # 只記型別與 traceback；不把 str(err) 帶進 log，SQLAlchemy 例外會夾帶含個資的 SQL 參數
        current_app.logger.error(
            "Unhandled %s at %s %s", type(err).__name__, request.method, request.path, exc_info=True
        )
        return _respond("internal_error", "伺服器發生未預期的錯誤", 500)
