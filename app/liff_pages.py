"""LIFF 頁面（Jinja render，無獨立前端專案）。

LINE Developers Console 的 LIFF Endpoint URL 設為 `https://<host>/liff`，
`https://liff.line.me/<LIFF_ID>/intake` 就會開到 `/liff/intake`。
頁面骨架不需驗證；頁內 JS 以 liff.getIDToken() 換 JWT 後再打 /api/v1/liff/*。
"""

from __future__ import annotations

import json

from flask import Blueprint, render_template, request

from app.domain.intake_schema import CONSENT_VERSION, public_schema
from app.domain.reminder_rules import public_rules
from app.domain.triage_rules import district_options
from app.domain.triage_rules import public_rules as triage_rules

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
    """病人自填表（6 步）。不帶院所：院所在後台勾選病人、病人在 LINE 上確認後才綁定。"""
    return render_template(
        "liff/intake.html",
        schema_json=json.dumps(public_schema(), ensure_ascii=False),
        districts_json=json.dumps(district_options(), ensure_ascii=False),
        consent_version=CONSENT_VERSION,
    )


@liff_pages_bp.get("/feedback")
def feedback():
    """意見回饋表單：任何加過好友的人都能填，不需先填病人表單。"""
    return render_template("liff/feedback.html")


@liff_pages_bp.get("/prescriptions/<prescription_id>")
def prescription(prescription_id: str):
    """處方辨識結果：檢視照片、修改藥品清單、確認、建立提醒。"""
    return render_template(
        "liff/prescription.html",
        prescription_id=prescription_id,
        rules_json=json.dumps(public_rules(), ensure_ascii=False),
    )


@liff_pages_bp.get("/reminders")
def reminders():
    """服藥提醒總覽與編輯。`?prescription=<id>` 會先載入該處方的建議排程。"""
    return render_template(
        "liff/reminders.html",
        prescription_id=request.args.get("prescription", ""),
        rules_json=json.dumps(public_rules(), ensure_ascii=False),
    )


@liff_pages_bp.get("/er")
def er_board():
    """急診即時看板（公開資料，不需登入；LINE 內外都能開）。

    `?area=01` 預選縣市；`?lat=&lng=` 以該位置依距離排序（LINE 找醫院的「完整看板」連結會帶）。
    """
    return render_template(
        "liff/er.html",
        area_code=request.args.get("area", ""),
        lat=request.args.get("lat", ""),
        lng=request.args.get("lng", ""),
        rules_json=json.dumps(triage_rules(), ensure_ascii=False),
    )
