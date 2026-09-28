"""領域列舉：角色、權限、綁定狀態、意圖。

角色分成三方：
- 用戶端（END_USER_ROLES）：GUEST（未驗證）→ PATIENT（本人）/ FAMILY（家屬）
- 院方（HOSPITAL_ROLES）：HOSPITAL_STAFF（管病人：病人核對建檔、就診紀錄、意見回饋）、
  MEDICAL_STAFF（管藥 / 衛教：處方與服藥紀錄、衛教內容）、
  HOSPITAL_ADMIN（院所管理員：前兩者全部 + 人員 / 院所管理）
- 系統方（SYSTEM_ROLES）：SUPER_ADMIN（開發 / 維運人員，不屬於任何院所）

`STAFF_ROLES` = 院方 + 系統方，代表「可以登入後台的人」。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    GUEST = "guest"
    PATIENT = "patient"
    FAMILY = "family"
    HOSPITAL_STAFF = "hospital_staff"
    MEDICAL_STAFF = "medical_staff"
    HOSPITAL_ADMIN = "hospital_admin"
    SUPER_ADMIN = "super_admin"

    @property
    def is_end_user(self) -> bool:
        return self in END_USER_ROLES

    @property
    def is_hospital_staff(self) -> bool:
        """院方角色：必須有所屬院所，受院所隔離。"""
        return self in HOSPITAL_ROLES

    @property
    def is_system(self) -> bool:
        """系統方角色：不屬於任何院所、不受院所隔離。"""
        return self in SYSTEM_ROLES

    @property
    def is_staff(self) -> bool:
        """可登入後台（院方 + 系統方）。"""
        return self in STAFF_ROLES


END_USER_ROLES: frozenset[Role] = frozenset({Role.GUEST, Role.PATIENT, Role.FAMILY})
HOSPITAL_ROLES: frozenset[Role] = frozenset(
    {Role.HOSPITAL_STAFF, Role.MEDICAL_STAFF, Role.HOSPITAL_ADMIN}
)
SYSTEM_ROLES: frozenset[Role] = frozenset({Role.SUPER_ADMIN})
STAFF_ROLES: frozenset[Role] = HOSPITAL_ROLES | SYSTEM_ROLES


class Permission(StrEnum):
    """細粒度權限，命名格式為 resource:action。"""

    CONSULT_SYMPTOM = "consult:symptom"  # ① 病況諮詢
    CONSULT_HOSPITAL = "consult:hospital"  # 找醫院 / 院所查詢 + 壅塞燈號
    CONSULT_MEDICATION_ONCE = "consult:medication_once"  # ③ 藥物諮詢單次查詢
    CONSULT_EDUCATION_GENERIC = "consult:education_generic"  # ② 衛教諮詢通用版
    FEEDBACK_SUBMIT = "feedback:submit"  # 送出意見回饋（任何人，不需填過病人表單）
    INTAKE_WRITE_OWN = "intake:write_own"  # 填寫 / 送出病人自填表（驗證前即可）
    PRESCRIPTION_MANAGE_OWN = "prescription:manage_own"  # 處方箋拍照辨識 / 確認 / 修改（自己的）
    REMINDER_MANAGE_OWN = "reminder:manage_own"  # ④ 服藥小幫手排程提醒（任何人都可用）

    PROFILE_READ_OWN = "profile:read_own"
    PROFILE_WRITE_OWN = "profile:write_own"
    EDUCATION_PERSONAL = "education:personal"  # ② 衛教個人化版 / 每日任務推播

    FEEDBACK_MANAGE = "feedback:manage"  # 後台查看 / 標記處理意見回饋
    CONVERSATION_READ = "conversation:read"
    PATIENT_READ = "patient:read"  # 病人自填資料 + 本院紀錄（身分證遮罩）

    EDUCATION_CONTENT_MANAGE = "education:content_manage"
    MEDICATION_MANAGE = "medication:manage"  # 後台檢視 / 管理病人處方與服藥紀錄（頁面尚未實作）
    PATIENT_WRITE = "patient:write"  # 修正自填資料、風險警示、追蹤事項
    PATIENT_VERIFY = "patient:verify"  # 核對身份 → 建檔 / 退回
    ENCOUNTER_MANAGE = "encounter:manage"  # 就診 / 住院紀錄

    STAFF_MANAGE = "staff:manage"
    HOSPITAL_SETTINGS = "hospital:settings"
    AUDIT_READ = "audit:read"

    HOSPITAL_MANAGE = "hospital:manage"
    REFERENCE_DATA_MANAGE = "reference_data:manage"  # 匯入藥局 / 藥品等開放資料（系統）
    SYSTEM_MANAGE = "system:manage"


class BindingStatus(StrEnum):
    """基本資料是否已填且驗證通過（服務地圖上的「已驗證？」菱形判斷）。

    UNBOUND → 送出 LIFF 表單 → PENDING → 院所新增病人、病人在 LINE 上確認在該院就醫、
    院方核對建檔 → VERIFIED；院方退回 → UNBOUND；異常由院方鎖定 → LOCKED。
    轉換邏輯在 `IntakeService` 與 `PatientRecordService`。
    """

    UNBOUND = "unbound"  # 未填寫或被退回
    PENDING = "pending"  # 已送出表單，待院方核對
    VERIFIED = "verified"  # 驗證通過，可使用個人化功能
    LOCKED = "locked"  # 異常鎖定，由院方在後台解鎖


class Intent(StrEnum):
    """Rich Menu 六格與其他入口的意圖。"""

    SYMPTOM = "symptom"  # 我不舒服
    FIND_HOSPITAL = "hospital"  # 找醫院
    DISCHARGE = "discharge"  # 我剛出院
    MEDICATION = "medication"  # 去哪拿藥
    REMINDER = "reminder"  # 服藥提醒
    FEEDBACK = "feedback"  # 意見回饋（LIFF 表單）
    SETTINGS = "settings"  # 設定頁
    MENU = "menu"  # 主選單（列出六個功能的按鈕）
    EMERGENCY = "emergency"  # 紅旗命中 → 緊急畫面
    ASSISTANT = "assistant"  # 自由文字 → AI（RAG）判斷事件後套用模組回應
    HOSPITAL_CONFIRM = "hospital_confirm"  # 病人回答「是否在某院所就醫」（院方請求確認的按鈕）
