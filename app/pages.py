"""一般網頁（非 API）：首頁。"""

from __future__ import annotations

from flask import Blueprint, current_app, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def home():
    # 首頁只介紹功能與加 LINE 好友，不提供登入；院方人員由 /admin/login 進後台
    basic_id = current_app.config.get("LINE_BASIC_ID") or ""
    return render_template(
        "home.html",
        add_friend_url=f"https://line.me/R/ti/p/@{basic_id}" if basic_id else None,
    )
