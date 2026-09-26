"""LIFF 頁面（Jinja render，無獨立前端專案）。

LINE Developers Console 的 LIFF Endpoint URL 設為 `https://<host>/liff`，
`https://liff.line.me/<LIFF_ID>/intake` 就會開到 `/liff/intake`。
頁面骨架不需驗證；頁內 JS 以 liff.getIDToken() 換 JWT 後再打 /api/v1/liff/*。
"""

from __future__ import annotations

import json

from flask import Blueprint, render_template, request

from app.domain.intake_schema import CONSENT_VERSION, public_schema

liff_pages_bp = Blueprint("liff_pages", __name__, url_prefix="/liff")


# LIFF Endpoint URL 設為 https://<host>/liff，LINE 會先開 /liff?liff.state=<path>。
# 這個第一個回應絕對不能是 3xx：LIFF 瀏覽器遇到轉址會改用外部瀏覽器開啟，整個流程就跳出 LINE。
# 所以 /liff 與 /liff/ 都直接回 200（strict_slashes=False，不做補尾斜線轉址）。
@liff_pages_bp.get("/", strict_slashes=False)
@liff_pages_bp.get("/settings")
def settings():
    """設定頁：顯示綁定狀態與已填資料摘要，導向表單。"""
    return render_template("liff/settings.html")


@liff_pages_bp.get("/intake")
def intake():
    """病人自填表（6 步）。`?hospital=<院所代碼>` 由院所提供的 QR / 連結帶入。"""
    return render_template(
        "liff/intake.html",
        schema_json=json.dumps(public_schema(), ensure_ascii=False),
        hospital_id=request.args.get("hospital", ""),
        consent_version=CONSENT_VERSION,
    )
