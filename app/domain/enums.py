"""領域列舉：角色、權限、綁定狀態、意圖。

角色依「服務地圖」拆成兩群：
- 用戶端：GUEST（未綁定）→ PATIENT（本人）/ FAMILY（家屬）
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

    # ---- 不需基本資料即可使用（服務地圖 ①、③、找醫院、找真人） ----
    CONSULT_SYMPTOM = "consult:symptom"  # ① 病況諮詢
    CONSULT_HOSPITAL = "consult:hospital"  # 找醫院 / 院所查詢 + 壅塞燈號
    CONSULT_MEDICATION_ONCE = "consult:medication_once"  # ③ 藥物諮詢單次查詢
    CONSULT_EDUCATION_GENERIC = "consult:education_generic"  # ② 衛教諮詢通用版
    HANDOFF_REQUEST = "handoff:request"  # 轉真人客服（任何時候一鍵可按）

    # ---- 綁定流程（B1–B9） ----
    BINDING_CREATE = "binding:create"
    BINDING_REVOKE = "binding:revoke"  # 解除綁定（二次確認）

    # ---- 已綁定使用者專屬 ----
    PROFILE_READ_OWN = "profile:read_own"
    PROFILE_WRITE_OWN = "profile:write_own"
    EDUCATION_PERSONAL = "education:personal"  # ② 衛教個人化版 / 每日任務推播
    REMINDER_MANAGE_OWN = "reminder:manage_own"  # ④ 服藥小幫手跨日排程提醒

    # ---- 真人客服 ----
    HANDOFF_ACCEPT = "handoff:accept"
    HANDOFF_REPLY = "handoff:reply"
    CONVERSATION_READ = "conversation:read"
    BINDING_MANUAL_VERIFY = "binding:manual_verify"  # B8 連續 3 次失敗轉真人核對
    BINDING_UNLOCK = "binding:unlock"  # B9 鎖定並轉真人處理
    PATIENT_READ = "patient:read"

    # ---- 醫護 / 衛教 ----
    EDUCATION_CONTENT_MANAGE = "education:content_manage"
    ROSTER_READ = "roster:read"  # 院方識別名單

    # ---- 院所管理 ----
    ROSTER_MANAGE = "roster:manage"
    STAFF_MANAGE = "staff:manage"
    HOSPITAL_SETTINGS = "hospital:settings"
    RICHMENU_MANAGE = "richmenu:manage"
    AUDIT_READ = "audit:read"

    # ---- 系統 ----
    HOSPITAL_MANAGE = "hospital:manage"
    SYSTEM_MANAGE = "system:manage"


class BindingStatus(StrEnum):
    """基本資料是否已填且驗證通過（= 是否綁定的唯一依據）。"""

    UNBOUND = "unbound"
    PENDING = "pending"  # B1–B5 進行中
    VERIFIED = "verified"  # B7 綁定完成
    LOCKED = "locked"  # B9 已被其他 LINE 綁定 / 異常鎖定，需真人處理


class IdentityType(StrEnum):
    """B2 身分別。"""

    SELF = "self"
    FAMILY = "family"


class VerificationMethod(StrEnum):
    """B4 驗證方式。"""

    DISCHARGE_CODE = "discharge_code"  # 出院單一次性代碼
    SMS_OTP = "sms_otp"  # 手機簡訊 OTP


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
