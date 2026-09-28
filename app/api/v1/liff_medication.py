"""LIFF 端：處方箋（拍照辨識結果的檢視 / 修改 / 確認）、找藥局、藥品查詢、服藥提醒。

頁面在 /liff/prescriptions/<id> 與 /liff/reminders（app/liff_pages.py），頁內 JS 打這裡的端點。
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from app.auth.decorators import current_principal, require_permission
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission
from app.domain.reminder_rules import public_rules
from app.utils.geo import parse_coords

liff_medication_bp = Blueprint("liff_medication", __name__, url_prefix="/liff")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


# ---- 處方箋 ----


@liff_medication_bp.get("/prescriptions")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_list():
    return ok(get_container().prescriptions.list_own(current_principal()))


@liff_medication_bp.get("/prescriptions/<prescription_id>")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_get(prescription_id: str):
    return ok(get_container().prescriptions.get_public(current_principal(), prescription_id))


@liff_medication_bp.get("/prescriptions/<prescription_id>/image")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_image(prescription_id: str):
    data, content_type = get_container().prescriptions.read_image(
        current_principal(), prescription_id
    )
    return Response(data, mimetype=content_type, headers={"Cache-Control": "private, no-store"})


@liff_medication_bp.patch("/prescriptions/<prescription_id>")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_update(prescription_id: str):
    return ok(
        get_container().prescriptions.update(current_principal(), prescription_id, _json_body())
    )


@liff_medication_bp.post("/prescriptions/<prescription_id>/confirm")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_confirm(prescription_id: str):
    return ok(get_container().prescriptions.confirm(current_principal(), prescription_id))


@liff_medication_bp.delete("/prescriptions/<prescription_id>")
@require_permission(Permission.PRESCRIPTION_MANAGE_OWN)
def prescription_discard(prescription_id: str):
    get_container().prescriptions.discard(current_principal(), prescription_id)
    return ok({"discarded": True})


@liff_medication_bp.post("/prescriptions/<prescription_id>/schedules/propose")
@require_permission(Permission.REMINDER_MANAGE_OWN)
def prescription_propose_schedules(prescription_id: str):
    """依處方各項藥的頻率算出預設提醒（不寫入），LIFF 頁面讓病人調整後再 POST /reminders。"""
    return ok(
        {
            "drafts": get_container().reminders.propose_from_prescription(
                current_principal(), prescription_id
            ),
            "rules": public_rules(),
        }
    )


# ---- 藥局 / 藥品 ----


@liff_medication_bp.get("/pharmacies")
@require_permission(Permission.CONSULT_MEDICATION_ONCE)
@rate_limit("pharmacy_search", limit=60, per_seconds=600, by="user")
def pharmacy_search():
    """`?address=台北市松山區…` 或 `?lat=&lng=`：依縣市 / 鄉鎮市區列出健保特約藥局。"""
    coords = parse_coords(request.args.get("lat"), request.args.get("lng"))
    return ok(
        get_container().pharmacies.find_nearby(
            address=request.args.get("address") or None,
            lat=coords[0] if coords else None,
            lng=coords[1] if coords else None,
        )
    )


@liff_medication_bp.get("/drugs")
@require_permission(Permission.CONSULT_MEDICATION_ONCE)
@rate_limit("drug_search", limit=120, per_seconds=600, by="user")
def drug_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        raise ValidationError("請輸入至少 2 個字")
    return ok(get_container().pharmacies.search_drugs(query))


# ---- 服藥提醒 ----


@liff_medication_bp.get("/reminders")
@require_permission(Permission.REMINDER_MANAGE_OWN)
def reminder_list():
    data = get_container().reminders.list_own(current_principal())
    return ok({**data, "rules": public_rules()})


@liff_medication_bp.post("/reminders")
@require_permission(Permission.REMINDER_MANAGE_OWN)
@rate_limit("reminder_create", limit=60, per_seconds=600, by="user")
def reminder_create():
    body = request.get_json(silent=True)
    drafts = body.get("schedules") if isinstance(body, dict) else body
    return ok(get_container().reminders.create_many(current_principal(), drafts), status=201)


@liff_medication_bp.patch("/reminders/<schedule_id>")
@require_permission(Permission.REMINDER_MANAGE_OWN)
def reminder_update(schedule_id: str):
    return ok(get_container().reminders.update(current_principal(), schedule_id, _json_body()))


@liff_medication_bp.delete("/reminders/<schedule_id>")
@require_permission(Permission.REMINDER_MANAGE_OWN)
def reminder_delete(schedule_id: str):
    get_container().reminders.delete(current_principal(), schedule_id)
    return ok({"deleted": True})


@liff_medication_bp.get("/reminders/history")
@require_permission(Permission.REMINDER_MANAGE_OWN)
def reminder_history():
    days = request.args.get("days", default=7, type=int)
    return ok(get_container().reminders.history(current_principal(), days))
