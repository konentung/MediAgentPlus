"""LINE Webhook 端點：驗證簽章 → 解析事件 → 交給 EventDispatcher。"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhook import WebhookParser

from app.core.container import get_container
from app.line.dispatcher import EventDispatcher

line_bp = Blueprint("line", __name__, url_prefix="/line")


def _dispatcher() -> EventDispatcher:
    ext = current_app.extensions
    if "line_dispatcher" not in ext:
        ext["line_dispatcher"] = EventDispatcher.default(
            get_container(), current_app.config["LIFF_ID"]
        )
    return ext["line_dispatcher"]


@line_bp.post("/webhook")
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    parser = WebhookParser(current_app.config["LINE_CHANNEL_SECRET"])
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        abort(400, description="Invalid LINE signature")
    _dispatcher().dispatch(events)
    return "OK", 200
