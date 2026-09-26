"""領域列舉：角色、權限、綁定狀態、意圖。

角色依「服務地圖」拆成兩群：
- 用戶端：GUEST（未驗證）→ PATIENT（本人）/ FAMILY（家屬）
- 院方端：CS_AGENT（真人客服）、MEDICAL_STAFF（醫護 / 衛教）、HOSPITAL_ADMIN、SUPER_ADMIN
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    GUEST = "guest"
    PATIENT = "patient"
    FAMILY = "family"
    CS_AGENT = "cs_agent"
    MEDICAL_STAFF = "medical_staff"
    HOSPITAL_ADMIN = "hospital_admin"
    SUPER_ADMIN = "super_admin"

    @property
    def is_staff(self) -> bool:
        return self in STAFF_ROLES

    @property
    def is_end_user(self) -> bool:
        return self in END_USER_ROLES


END_USER_ROLES: frozenset[Role] = frozenset({Role.GUEST, Role.PATIENT, Role.FAMILY})
STAFF_ROLES: frozenset[Role] = frozenset(
    {Role.CS_AGENT, Role.MEDICAL_STAFF, Role.HOSPITAL_ADMIN, Role.SUPER_ADMIN}
)


class Permission(StrEnum):
    """細粒度權限，命名格式為 resource:action。"""

    CONSULT_SYMPTOM = "consult:symptom"  # ① 病況諮詢
    CONSULT_HOSPITAL = "consult:hospital"  # 找醫院 / 院所查詢 + 壅塞燈號
    CONSULT_MEDICATION_ONCE = "consult:medication_once"  # ③ 藥物諮詢單次查詢
    CONSULT_EDUCATION_GENERIC = "consult:education_generic"  # ② 衛教諮詢通用版
    HANDOFF_REQUEST = "handoff:request"  # 轉真人客服（任何時候一鍵可按）
    INTAKE_WRITE_OWN = "intake:write_own"  # 填寫 / 送出病人自填表（驗證前即可）

    PROFILE_READ_OWN = "profile:read_own"
    PROFILE_WRITE_OWN = "profile:write_own"
    EDUCATION_PERSONAL = "education:personal"  # ② 衛教個人化版 / 每日任務推播
    REMINDER_MANAGE_OWN = "reminder:manage_own"  # ④ 服藥小幫手跨日排程提醒

    HANDOFF_ACCEPT = "handoff:accept"
    HANDOFF_REPLY = "handoff:reply"
    CONVERSATION_READ = "conversation:read"
    PATIENT_READ = "patient:read"  # 病人自填資料 + 本院紀錄（身分證遮罩）

    EDUCATION_CONTENT_MANAGE = "education:content_manage"
    PATIENT_WRITE = "patient:write"  # 修正自填資料、風險警示、追蹤事項
    PATIENT_VERIFY = "patient:verify"  # 核對身份 → 建檔 / 退回
    ENCOUNTER_MANAGE = "encounter:manage"  # 就診 / 住院紀錄

    STAFF_MANAGE = "staff:manage"
    HOSPITAL_SETTINGS = "hospital:settings"
    RICHMENU_MANAGE = "richmenu:manage"
    AUDIT_READ = "audit:read"
    PATIENT_IDENTITY_READ = "patient:identity_read"  # 讀取身分證明碼

    HOSPITAL_MANAGE = "hospital:manage"
    SYSTEM_MANAGE = "system:manage"


class BindingStatus(StrEnum):
    """基本資料是否已填且驗證通過（服務地圖上的「已驗證？」菱形判斷）。

    綁定流程本身尚未設計，目前只有這個狀態欄位；狀態的轉換邏輯待流程定案後實作。
    """

    UNBOUND = "unbound"
    PENDING = "pending"  # 綁定進行中
    VERIFIED = "verified"  # 驗證通過，可使用個人化功能
    LOCKED = "locked"  # 異常鎖定，需真人處理


class Intent(StrEnum):
    """Rich Menu 六格與其他入口的意圖。"""

    SYMPTOM = "symptom"  # 我不舒服
    FIND_HOSPITAL = "hospital"  # 找醫院
    DISCHARGE = "discharge"  # 我剛出院
    MEDICATION = "medication"  # 去哪拿藥
    REMINDER = "reminder"  # 服藥提醒
    HUMAN = "human"  # 找真人
    SETTINGS = "settings"  # 設定頁
    EMERGENCY = "emergency"  # 紅旗命中 → 緊急畫面
