"""院方後台頁面（Jinja + Bootstrap，頁內 fetch() 呼叫 /api/v1/admin/*）。

    /admin                    儀表板
    /admin/login              後台登入頁（LINE 登入按鈕；?next= 登入後要回的頁、?error= 顯示原因）
    /admin/patients           病人列表（待審核 / 已建檔）
    /admin/patients/<user_id> 病人詳情：核對、修正、就診、風險、追蹤、備註
    /admin/handoffs           真人客服工單
    /admin/staff              人員與角色
    /admin/settings           院所設定（本院資料、填表連結、所有院所）
    /admin/triage             急診動態分流：急診即時看板、分流建議、接收者（訂閱）管理
    /admin/knowledge          知識庫（AI 助理 RAG 的檢索來源）：文件維護、Markdown 匯入、試搜
    /admin/permissions        權限矩陣（查閱）
    /admin/audit              稽核紀錄（讀取身分證明碼、鎖定 / 解鎖）

頁面骨架不含資料、不需登入即可 render；登入狀態與權限由 admin/base.html 的 JS 以
sessionStorage 內的 JWT 打 /api/v1/auth/me 判斷，真正的授權仍在 API 端。
"""

from __future__ import annotations

import json

from flask import Blueprint, render_template, request, url_for

from app.api.v1.auth import _safe_redirect_to
from app.domain.enums import Permission
from app.domain.intake_schema import public_schema
from app.domain.triage_rules import public_rules as triage_rules
from app.services import patient_record_service as prs

admin_pages_bp = Blueprint("admin_pages", __name__, url_prefix="/admin")

# 權限的中文說明（院所設定頁的權限矩陣用）；新增 Permission 時要一起補，tests 會檢查
PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.CONSULT_SYMPTOM: "病況諮詢（我不舒服）",
    Permission.CONSULT_HOSPITAL: "找醫院、院所查詢與壅塞燈號",
    Permission.CONSULT_MEDICATION_ONCE: "藥物諮詢單次查詢（去哪拿藥）",
    Permission.CONSULT_EDUCATION_GENERIC: "通用版衛教內容",
    Permission.HANDOFF_REQUEST: "轉接真人客服",
    Permission.INTAKE_WRITE_OWN: "填寫 / 送出自己的基本資料表（驗證前即可）",
    Permission.PRESCRIPTION_MANAGE_OWN: "拍處方箋辨識、檢視 / 修改自己的處方",
    Permission.PROFILE_READ_OWN: "查看自己的基本資料",
    Permission.PROFILE_WRITE_OWN: "修改自己的基本資料",
    Permission.EDUCATION_PERSONAL: "個人化衛教與每日任務推播",
    Permission.REMINDER_MANAGE_OWN: "管理自己的服藥提醒（不需先驗證基本資料）",
    Permission.HANDOFF_ACCEPT: "接手真人客服工單",
    Permission.HANDOFF_REPLY: "回覆真人客服工單",
    Permission.CONVERSATION_READ: "查看使用者對話紀錄",
    Permission.PATIENT_READ: "查看病人自填資料與本院紀錄（身分證遮罩）、新增備註",
    Permission.TRIAGE_READ: "查看急診即時看板與分流建議",
    Permission.TRIAGE_SUBSCRIPTION_MANAGE: "管理分流建議接收者（119 勤指中心 / 診所）",
    Permission.EDUCATION_CONTENT_MANAGE: "管理衛教內容與 AI 知識庫（本院文件）",
    Permission.PATIENT_WRITE: "修正病人自填資料、風險警示、追蹤事項",
    Permission.PATIENT_VERIFY: "核對病人身份、建檔或退回",
    Permission.ENCOUNTER_MANAGE: "新增 / 修改就診與住院紀錄、出院代碼",
    Permission.STAFF_MANAGE: "指派院所人員角色",
    Permission.HOSPITAL_SETTINGS: "院所設定",
    Permission.RICHMENU_MANAGE: "管理 LINE Rich Menu",
    Permission.AUDIT_READ: "查看稽核紀錄",
    Permission.PATIENT_IDENTITY_READ: "查看病人身分證字號明碼",
    Permission.HOSPITAL_MANAGE: "管理所有院所（系統）",
    Permission.REFERENCE_DATA_MANAGE: "匯入藥局 / 藥品開放資料（系統）",
    Permission.SYSTEM_MANAGE: "系統管理",
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
    "binding_statuses": {
        "unbound": "未填寫",
        "pending": "待核對",
        "verified": "已建檔",
        "locked": "已鎖定",
    },
    "roles": {
        "guest": "訪客",
        "patient": "病人",
        "family": "家屬",
        "cs_agent": "真人客服",
        "medical_staff": "醫護",
        "hospital_admin": "院所管理員",
        "super_admin": "系統管理員",
    },
}


@admin_pages_bp.context_processor
def inject_admin_globals():
    return {"admin_choices_json": json.dumps(ADMIN_CHOICES, ensure_ascii=False)}


@admin_pages_bp.get("/")
def dashboard():
    return render_template("admin/dashboard.html", active="dashboard")


LOGIN_ERRORS = {
    "expired": "登入已過期，請重新登入。",
    "not_staff": "這個 LINE 帳號不是院方人員，無法進入後台。請改用其他帳號，或請管理員指派角色。",
    "unauthorized": "請先登入才能使用後台。",
}


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
        error=LOGIN_ERRORS.get(request.args.get("error") or ""),
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


@admin_pages_bp.get("/handoffs")
def handoffs():
    return render_template("admin/handoffs.html", active="handoffs")


@admin_pages_bp.get("/staff")
def staff():
    return render_template("admin/staff.html", active="staff")


@admin_pages_bp.get("/settings")
def settings():
    return render_template("admin/settings.html", active="settings")


@admin_pages_bp.get("/triage")
def triage():
    return render_template(
        "admin/triage.html",
        active="triage",
        triage_rules_json=json.dumps(triage_rules(), ensure_ascii=False),
    )


@admin_pages_bp.get("/knowledge")
def knowledge():
    from app.services.knowledge_service import CATEGORIES

    return render_template(
        "admin/knowledge.html",
        active="knowledge",
        categories_json=json.dumps(CATEGORIES, ensure_ascii=False),
    )


@admin_pages_bp.get("/permissions")
def permissions():
    return render_template("admin/permissions.html", active="permissions")


@admin_pages_bp.get("/audit")
def audit():
    return render_template("admin/audit.html", active="audit")
