"""院方後台 API（真人客服 / 醫護 / 院所管理員 / 系統管理員）。

對應的網頁後台在 /admin/*（app/admin_pages.py），頁面內以 fetch() 呼叫這裡的端點。
院所隔離由 PatientRecordService / HandoffService / UserService 內的 AccessPolicy 判斷。
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict

from flask import Blueprint, Response, request

from app.auth.decorators import current_principal, require_permission, require_role
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import STAFF_ROLES, Permission, Role
from app.domain.rbac import AccessPolicy

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


@admin_bp.get("/permissions")
@require_role(*[r for r in Role if r.is_staff])
def permission_matrix():
    return ok(AccessPolicy.matrix())


@admin_bp.get("/dashboard")
@require_permission(Permission.PATIENT_READ)
def dashboard():
    actor = current_principal()
    container = get_container()
    stats = container.patient_records.dashboard(actor)
    stats["open_handoffs"] = len(container.handoff.list_open(actor))
    return ok(stats)


@admin_bp.get("/hospitals")
@require_role(*[r for r in Role if r.is_staff])
def list_hospitals():
    include_inactive = (
        request.args.get("all") == "1" and current_principal().role is Role.SUPER_ADMIN
    )
    return ok(get_container().hospitals.list(active_only=not include_inactive))


@admin_bp.post("/hospitals")
@require_permission(Permission.HOSPITAL_MANAGE)
def create_hospital():
    return ok(get_container().hospitals.create(current_principal(), _json_body()), status=201)


@admin_bp.get("/hospitals/import/template")
@require_permission(Permission.HOSPITAL_MANAGE)
def hospital_import_template():
    from app.services.hospital_service import IMPORT_TEMPLATE_HEADER

    body = (
        IMPORT_TEMPLATE_HEADER + "\nH1,第一醫院,台北市,台北市中正區xx路1號,02-12345678,regional\n"
    )
    return Response(
        "\ufeff" + body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=hospitals_template.csv"},
    )


@admin_bp.post("/hospitals/import")
@require_permission(Permission.HOSPITAL_MANAGE)
@rate_limit("hospital_import", limit=10, per_seconds=600, by="user")
def import_hospitals():
    """上傳 CSV（multipart 欄位 file）或 JSON {"rows": [...]}。

    支援自家範本與健保署「特約醫事機構」開放資料格式。
    """
    only_hospitals = request.args.get("only_hospitals") == "1"
    upload = request.files.get("file")
    if upload is not None:
        raw = upload.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("big5", errors="replace")  # 政府開放資料偶有 Big5
        rows = list(csv.DictReader(io.StringIO(text)))
    else:
        body = _json_body()
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise ValidationError("請上傳 CSV 檔（欄位 file）或提供 rows 陣列")
    if not rows:
        raise ValidationError("檔案沒有資料列")
    result = get_container().hospitals.import_rows(
        current_principal(), rows, only_hospitals=only_hospitals
    )
    return ok(result)


@admin_bp.patch("/hospitals/<hospital_id>")
@require_permission(Permission.HOSPITAL_SETTINGS, Permission.HOSPITAL_MANAGE, any_of=True)
def update_hospital(hospital_id: str):
    return ok(get_container().hospitals.update(current_principal(), hospital_id, _json_body()))


@admin_bp.get("/users")
@require_permission(Permission.STAFF_MANAGE)
def list_users():
    actor = current_principal()
    hospital_scope = AccessPolicy.hospital_scope(actor)
    staff_only = request.args.get("staff", "1") != "0"
    users = get_container().user_repo.search(
        roles=STAFF_ROLES if staff_only else None,
        hospital_id=hospital_scope,
        # 院所管理員要能找到剛登入、還沒指派院所的新同仁
        include_unassigned=not staff_only and hospital_scope is not None,
        query=request.args.get("q") or None,
    )
    return ok([get_container().users.to_principal(u).to_dict() for u in users])


@admin_bp.put("/users/<user_id>/role")
@require_permission(Permission.STAFF_MANAGE)
def set_role(user_id: str):
    body = _json_body()
    try:
        role = Role(body.get("role"))
    except ValueError as exc:
        raise ValidationError("role 不合法", details={"allowed": [r.value for r in Role]}) from exc
    user = get_container().users.assign_role(
        current_principal(),
        user_id,
        role,
        body.get("hospital_id") or None,
        clear_hospital="hospital_id" in body and not body.get("hospital_id"),
    )
    return ok(get_container().users.to_principal(user).to_dict())


@admin_bp.get("/handoffs")
@require_permission(Permission.HANDOFF_ACCEPT)
def list_handoffs():
    tickets = get_container().handoff.list_open(current_principal())
    return ok([asdict(t) for t in tickets])


@admin_bp.post("/handoffs/<ticket_id>/accept")
@require_permission(Permission.HANDOFF_ACCEPT)
def accept_handoff(ticket_id: str):
    ticket = get_container().handoff.accept(current_principal(), ticket_id)
    return ok(asdict(ticket))


@admin_bp.get("/patients")
@require_permission(Permission.PATIENT_READ)
def list_patients():
    patients = get_container().patient_records.list_patients(
        current_principal(),
        status=request.args.get("status", "pending"),
        query=request.args.get("q"),
    )
    return ok(patients)


@admin_bp.get("/patients/<user_id>")
@require_permission(Permission.PATIENT_READ)
def get_patient(user_id: str):
    return ok(get_container().patient_records.get_patient(current_principal(), user_id))


@admin_bp.get("/patients/<user_id>/national-id")
@require_permission(Permission.PATIENT_IDENTITY_READ)
@rate_limit("national_id_reveal", limit=30, per_seconds=600, by="user")
def reveal_national_id(user_id: str):
    value = get_container().patient_records.reveal_national_id(
        current_principal(), user_id, ip_address=request.remote_addr
    )
    return ok({"national_id": value})


@admin_bp.post("/patients/<user_id>/lock")
@require_permission(Permission.PATIENT_VERIFY)
def lock_patient(user_id: str):
    body = _json_body()
    svc = get_container().patient_records
    return ok(svc.set_locked(current_principal(), user_id, locked=True, reason=body.get("reason")))


@admin_bp.post("/patients/<user_id>/unlock")
@require_permission(Permission.PATIENT_VERIFY)
def unlock_patient(user_id: str):
    body = _json_body()
    svc = get_container().patient_records
    return ok(svc.set_locked(current_principal(), user_id, locked=False, reason=body.get("reason")))


@admin_bp.get("/audit")
@require_permission(Permission.AUDIT_READ)
def list_audit():
    return ok(get_container().patient_records.list_audit(current_principal()))


@admin_bp.post("/patients/<user_id>/verify")
@require_permission(Permission.PATIENT_VERIFY)
def verify_patient(user_id: str):
    body = _json_body()
    record = get_container().patient_records.verify(
        current_principal(),
        user_id,
        hospital_id=body.get("hospital_id"),
        medical_record_no=body.get("medical_record_no"),
        identity_method=body.get("identity_method", "in_person"),
        note=body.get("note"),
    )
    return ok(record)


@admin_bp.post("/patients/<user_id>/reject")
@require_permission(Permission.PATIENT_VERIFY)
def reject_patient(user_id: str):
    body = _json_body()
    result = get_container().patient_records.reject(
        current_principal(), user_id, reason=body.get("reason", "")
    )
    return ok(result)


@admin_bp.patch("/patients/<user_id>/profile")
@require_permission(Permission.PATIENT_WRITE)
def correct_patient_profile(user_id: str):
    body = _json_body()
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("changes 必須是非空物件")
    profile = get_container().patient_records.correct_profile(
        current_principal(), user_id, changes, note=body.get("note")
    )
    return ok({"profile": profile})


@admin_bp.post("/patients/<user_id>/encounters")
@require_permission(Permission.ENCOUNTER_MANAGE)
def add_encounter(user_id: str):
    svc = get_container().patient_records
    return ok(svc.add_encounter(current_principal(), user_id, _json_body()), status=201)


@admin_bp.patch("/patients/<user_id>/encounters/<encounter_id>")
@require_permission(Permission.ENCOUNTER_MANAGE)
def update_encounter(user_id: str, encounter_id: str):
    svc = get_container().patient_records
    return ok(svc.update_encounter(current_principal(), user_id, encounter_id, _json_body()))


@admin_bp.post("/patients/<user_id>/flags")
@require_permission(Permission.PATIENT_WRITE)
def add_flag(user_id: str):
    svc = get_container().patient_records
    return ok(svc.add_flag(current_principal(), user_id, _json_body()), status=201)


@admin_bp.patch("/patients/<user_id>/flags/<flag_id>")
@require_permission(Permission.PATIENT_WRITE)
def update_flag(user_id: str, flag_id: str):
    svc = get_container().patient_records
    return ok(svc.update_flag(current_principal(), user_id, flag_id, _json_body()))


@admin_bp.post("/patients/<user_id>/follow-ups")
@require_permission(Permission.PATIENT_WRITE)
def add_follow_up(user_id: str):
    svc = get_container().patient_records
    return ok(svc.add_follow_up(current_principal(), user_id, _json_body()), status=201)


@admin_bp.patch("/patients/<user_id>/follow-ups/<follow_up_id>")
@require_permission(Permission.PATIENT_WRITE)
def update_follow_up(user_id: str, follow_up_id: str):
    svc = get_container().patient_records
    return ok(svc.update_follow_up(current_principal(), user_id, follow_up_id, _json_body()))


@admin_bp.post("/patients/<user_id>/notes")
@require_permission(Permission.PATIENT_READ)
def add_note(user_id: str):
    svc = get_container().patient_records
    return ok(svc.add_note(current_principal(), user_id, _json_body()), status=201)
