"""LINE Webhook：驗簽 → 解析事件 → 立即回 200，事件交給背景執行緒跑 EventDispatcher。
AI / OCR 可達 10–60 秒，同步處理會超時觸發 LINE redelivery 造成重複訊息；
`WEBHOOK_ASYNC=False`（測試）改同步。redelivery 由 dispatcher 去重。
"""

from __future__ import annotations

import threading

from flask import Blueprint, abort, current_app, request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhook import WebhookParser

from app.line.dispatcher import EventDispatcher

line_bp = Blueprint("line", __name__, url_prefix="/line")


def _dispatcher() -> EventDispatcher:
    return current_app.extensions["line_dispatcher"]


@line_bp.post("/webhook")
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    parser = WebhookParser(current_app.config["LINE_CHANNEL_SECRET"])
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        abort(400, description="Invalid LINE signature")
    if not current_app.config.get("WEBHOOK_ASYNC", True):
        _dispatcher().dispatch(events)
        return "OK", 200

    app = current_app._get_current_object()  # 背景執行緒需要真正的 app 物件來開 app context

    def run() -> None:
        with app.app_context():
            app.extensions["line_dispatcher"].dispatch(events)

    threading.Thread(target=run, name="line-webhook", daemon=True).start()
    return "OK", 200
