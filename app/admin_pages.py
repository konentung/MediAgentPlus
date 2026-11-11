from __future__ import annotations

import json

from flask import Blueprint, render_template, request, url_for

from app.api.v1.auth import _safe_redirect_to
from app.domain.enums import Permission
from app.domain.intake_schema import public_schema
from app.domain.rbac import PHARMACY_ADMIN_KEY
from app.services import (
    feedback_service,
    pharmacy_service,
    pickup_service,
    push_admin_service,
)
from app.services import patient_record_service as prs
from app.services.knowledge_job_service import MAX_UPLOAD_FILES

admin_pages_bp = Blueprint("admin_pages", __name__, url_prefix="/admin")

# 權限表顯示用，順序與 Permission 的分組一致
PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    # 公開：加好友就有
    Permission.CONSULT_SYMPTOM: "病況諮詢：描述症狀、建議科別",
    Permission.CONSULT_HOSPITAL: "找醫院：依位置推薦急救責任醫院、附近診所",
    Permission.CONSULT_MEDICATION: "找藥局：拍處方箋、查藥名、找領藥的藥局",
    Permission.FEEDBACK_SUBMIT: "送出問題回報，不需填過基本資料",
    Permission.INTAKE_WRITE_OWN: "填寫、送出自己的基本資料，驗證前就可以",
    Permission.PRESCRIPTION_MANAGE_OWN: "拍處方箋辨識，查看、修改自己的處方",
    Permission.REMINDER_MANAGE_OWN: "管理自己的服藥提醒，不需先驗證基本資料",
    # 已驗證的病人 / 家屬
    Permission.PROFILE_READ_OWN: "查看自己的基本資料",
    Permission.EDUCATION_PERSONAL: "個人化衛教、每日任務推播（推播尚未實作）",
    # 院所：行政人員
    Permission.FEEDBACK_MANAGE: "查看問題回報、標記已處理",
    Permission.PATIENT_READ: "查看病人自填資料與本院紀錄（身分證只顯示末四碼）、新增備註",
    Permission.PATIENT_WRITE: "修正病人自填資料、風險警示、追蹤事項",
    Permission.PATIENT_VERIFY: "核對病人身份後建檔或退回",
    # 院所：醫護人員
    Permission.ENCOUNTER_MANAGE: "新增、修改就診與住院紀錄、出院代碼",
    Permission.EDUCATION_CONTENT_MANAGE: "管理本院衛教文件：上傳 PDF、新增、編輯",
    Permission.MEDICATION_MANAGE: "查看藥局清單",
    # 藥局人員
    Permission.PHARMACY_SETTINGS: "維護自己藥局的電話、地址、營業時段",
    Permission.PICKUP_MANAGE: "處理病人指定到本藥局的領藥需求",
    # 單位管理員
    Permission.STAFF_MANAGE: "指派本單位人員的角色",
    Permission.HOSPITAL_SETTINGS: "修改自己院所的名稱、電話、地址",
    Permission.AUDIT_READ: "查看稽核紀錄（鎖定、解鎖帳號等）",
    Permission.EDUCATION_EMBED: "把本院衛教片段算成向量、重建片段（會產生 OpenAI 費用）",
    # 系統方
    Permission.HOSPITAL_MANAGE: "新增、匯入、修改所有院所",
    Permission.REFERENCE_DATA_MANAGE: "匯入藥局、藥品等開放資料",
    Permission.PUSH_MANAGE: "推播管理：查看推播紀錄、停用或啟用提醒排程",
    Permission.PERMISSION_MATRIX_READ: "查看權限表（每個角色有哪些權限）",
}

# 後台表單用的選項與中文標籤（與 PatientRecordService 的白名單同源）
ADMIN_CHOICES = {
    "permissions": {p.value: desc for p, desc in PERMISSION_DESCRIPTIONS.items()},
    "identity_methods": dict(
        zip(prs.IDENTITY_METHODS, ("健保卡", "身分證", "現場核對", "電話核對"), strict=True)
    ),
    "encounter_types": dict(zip(prs.ENCOUNTER_TYPES, ("門診", "急診", "住院"), strict=True)),
    "flag_types": dict(
        zip(
            prs.FLAG_TYPES,
            ("跌倒風險", "過敏警示", "感染管制", "認知功能", "自傷風險", "其他"),
            strict=True,
        )
    ),
    "flag_levels": dict(zip(prs.FLAG_LEVELS, ("低", "中", "高"), strict=True)),
    "follow_up_kinds": dict(
        zip(prs.FOLLOW_UP_KINDS, ("回診", "檢查", "電訪", "換藥", "其他"), strict=True)
    ),
    "follow_up_statuses": dict(
        zip(prs.FOLLOW_UP_STATUSES, ("待處理", "已完成", "已取消"), strict=True)
    ),
    "note_visibility": dict(zip(prs.NOTE_VISIBILITY, ("僅院方", "病人可見"), strict=True)),
    "feedback_categories": feedback_service.CATEGORIES,
    "feedback_statuses": {"open": "待處理", "resolved": "已處理"},
    "binding_statuses": {
        "unbound": "未填寫",
        "pending": "待核對",
        "verified": "已建檔",
        "locked": "黑名單",
    },
    "pickup_statuses": pickup_service.STATUS_LABELS,
    "push_delivery_statuses": push_admin_service.DELIVERY_STATUS_LABELS,
    "weekdays": dict(
        zip(
            pharmacy_service.WEEKDAY_KEYS,
            ("週一", "週二", "週三", "週四", "週五", "週六", "週日"),
            strict=True,
        )
    ),
    "pharmacy_slots": pharmacy_service.SLOT_LABELS,
    "roles": {
        "guest": "訪客",
        "patient": "病人",
        "family": "家屬",
        "clinic_staff": "診所人員（全部院內功能）",
        "hospital_staff": "醫院行政人員（核對建檔 / 回報）",
        "medical_staff": "醫院醫護人員（就診紀錄 / 衛教）",
        "pharmacy_staff": "藥局人員",
        "hospital_admin": "單位管理員",
        PHARMACY_ADMIN_KEY: "單位管理員（藥局）",
        "super_admin": "系統管理員",
    },
}


@admin_pages_bp.context_processor
def inject_admin_globals():
    return {"admin_choices_json": json.dumps(ADMIN_CHOICES, ensure_ascii=False)}


@admin_pages_bp.get("/")
def dashboard():
    return render_template("admin/dashboard.html", active="dashboard")


@admin_pages_bp.get("/login")
def login():
    """後台專用登入頁。LIFF 端不經過這裡，維持 liff.init 的流程。"""
    next_path = _safe_redirect_to(request.args.get("next"))
    if not next_path.startswith("/admin"):
        next_path = "/admin"
    return render_template(
        "admin/login.html",
        next_path=next_path,
        login_url=url_for("api_v1.auth.line_login", redirect_to=next_path),
    )


@admin_pages_bp.get("/patients")
def patients():
    return render_template("admin/patients/list.html", active="patients")


@admin_pages_bp.get("/patients/<user_id>")
def patient_detail(user_id: str):
    return render_template(
        "admin/patients/detail.html",
        active="patients",
        user_id=user_id,
        schema_json=json.dumps(public_schema(), ensure_ascii=False),
    )


@admin_pages_bp.get("/feedback")
def feedback():
    return render_template("admin/feedback.html", active="feedback")


@admin_pages_bp.get("/staff")
def staff():
    return render_template("admin/staff.html", active="staff")


@admin_pages_bp.get("/settings")
def settings():
    return render_template("admin/settings.html", active="settings")


@admin_pages_bp.get("/pickups")
def pickups():
    return render_template("admin/pickups.html", active="pickups")


@admin_pages_bp.get("/pharmacy")
def my_pharmacy():
    return render_template("admin/pharmacy.html", active="pharmacy")


@admin_pages_bp.get("/pharmacies")
def pharmacies():
    return render_template("admin/pharmacies.html", active="pharmacies")


@admin_pages_bp.get("/drugs")
def drugs():
    return render_template("admin/drugs.html", active="drugs")


@admin_pages_bp.get("/knowledge")
def knowledge():
    from app.services.knowledge_service import CATEGORIES

    return render_template(
        "admin/knowledge.html",
        active="knowledge",
        categories_json=json.dumps(CATEGORIES, ensure_ascii=False),
    )


@admin_pages_bp.get("/imports")
def imports():
    return render_template("admin/imports.html", active="imports", max_pdf_files=MAX_UPLOAD_FILES)


@admin_pages_bp.get("/pushes")
def pushes():
    return render_template("admin/pushes.html", active="pushes")


@admin_pages_bp.get("/permissions")
def permissions():
    return render_template("admin/permissions.html", active="permissions")


@admin_pages_bp.get("/audit")
def audit():
    return render_template("admin/audit.html", active="audit")
