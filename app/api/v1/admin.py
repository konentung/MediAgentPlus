"""院方後台 API（診所人員 / 醫院行政與醫護 / 藥局人員 / 單位管理員 / 系統管理員）。

對應的網頁後台在 /admin/*（app/admin_pages.py），頁面內以 fetch() 呼叫這裡的端點。
院所隔離由 PatientRecordService / FeedbackService / UserService 內的 AccessPolicy 判斷。
"""

from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, request

from app.auth.decorators import current_principal, require_permission, require_role
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import STAFF_ROLES, SYSTEM_ROLES, Permission, Role
from app.domain.rbac import AccessPolicy
from app.domain.triage_rules import district_options

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _page_arg() -> int:
    try:
        return max(int(request.args.get("page") or 1), 1)
    except ValueError as exc:
        raise ValidationError("page 需為整數") from exc


def _optional_json_body() -> dict:
    """body 可為空；有給就必須是 JSON 物件。"""  # 新增
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


@admin_bp.get("/permissions")
@require_permission(Permission.PERMISSION_MATRIX_READ)
def permission_matrix():
    return ok(AccessPolicy.matrix())


@admin_bp.get("/dashboard")
@require_permission(Permission.PATIENT_READ, Permission.PICKUP_MANAGE, any_of=True)
def dashboard():
    """儀表板數字，只回這個人有權限看的項目：院所端是病人 / 回報，藥局端是待備藥的領藥需求。"""
    actor = current_principal()
    container = get_container()
    stats: dict = {}
    if AccessPolicy.has_permission(actor, Permission.PATIENT_READ):
        stats.update(container.patient_records.dashboard(actor))
    if AccessPolicy.has_permission(actor, Permission.FEEDBACK_MANAGE):
        stats["open_feedback"] = container.feedback.count_open(actor)
    # 藥局帳號是自己藥局待備藥的筆數，SUPER_ADMIN 是全部藥局的
    if AccessPolicy.has_permission(actor, Permission.PICKUP_MANAGE):
        stats["requested_pickups"] = container.pickups.count_requested(actor)
    return ok(stats)


@admin_bp.get("/hospitals/options")
@require_role(*[r for r in Role if r.is_staff])
def hospital_options():
    """後台每頁載入一次的院所下拉選單：只有醫院層級（診所有上萬家，不放進選單）。"""
    # 登入者自己的院所（即使是診所層級）也要在清單裡，側欄與院所設定才顯示得出來
    return ok(get_container().hospitals.options(ensure_id=current_principal().hospital_id))


@admin_bp.get("/districts")
@require_role(*STAFF_ROLES)
def list_districts():
    """後台篩選用的縣市 → 鄉鎮市區下拉選單（離線資料 tw_districts.csv），縣市依北中南東排序。"""
    return ok(district_options())


@admin_bp.get("/hospitals")
@require_role(*[r for r in Role if r.is_staff])
def list_hospitals():
    """後台「所有院所」表格：分頁（每頁 15 筆）、q / region / district / tier 篩選。

    all=1 含停用（只有 SUPER_ADMIN）。
    """
    args = request.args
    include_inactive = args.get("all") == "1" and current_principal().role is Role.SUPER_ADMIN
    return ok(
        get_container().hospitals.paginate(
            query=args.get("q"),
            region=args.get("region"),
            district=args.get("district"),
            tier=args.get("tier"),
            include_inactive=include_inactive,
            page=_page_arg(),
        )
    )


@admin_bp.post("/hospitals")
@require_permission(Permission.HOSPITAL_MANAGE)
def create_hospital():
    return ok(get_container().hospitals.create(current_principal(), _json_body()), status=201)


@admin_bp.get("/hospitals/import/template")
@require_permission(Permission.HOSPITAL_MANAGE)
def hospital_import_template():
    from app.services.hospital_service import IMPORT_TEMPLATE_HEADER

    body = (
        IMPORT_TEMPLATE_HEADER
        + "\nH1,第一醫院,台北市,台北市中正區xx路1號,02-12345678,regional,25.0410,121.5185\n"
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
    result = get_container().hospitals.import_rows(
        current_principal(), _csv_rows_from_request(), only_hospitals=only_hospitals
    )
    return ok(result)


def _csv_rows_from_request() -> list[dict]:
    """匯入共用：multipart 欄位 file 的 CSV，或 JSON {"rows": [...]}。"""
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
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValidationError("請上傳 CSV 檔（欄位 file）或提供 rows 物件陣列")
    if not rows:
        raise ValidationError("檔案沒有資料列")
    return rows


# ---- 參考資料：藥局 / 藥品開放資料（SUPER_ADMIN）----


@admin_bp.get("/pharmacies")
@require_permission(Permission.MEDICATION_MANAGE)
def list_pharmacies():
    """後台「藥局」頁：表格分頁（每頁 15 筆），可用 city / district / q（名稱或地址）篩選。"""
    args = request.args
    return ok(
        get_container().pharmacies.list_pharmacies(
            city=args.get("city"),
            district=args.get("district"),
            query=args.get("q"),
            page=_page_arg(),
        )
    )


@admin_bp.post("/pharmacies/import")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
@rate_limit("pharmacy_import", limit=10, per_seconds=600, by="user")
def import_pharmacies():
    """健保署「特約醫事機構」（只取藥局列）或衛福部「藥局基本資料」CSV；同代碼更新、否則新增。"""
    return ok(get_container().pharmacies.import_pharmacies(_csv_rows_from_request()))


@admin_bp.get("/drugs")
@require_role(*STAFF_ROLES)
def list_drugs():
    """後台「藥品」頁：表格分頁（每頁 15 筆），可用 q（代號 / 名稱 / 成分）篩選。"""
    return ok(get_container().pharmacies.list_drugs(query=request.args.get("q"), page=_page_arg()))


@admin_bp.post("/drugs/import")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
@rate_limit("drug_import", limit=10, per_seconds=600, by="user")
def import_drugs():
    """健保署「健保用藥品項」CSV（約 96MB）；同藥品代號只保留有效迄日最新的一列。"""
    return ok(get_container().pharmacies.import_drugs(_csv_rows_from_request()))


# ---- 藥局自己維護資料、處理領藥需求（藥局人員 / 藥局管理員；SUPER_ADMIN 可代管）----


@admin_bp.get("/pharmacy")
@require_permission(Permission.PHARMACY_SETTINGS)
def my_pharmacy():
    """後台「我的藥局」：登入者所屬藥局的資料；SUPER_ADMIN 用 `?pharmacy_id=` 指定。"""
    return ok(
        get_container().pharmacies.get_managed(
            current_principal(), request.args.get("pharmacy_id") or None
        )
    )


@admin_bp.patch("/pharmacies/<pharmacy_id>")
@require_permission(Permission.PHARMACY_SETTINGS)
def update_pharmacy(pharmacy_id: str):
    """改電話 / 地址 / 營業時段（只能改自己的藥局）；名稱與啟用狀態只有 SUPER_ADMIN 能改。"""
    return ok(get_container().pharmacies.update(current_principal(), pharmacy_id, _json_body()))


@admin_bp.get("/pickups")
@require_permission(Permission.PICKUP_MANAGE)
def list_pickups():
    """病人指定到本藥局的領藥需求（分頁）。`?status=requested|ready|picked_up|cancelled`。"""
    args = request.args
    return ok(
        get_container().pickups.list(
            current_principal(),
            status=args.get("status") or None,
            page=_page_arg(),
            pharmacy_id=args.get("pharmacy_id") or None,
        )
    )


@admin_bp.patch("/pickups/<pickup_id>")
@require_permission(Permission.PICKUP_MANAGE)
def update_pickup(pickup_id: str):
    """改狀態：ready（備好，推播病人）/ picked_up（已領）/ cancelled（無法調劑，需填原因）。"""
    body = _json_body()
    return ok(
        get_container().pickups.set_status(
            current_principal(), pickup_id, str(body.get("status") or ""), body.get("note")
        )
    )


# ---- 推播管理（目前只有 SUPER_ADMIN：看得到所有使用者的提醒排程與藥名）----


@admin_bp.get("/pushes/overview")
@require_permission(Permission.PUSH_MANAGE)
def push_overview():
    """今天的推播數字（送出 / 已服用 / 失敗 / 略過）、有效排程數、收不到推播的人數。"""
    return ok(get_container().push_admin.overview())


@admin_bp.get("/pushes/deliveries")
@require_permission(Permission.PUSH_MANAGE)
def list_push_deliveries():
    """推播紀錄（分頁，最近的在前）。`?status=pending|sent|taken|snoozed|skipped|failed`。"""
    return ok(
        get_container().push_admin.list_deliveries(
            status=request.args.get("status") or None, page=_page_arg()
        )
    )


@admin_bp.get("/pushes/schedules")
@require_permission(Permission.PUSH_MANAGE)
def list_push_schedules():
    """提醒排程（分頁）。`?active=1` 只看啟用的、`?active=0` 只看停用的，不帶 = 全部。"""
    flag = request.args.get("active")
    active = None if flag in (None, "") else flag == "1"
    return ok(get_container().push_admin.list_schedules(active=active, page=_page_arg()))


@admin_bp.patch("/pushes/schedules/<schedule_id>")
@require_permission(Permission.PUSH_MANAGE)
def update_push_schedule(schedule_id: str):
    """停用 / 啟用一個提醒排程（body `{"active": true|false}`），會寫稽核紀錄。"""
    return ok(
        get_container().push_admin.set_schedule_active(
            current_principal(), schedule_id, _json_body().get("active")
        )
    )


@admin_bp.patch("/hospitals/<hospital_id>")
@require_permission(Permission.HOSPITAL_SETTINGS, Permission.HOSPITAL_MANAGE, any_of=True)
def update_hospital(hospital_id: str):
    return ok(get_container().hospitals.update(current_principal(), hospital_id, _json_body()))


@admin_bp.get("/users")
@require_permission(Permission.STAFF_MANAGE)
def list_users():
    """人員清單：單位管理員只看得到自己單位（院所或藥局）的人，SUPER_ADMIN 看全部。"""
    actor = current_principal()
    # 藥局管理員的範圍是藥局，其餘（院所管理員 / SUPER_ADMIN）照院所範圍
    pharmacy_scope = actor.pharmacy_id if actor.role is not Role.SUPER_ADMIN else None
    hospital_scope = None if pharmacy_scope else AccessPolicy.hospital_scope(actor)
    scoped = bool(pharmacy_scope or hospital_scope)
    staff_only = request.args.get("staff", "1") != "0"
    users = get_container().user_repo.search(
        roles=STAFF_ROLES if staff_only else None,
        hospital_id=hospital_scope,
        pharmacy_id=pharmacy_scope,
        # 單位管理員要能找到剛登入、還沒指派單位的新同仁
        include_unassigned=not staff_only and scoped,
        query=request.args.get("q") or None,
    )
    if scoped:
        # 單位管理員管不到系統方帳號，不列出（前端沒有對應的角色選項，會誤顯示成訪客）
        users = [u for u in users if u.role not in SYSTEM_ROLES]
    container = get_container()
    rows = []
    for user in users:
        row = container.users.to_principal(user).to_dict()
        # 藥局與診所都有上萬家，前端沒有完整的名稱對照表，直接附上所屬單位的名稱
        if user.pharmacy_id:
            pharmacy = container.pharmacy_repo.get(user.pharmacy_id)
            row["pharmacy_name"] = pharmacy["name"] if pharmacy else None
        if user.hospital_id:
            hospital = container.hospital_repo.get(user.hospital_id)
            row["hospital_name"] = hospital["name"] if hospital else None
        rows.append(row)
    return ok(rows)


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
        pharmacy_id=body.get("pharmacy_id") or None,
        clear_hospital="hospital_id" in body and not body.get("hospital_id"),
    )
    return ok(get_container().users.to_principal(user).to_dict())


@admin_bp.get("/feedback")
@require_permission(Permission.FEEDBACK_MANAGE)
def list_feedback():
    """問題回報（依院所隔離）。`?status=open|resolved`，不帶 = 全部。"""
    return ok(
        get_container().feedback.list(current_principal(), request.args.get("status") or None)
    )


@admin_bp.patch("/feedback/<feedback_id>")
@require_permission(Permission.FEEDBACK_MANAGE)
def update_feedback(feedback_id: str):
    body = _json_body()
    return ok(
        get_container().feedback.set_status(
            current_principal(), feedback_id, str(body.get("status") or ""), body.get("note")
        )
    )


@admin_bp.get("/patients")
@require_permission(Permission.PATIENT_READ)
def list_patients():
    patients = get_container().patient_records.list_patients(
        current_principal(),
        status=request.args.get("status", "pending"),
        query=request.args.get("q"),
    )
    return ok(patients)


@admin_bp.get("/patients/unclaimed")
@require_permission(Permission.PATIENT_VERIFY)
@rate_limit("patient_unclaimed_search", limit=60, per_seconds=600, by="user")
def find_unclaimed_patients():
    """搜尋病人：用手機 / 姓名 / 末四碼（至少一個）精準找還沒加入本院的病人（在別家就醫不影響）。"""
    return ok(
        get_container().patient_records.find_unclaimed(
            current_principal(),
            phone=request.args.get("phone"),
            name=request.args.get("name"),
            last4=request.args.get("last4"),
        )
    )


@admin_bp.get("/patients/<user_id>")
@require_permission(Permission.PATIENT_READ)
def get_patient(user_id: str):
    return ok(get_container().patient_records.get_patient(current_principal(), user_id))


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


@admin_bp.post("/patients/<user_id>/request-hospital-confirmation")
@require_permission(Permission.PATIENT_VERIFY)
@rate_limit("hospital_confirmation", limit=20, per_seconds=600, by="user")
def request_hospital_confirmation(user_id: str):
    """推播 LINE 請病人自己確認「在本院就醫」；確認後才能 verify。"""
    body = _optional_json_body()
    return ok(
        get_container().patient_records.request_hospital_confirmation(
            current_principal(), user_id, hospital_id=body.get("hospital_id")
        )
    )


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
