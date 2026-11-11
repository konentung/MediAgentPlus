"""領域列舉：角色、權限、綁定狀態、意圖。

角色分成三方：
- 用戶端（END_USER_ROLES）：GUEST（未驗證）→ PATIENT（本人）/ FAMILY（家屬）
- 院方（HOSPITAL_ROLES）：一定屬於某個單位（醫院 / 診所掛 `hospital_id`，藥局掛 `pharmacy_id`）
  - HOSPITAL_ADMIN：單位管理員，醫院 / 診所 / 藥局共用；實際權限依所屬單位類型決定（見 rbac.py）
  - CLINIC_STAFF：診所人員（助理 / 護理師），一個角色做完病人、就診、回報、衛教上傳
  - HOSPITAL_STAFF / MEDICAL_STAFF：大醫院分工 —— 行政人員（核對建檔、回報）/
    醫護人員（就診紀錄、風險與追蹤、衛教上傳）
  - PHARMACY_STAFF：藥局人員（處理病人的領藥需求、維護自己藥局的資料）
- 系統方（SYSTEM_ROLES）：SUPER_ADMIN（開發 / 維運人員，不屬於任何單位）

`STAFF_ROLES` = 院方 + 系統方，代表「可以登入後台的人」。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    GUEST = "guest"
    PATIENT = "patient"
    FAMILY = "family"
    CLINIC_STAFF = "clinic_staff"
    HOSPITAL_STAFF = "hospital_staff"
    MEDICAL_STAFF = "medical_staff"
    PHARMACY_STAFF = "pharmacy_staff"
    HOSPITAL_ADMIN = "hospital_admin"
    SUPER_ADMIN = "super_admin"

    @property
    def is_end_user(self) -> bool:
        return self in END_USER_ROLES

    @property
    def is_hospital_staff(self) -> bool:
        """院方角色：必須有所屬單位（院所或藥局），受單位隔離。"""
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
# 只存在於醫院 / 診所的角色（所屬單位一定是院所）
CARE_STAFF_ROLES: frozenset[Role] = frozenset(
    {Role.CLINIC_STAFF, Role.HOSPITAL_STAFF, Role.MEDICAL_STAFF}
)
# 只存在於藥局的角色（所屬單位一定是藥局）
PHARMACY_STAFF_ROLES: frozenset[Role] = frozenset({Role.PHARMACY_STAFF})
# 院方：上面兩組 + 單位管理員（所屬單位可以是院所或藥局，二選一）
HOSPITAL_ROLES: frozenset[Role] = CARE_STAFF_ROLES | PHARMACY_STAFF_ROLES | {Role.HOSPITAL_ADMIN}
SYSTEM_ROLES: frozenset[Role] = frozenset({Role.SUPER_ADMIN})
STAFF_ROLES: frozenset[Role] = HOSPITAL_ROLES | SYSTEM_ROLES


class Permission(StrEnum):
    """一把權限 = 一組需要在角色之間區分的操作。

    角色（Role）回答「你是誰」，權限回答「你被允許做什麼」；誰拿到哪些權限由 rbac.py 的
    ROLE_PERMISSIONS 決定。檢查的地方只有兩種：API 的 `@require_permission(...)` 與
    LINE handler 的 `IntentHandler.required_permission`；後台選單的 `data-perm` 用同一個字串
    決定要不要顯示連結。

    命名慣例（格式 resource:action）：
    - `CONSULT_*`：LINE 端的諮詢功能，動作在前是沿用 Rich Menu 的講法
    - `*_OWN`：使用者操作自己的資料，不需要院方帳號
    - `*_SETTINGS`：維護自己所屬單位（院所 / 藥局）的資料，受單位隔離
    - `*_MANAGE`：後台處理資料，或系統方管理全部單位
    同一個頁面拆成多把權限（例如病人明細的 READ / WRITE / VERIFY）是因為不同角色能做的事不同；
    只有單一角色會用到的功能不另外拆。

    另一道門「基本資料是否驗證通過」（`requires_verified` / `@require_verified`）看的是
    BindingStatus，跟權限無關。
    """

    # ── 公開：加好友就有 ──
    CONSULT_SYMPTOM = "consult:symptom"  # 病況諮詢：描述症狀、建議科別
    CONSULT_HOSPITAL = "consult:hospital"  # 找醫院：依位置推薦急救責任醫院、附近診所
    # 找藥局：拍處方箋、藥名查詢、找領藥的藥局
    CONSULT_MEDICATION = "consult:medication"
    FEEDBACK_SUBMIT = "feedback:submit"  # 送出問題回報，不需填過病人表單
    INTAKE_WRITE_OWN = "intake:write_own"  # 填寫 / 送出自己的基本資料表，驗證前就可以
    PRESCRIPTION_MANAGE_OWN = "prescription:manage_own"  # 拍處方箋辨識、檢視 / 修改自己的處方
    REMINDER_MANAGE_OWN = "reminder:manage_own"  # 管理自己的服藥提醒，不需先驗證基本資料

    # ── 已驗證的病人 / 家屬 ──
    PROFILE_READ_OWN = "profile:read_own"  # 查看自己的基本資料
    # TODO: 出院後衛教推播尚未實作（排程、資料表、後台頁面都還沒有），目前只有病人主動問才回答；
    # 做的時候推播內容與紀錄放進後台「推播管理」頁
    EDUCATION_PERSONAL = "education:personal"  # 個人化衛教、每日任務推播

    # ── 院所：行政人員（HOSPITAL_STAFF）──
    FEEDBACK_MANAGE = "feedback:manage"  # 後台查看問題回報、標記已處理
    PATIENT_READ = "patient:read"  # 查看病人自填資料與本院紀錄（身分證只看得到遮罩）、新增備註
    PATIENT_WRITE = "patient:write"  # 修正病人自填資料、風險警示、追蹤事項（行政與醫護都有）
    PATIENT_VERIFY = "patient:verify"  # 核對病人身份後建檔或退回（只有行政人員）

    # ── 院所：醫護人員（MEDICAL_STAFF）──
    ENCOUNTER_MANAGE = "encounter:manage"  # 新增 / 修改就診與住院紀錄、出院代碼
    EDUCATION_CONTENT_MANAGE = "education:content_manage"  # 本院衛教文件：上傳 PDF、新增、編輯
    MEDICATION_MANAGE = "medication:manage"  # 後台查看藥局清單

    # ── 藥局人員（PHARMACY_STAFF）──
    PHARMACY_SETTINGS = "pharmacy:settings"  # 維護自己藥局的電話 / 地址 / 營業時段
    PICKUP_MANAGE = "pickup:manage"  # 處理病人指定到本藥局的領藥需求

    # ── 單位管理員 ──
    STAFF_MANAGE = "staff:manage"  # 指派本單位人員的角色
    HOSPITAL_SETTINGS = "hospital:settings"  # 修改自己院所的名稱 / 電話 / 地址
    AUDIT_READ = "audit:read"  # 查看稽核紀錄（鎖定 / 解鎖帳號等）
    # 把本院衛教片段算成向量、重建片段；會產生 OpenAI 費用，所以不給一般人員
    EDUCATION_EMBED = "education:embed"

    # ── 系統方 ──
    HOSPITAL_MANAGE = "hospital:manage"  # 新增 / 匯入 / 修改所有院所
    REFERENCE_DATA_MANAGE = "reference_data:manage"  # 匯入藥局 / 藥品等開放資料
    PUSH_MANAGE = "push:manage"  # 推播管理：查看推播紀錄、停用 / 啟用提醒排程
    PERMISSION_MATRIX_READ = "permission:read"  # 後台「權限表」：看每個角色有哪些權限


class BindingStatus(StrEnum):
    """基本資料是否已填且驗證通過"""

    UNBOUND = "unbound"  # 未填寫或被退回
    PENDING = "pending"  # 已送出表單，待院方核對
    VERIFIED = "verified"  # 驗證通過，可使用個人化功能
    LOCKED = "locked"  # 異常鎖定，由院方在後台解鎖


class Intent(StrEnum):
    """Rich Menu 六格與其他功能的意圖。"""

    SYMPTOM = "symptom"  # 病況諮詢
    FIND_HOSPITAL = "hospital"  # 找醫院
    DISCHARGE = "discharge"  # 衛教諮詢
    MEDICATION = "medication"  # 找藥局
    REMINDER = "reminder"  # 服藥提醒
    FEEDBACK = "feedback"  # 問題回報（LIFF 問題回報表單）
    SETTINGS = "settings"  # 設定頁
    INTAKE = "intake"  # 基本資料：Flex 列出「基本資料表」與「我的就醫院所」兩張表單
    MENU = "menu"  # 主選單（列出六個功能的按鈕）
    ASSISTANT = "assistant"  # 自由文字 → AI（RAG）判斷事件後套用模組回應
    HOSPITAL_CONFIRM = "hospital_confirm"  # 病人回答「是否在某院所就醫」（院方請求確認的按鈕）
