# 病人表單設計（Roadmap 第 6 項）

> 狀態：**已實作（2026-09-23）**。本文件是設計依據；實際程式位置見 CLAUDE.md「病人自填表與院方後台」。
> 與設計不同之處：
> - `patient_media`（藥袋照片）尚未做，等 Roadmap 第 4 項 OCR 定案再補。
> - `Hospital` 實體已建立（`hospitals` 表）。`preferred_hospital_id` 不再是表單欄位、也沒有 QR 連結（2026-09-28 起）：病人加好友填表即可；院方在後台「新增病人」用手機或姓名 + 末四碼精準找到本人並加入本院（寫入 `preferred_hospital_id`），推播 LINE 請病人按「是，我在這裡就醫」記下 `hospital_confirmed_at`。只有新增的院所看得到這位病人，其他院所看不到也不能動，SUPER_ADMIN 看全部。原設計的自由文字 `preferred_hospital_name` 已移除。
> - 後台登入沿用 `sessionStorage` + Bearer（§5.3），沒有另做 cookie session。
> - 身分證明碼沒有任何讀取端點與權限（2026-09-28 起），後台一律只看遮罩。

## 1. 設計原則

- **兩張表分離**：
  - **A. 病人自填表（Patient Intake Form）**：一般民眾在 LIFF 頁面填，用詞白話，
    不出現醫學代碼，能「不知道」就允許「不知道 / 不確定」。
  - **B. 院方紀錄表（Clinical Record）**：院所人員在後台填，含病歷號、診斷、
    出院代碼、內部備註等專業欄位，**病人看不到**（除非欄位標示為 `patient_visible`）。
- **A 屬於病人本人、跨院所共用；B 屬於院所、院所之間互相看不到。**
  病人的自填資料只有一份，掛在 `users.id` 下；每家院所要為這位病人「建檔」時，
  才在自己院所名下建立一筆 B 紀錄，並依 `AccessPolicy.can_access_hospital()` 隔離。
- **原始送出內容永遠保留**：病人每次送出的答案以 JSONB 快照存進
  `patient_form_submissions`，院方審核、修正時只改結構化表，不改快照，方便稽核與爭議追溯。
- **最小蒐集**：不蒐集與照護無關的敏感資料（宗教、性傾向、HIV 等特種個資不在表單內）。
  身分證字號不保存明碼與密文，只留末四碼與 HMAC 指紋，畫面上一律遮罩顯示。
- **跟綁定流程的關係**：本表單送出即是「基本資料已填」，院方核對身份通過即是「驗證通過」。
  這等於給了 B1–B9 一個具體提案（見 §6），但綁定流程尚未定案，此處只做預留、不綁死。

## 2. A. 病人自填表（LIFF，分 6 步）

欄位型別：`text` / `date` / `select` / `multi` / `bool` / `list`（可新增多筆）/ `file`。
「必填」欄位是最低建檔門檻；其餘允許跳過，之後在設定頁補填。

### 步驟 1 — 填表人與病人身份

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 我是 | `filler_relation` | select：本人 / 家屬代填 | ✓ | 決定 `Role.PATIENT` 或 `Role.FAMILY` |
| 代填者姓名 | `filler_name` | text | 家屬代填時 ✓ | |
| 代填者與病人關係 | `filler_relationship` | select：配偶 / 子女 / 父母 / 手足 / 看護 / 其他 | 家屬代填時 ✓ | |
| 代填者手機 | `filler_phone` | text | 家屬代填時 ✓ | `^09\d{8}$` |
| 病人姓名 | `full_name` | text | ✓ | 2–50 字 |
| 身分證 / 居留證字號 | `national_id` | text | ✓ | 台灣身分證檢核碼；外籍以居留證格式檢核；**只存末四碼與指紋，不存明碼 / 密文** |
| 出生日期 | `birth_date` | date | ✓ | 早於今天；年齡 < 18 提示需監護人 |
| 生理性別 | `sex` | select：男 / 女 / 其他 | ✓ | 用於用藥與懷孕相關欄位顯示 |
| 慣用語言 | `preferred_language` | select：國語 / 台語 / 客語 / 英語 / 其他 | | 供客服與衛教內容選擇 |

### 步驟 2 — 聯絡方式與緊急聯絡人

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 手機 | `phone` | text | ✓ | `^09\d{8}$`；與 LINE 帳號無關，供院方電話聯絡 |
| Email | `email` | text | | RFC 格式 |
| 通訊地址 | `address` | 結構：`city` / `district` / `street` | ✓ | 縣市、鄉鎮市區用 select |
| 緊急聯絡人 | `emergency_contacts` | list（0–3 筆） | | 選填但建議填；每筆：`name`、`relationship`、`phone`、`is_primary` |

### 步驟 3 — 基本健康狀況

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 身高（cm） | `height_cm` | number | ✓ | 50–250，供劑量 / 照護建議計算 |
| 體重（kg） | `weight_kg` | number | ✓ | 2–300 |
| 血型 | `blood_type` | select：A / B / AB / O / 不知道 | | 加 Rh：`rh`（+ / − / 不知道） |
| 是否懷孕或哺乳 | `pregnancy_status` | select：否 / 懷孕中 / 哺乳中 / 不確定 | | 僅 `sex=女` 顯示 |
| 吸菸 | `smoking` | select：從不 / 已戒 / 目前有 | | 目前有 → `smoking_per_day` |
| 飲酒 | `drinking` | select：從不 / 偶爾 / 經常 | | |
| 嚼檳榔 | `betel_nut` | select：從不 / 已戒 / 目前有 | | |

### 步驟 4 — 過敏與病史（最重要的一頁）

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 藥物過敏 | `drug_allergies` | list | ✓（可選「無」） | 每筆：`substance`（藥名，可自由輸入）、`reaction`（皮疹 / 呼吸困難 / 腫脹 / 其他）、`severity`（輕 / 中 / 重） |
| 食物過敏 | `food_allergies` | list | ✓（可選「無」） | 同上結構 |
| 其他過敏 | `other_allergies` | multi：顯影劑 / 乳膠 / 麻醉藥 / 其他 | | 其他 → 文字補充 |
| 慢性病 / 長期病史 | `chronic_conditions` | multi | ✓（可選「無」） | 高血壓 / 糖尿病 / 心臟病 / 腎臟病（含洗腎）/ 肝病 / 氣喘或 COPD / 中風 / 癌症 / 癲癇 / 甲狀腺疾病 / 精神或情緒相關 / 自體免疫疾病 / 其他 |
| 病史補充 | `condition_notes` | text | | 例：癌症種類、確診年份，500 字內 |
| 手術或住院紀錄 | `surgery_history` | list | | 每筆：`year`、`description`、`hospital_name` |
| 家族病史 | `family_history` | multi | | 高血壓 / 糖尿病 / 心臟病 / 中風 / 癌症 / 其他 / 不知道 |

「無」與「未填」必須分得開：`drug_allergies` 送 `[]` 且 `drug_allergies_none=true` 才算已確認無過敏。

### 步驟 5 — 目前用藥（串 Roadmap 第 4、5 項）

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 目前是否有長期用藥 | `has_current_medications` | bool | ✓ | |
| 用藥清單 | `current_medications` | list | 有用藥時 ✓ | 每筆：`drug_name`（自由輸入）、`dose`（例：1 顆）、`frequency`（每日 1 次 / 2 次 / 3 次 / 睡前 / 需要時 / 其他）、`purpose`（自由輸入，選填） |
| 藥袋照片 | `medication_bag_photos` | file（≤ 5 張） | | 先只存檔，OCR 待第 4 項定案；照片存物件儲存，DB 只留 URL |
| 是否使用中藥 / 保健食品 | `supplements` | text | | 用於交互作用提醒 |

### 步驟 6 — 就醫資訊與同意事項

| 欄位 | key | 型別 | 必填 | 驗證 / 說明 |
|---|---|---|---|---|
| 健保身份 | `nhi_status` | select：一般 / 無健保 / 不確定 | | |
| 重大傷病卡 | `catastrophic_illness_card` | bool | | |
| 身心障礙證明 | `disability_card` | bool | | |
| 需要無障礙協助 | `accessibility_needs` | multi：輪椅 / 視障 / 聽障 / 翻譯 / 其他 | | |
| 同意蒐集個資 | `consent_data_collection` | bool | ✓ 必須為 true | 顯示告知事項全文（個資法第 8 條） |
| 同意分享給合作院所 | `consent_share_with_hospitals` | bool | ✓ | false 時院所仍可為病人建檔，但只看得到病人主動出示的部分（見 §5.4） |
| 同意接收 LINE 推播 | `consent_push_notification` | bool | ✓ | false 時衛教每日任務與服藥提醒不推 |
| 電子簽名時間 | `consented_at` | 系統填 | | 一併記錄 IP、LINE user id、告知事項版本 `consent_version` |

## 3. B. 院方紀錄表（後台，院所人員填）

| 區塊 | 欄位 | 說明 |
|---|---|---|
| B1 建檔資訊 | `medical_record_no`（院內病歷號）、`hospital_id`、`created_by`、`source`（liff_form / counter / his_import）、`identity_verified_at`、`identity_verified_by`、`identity_method`（健保卡 / 身分證 / 現場 / 電話） | 建檔 = 為病人在本院建立 `patient_records` 一筆，並觸發 `binding_status → VERIFIED` |
| B2 自填資料審核 | 每個 A 步驟一組 `reviewed_by` / `reviewed_at` / `review_note`；`corrections`（院方修正的欄位與值） | 院方可改結構化表，但原始快照不動 |
| B3 就診 / 住院紀錄 `encounters` | `encounter_type`（門診 / 急診 / 住院）、`visit_date`、`department`、`attending_physician`、`diagnoses`（list：`icd10_code`、`description`）、`admitted_at` / `discharged_at`、`discharge_code`、`patient_visible_summary`（給病人看的白話摘要） | `discharge_code` 就是第 3 項衛教內容配對的鍵 |
| B4 風險與警示 `risk_flags` | `flag_type`（跌倒風險 / 過敏警示 / 感染管制 / 認知功能 / 自傷風險 / 其他）、`level`、`note`、`active` | 在客服與醫護畫面以醒目標籤顯示 |
| B5 追蹤事項 `follow_ups` | `due_date`、`kind`（回診 / 檢查 / 電訪 / 換藥）、`description`、`status`、`assignee_id` | 可選擇推播提醒病人（受 `consent_push_notification` 限制） |
| B6 內部備註 `notes` | `body`、`author_id`、`visibility`（staff_only / patient_visible） | 客服交接、醫護叮嚀 |
| B7 處方 / 衛教指派 | 引用第 4 項 `Prescription`、第 3 項 `PatientEducationAssignment` | 本文件不重複設計，只保留外鍵位置 |

## 4. 資料模型

所有表繼承 `db.Model, TimestampMixin`，主鍵 `String(32)` uuid hex，Enum 走
`sa.Enum(..., native_enum=False)`，JSON 欄位用 `JSONB().with_variant(JSON(), "sqlite")`，
與現有 `users` / `feedback` 一致。

### 4.1 病人側（跟著 user 走，跨院共用）

```text
patient_profiles            1:1 users            A 的純量欄位攤平成 column
  user_id PK/FK, filler_relation, filler_name, filler_relationship, filler_phone,
  full_name, national_id_last4, national_id_fingerprint, birth_date, sex,
  preferred_language, phone, email, address_city, address_district, address_street,
  height_cm, weight_kg, blood_type, rh, pregnancy_status, smoking, smoking_per_day,
  drinking, betel_nut, condition_notes, supplements,
  preferred_hospital_id, preferred_hospital_name, nhi_status,
  catastrophic_illness_card, disability_card, accessibility_needs JSONB,
  drug_allergies_none, food_allergies_none, chronic_conditions_none,
  completed_steps JSONB                          — 已完成的步驟編號，供草稿續填

patient_emergency_contacts  N:1 users            name, relationship, phone, is_primary
patient_allergies           N:1 users            kind(drug/food/other), substance, reaction, severity
patient_conditions          N:1 users            kind(chronic/surgery/family), code(選項值), description, year, hospital_name
patient_medications         N:1 users            drug_name, dose, frequency, purpose, source(self_reported/prescription), active
patient_media               N:1 users            kind(medication_bag), url, uploaded_at
patient_consents            N:1 users            consent_type, granted, consent_version, granted_at, ip, line_user_id
                                                  — 每次變更新增一列，不覆寫，方便舉證
patient_form_submissions    N:1 users            form_type(intake), answers JSONB(原始快照), submitted_at,
                                                  status(submitted/reviewed/rejected), reviewed_by, reviewed_at, review_note
```

過敏、病史、用藥拆成獨立表而不是 JSONB，是因為院方需要跨病人查詢
（例如「對 penicillin 過敏的病人」、「用藥清單 vs 新處方交互作用」）。緊急聯絡人同理。

### 4.2 院所側（跟著 hospital 走，院所隔離）

```text
patient_records             N:1 users, N:1 hospital     (user_id, hospital_id) UNIQUE
  medical_record_no, source, identity_verified_at, identity_verified_by, identity_method,
  status(active/inactive), created_by

patient_record_reviews      N:1 patient_records          step(1–6), reviewed_by, reviewed_at, review_note, corrections JSONB
patient_encounters          N:1 patient_records          encounter_type, visit_date, department, attending_physician,
                                                          diagnoses JSONB, admitted_at, discharged_at, discharge_code,
                                                          patient_visible_summary
patient_risk_flags          N:1 patient_records          flag_type, level, note, active
patient_follow_ups          N:1 patient_records          due_date, kind, description, status, assignee_id
patient_record_notes        N:1 patient_records          body, author_id, visibility
```

`hospital_id` 皆為指向 `hospitals.id` 的外鍵（`patient_records` 為 RESTRICT，其餘 SET NULL）。

### 4.3 對 `users` 的影響

不新增欄位。`binding_status` 由本流程驅動：

| 事件 | `binding_status` | `role` |
|---|---|---|
| 送出 intake（步驟 1–6 完成） | `UNBOUND → PENDING` | 不變 |
| 院方核對身份通過（建立 `patient_records`） | `PENDING → VERIFIED` | `GUEST → PATIENT` 或 `FAMILY`（依 `filler_relation`） |
| 院方退回 | `PENDING → UNBOUND` | 不變；推播告知原因；病人可再編輯並重新送出 |
| 送出後（`PENDING` / `VERIFIED`） | 不變 | 病人端唯讀，前端鎖表單、後端對存檔 / 送出回 403；修正由院方後台進行 |
| 院方鎖定 / 解鎖（後台按鈕，寫稽核） | `→ LOCKED` / `LOCKED → UNBOUND` | 鎖定中病人不能填、院方不能核對 |
| 同一身分證出現在第二個帳號 | 不變 | 只回 409 衝突、不自動鎖定（家屬與本人可能各自填），由院方核對時判斷 |

## 5. 服務與 API

### 5.1 病人端（LIFF）

頁面：`app/templates/liff/intake.html`（Bootstrap、單頁多步驟，`<script>` 內 `liff.init()` →
`POST /liff/auth` 換 JWT，之後每步存草稿）。

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/liff/intake` | 無（頁面本身） | render 表單頁；JS 再打 API 載入草稿 |
| GET | `/liff/intake/data` | `require_auth` + `INTAKE_WRITE_OWN` | 回目前 profile + `completed_steps`，供續填 |
| PUT | `/liff/intake/steps/<n>` | 同上 | 存單一步驟（部分驗證），寫入結構化表、更新 `completed_steps` |
| POST | `/liff/intake/submit` | 同上 | 全表驗證 → 寫 `patient_form_submissions` 快照與 `patient_consents` → `binding_status=PENDING` |
| GET | `/liff/profile` | `require_verified` + `PROFILE_READ_OWN` | 已存在的 stub，改回傳 profile（遮罩 `national_id`） |
| PUT | `/liff/profile` | `require_verified` + `PROFILE_WRITE_OWN` | 驗證後修改聯絡方式、用藥等非身份欄位；改身份欄位需重新審核 |

新增 `Permission.INTAKE_WRITE_OWN = "intake:write_own"`，放進 `_PUBLIC`，
因為 GUEST 必須能在驗證前填表；`PROFILE_*` 維持在 `_BOUND_USER`。

`IntakeService`（`app/services/intake_service.py`）：步驟驗證、身分證檢核與指紋計算、
「無」與「未填」判斷、送出時組快照並轉狀態；透過 `container.line_client.push()` 通知病人「已收到，待院方核對」。

### 5.2 院方端（後台）

REST 端點掛在既有 `admin_bp`（`/api/v1/admin/...`）：

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/admin/patients` | `PATIENT_READ` | 列表；`status=pending / active`、關鍵字（姓名、手機、病歷號）；依 `hospital_id` 隔離 |
| GET | `/admin/patients/<user_id>` | `PATIENT_READ` | 完整 A 資料 + 本院 B 紀錄 + 快照歷史；`national_id` 一律遮罩，沒有回明碼的端點 |
| POST | `/admin/patients/<user_id>/verify` | `PATIENT_VERIFY` | 核對身份 → 建 `patient_records` → `VERIFIED` + 指派角色 → 推播病人 |
| POST | `/admin/patients/<user_id>/reject` | `PATIENT_VERIFY` | 退回，帶原因 |
| PATCH | `/admin/patients/<user_id>/profile` | `PATIENT_WRITE` | 院方修正自填資料，寫 `patient_record_reviews.corrections` |
| POST / GET / PATCH | `/admin/patients/<user_id>/encounters[/<id>]` | `ENCOUNTER_MANAGE` | 就診紀錄與出院代碼 |
| POST / PATCH | `/admin/patients/<user_id>/flags[/<id>]` | `PATIENT_WRITE` | 風險警示 |
| POST / PATCH | `/admin/patients/<user_id>/follow-ups[/<id>]` | `PATIENT_WRITE` | 追蹤事項 |
| POST | `/admin/patients/<user_id>/notes` | `PATIENT_READ` | 內部備註（客服也可寫） |

新增權限與角色對照：

| Permission | HOSPITAL_STAFF（原 CS_AGENT） | MEDICAL_STAFF | HOSPITAL_ADMIN |
|---|---|---|---|
| `PATIENT_READ`（已存在） | ✓ | ✓ | ✓ |
| `PATIENT_WRITE` | ✓ | | ✓ |
| `PATIENT_VERIFY` | ✓ | | ✓ |
| `ENCOUNTER_MANAGE` | ✓ | | ✓ |

（2026-09-28 調整：管病人的是 HOSPITAL_STAFF，MEDICAL_STAFF 改管藥 / 衛教，病人資料唯讀。）

`PatientRecordService`：所有寫入先過 `AccessPolicy.can_access_hospital(principal, hospital_id)`；
`verify()` 內做「同身分證是否已被其他 LINE 帳號綁定」檢查，衝突 raise `ConflictError` 並轉 `LOCKED`。
`tests/test_rbac.py` 需補集合關係斷言（`CS_AGENT ⊉ PATIENT_WRITE` 等）。

後台頁面走專案既有模式：Flask + Jinja + Bootstrap，放 `app/templates/admin/patients/`
（列表、詳情兩頁），頁面內 `fetch()` 打上述 API。

### 5.3 後台介面：網址與登入

後台是獨立的 `admin_pages_bp`（`app/admin_pages.py`，`url_prefix="/admin"`），
Flask + Jinja + Bootstrap 5 render 頁面骨架，頁面內 JS 以 `fetch()` 打 §5.2 的 `/api/v1/admin/*`。
**目前這些頁面都不存在**，`/api/v1/admin/*` 只有 JSON API。

| 網址 | 頁面 | 主要操作 | 對應 API |
|---|---|---|---|
| `/admin` | 儀表板 | 待審核病人數、待處理回饋數、今日追蹤事項 | `GET /admin/patients?status=pending`、`GET /admin/feedback` |
| `/admin/login` | 登入 | 302 到 `/api/v1/auth/line/login?redirect_to=/admin` | 既有 LINE Login |
| `/admin/patients` | 病人列表 | 分頁「待審核 / 已建檔」、搜尋姓名 / 手機 / 病歷號 | `GET /admin/patients` |
| `/admin/patients/<user_id>` | 病人詳情 | 檢視 A 資料、核對通過 / 退回、修正、就診紀錄、風險警示、追蹤、備註 | `/admin/patients/<user_id>/*` |
| `/admin/feedback` | 意見回饋 | 查看、標記處理 | `GET /admin/feedback`、`PATCH .../{id}` |
| `/admin/staff` | 人員與角色 | 指派 HOSPITAL_STAFF / MEDICAL_STAFF / HOSPITAL_ADMIN | 既有 `PUT /admin/users/<id>/role` |
| `/admin/settings` | 院所設定 | 預留（Rich Menu、院所資訊） | 未來 |

**登入與權限**（解決 §9 第 3 題）：不另外做 cookie session。既有的 LINE Login callback
已經把 JWT 寫進 `sessionStorage` 並依 `redirect_to` 導回，後台頁面沿用這條路：

1. 頁面骨架不需驗證即可 render（骨架本身不含任何資料）。
2. 共用的 `admin/base.html` 內建 JS：讀 `sessionStorage.access_token`；沒有就導向 `/admin/login`；
   有就先打 `GET /api/v1/auth/me`，不是 staff 角色則顯示「無權限」頁並提供登出。
3. 所有資料請求帶 `Authorization: Bearer`，401（到期）就回登入頁重新登入。
4. 側欄選單依 `/api/v1/admin/permissions` 回傳的權限表顯示 / 隱藏（例如 CS_AGENT 看不到「人員與角色」）。
   **真正的授權仍由 API 端 decorator 決定**，前端隱藏只是體驗。

院所隔離同樣由 API 端 `AccessPolicy.can_access_hospital()` 處理，頁面不用自己判斷 `hospital_id`；
SUPER_ADMIN 在列表頁多一個「院所」篩選器。

`sessionStorage` 是分頁隔離的，開新分頁要重新登入；若院方覺得不便，第二階段再改成 HttpOnly cookie。

### 5.4 資料可見性規則

- 院所只看得到「在本院有 `patient_records`」的病人；列表的 `pending` 狀態需病人在步驟 6 選了
  該院為 `preferred_hospital`，或由病人在 LINE 上出示 QR / 病歷號讓櫃檯搜尋（待確認）。
- `consent_share_with_hospitals=false`：只有 `preferred_hospital` 看得到，其他院所建檔時需病人現場再次同意。
- B 區塊全部 staff only，`patient_visible_summary` 與 `visibility=patient_visible` 的備註例外，
  在 LIFF 設定頁顯示給病人。
- 病人自己可讀所有 A 資料，可下載自己的快照（個資法查閱權）。

## 6. 與既有架構整合點

- `Principal.is_verified` 不變，服務地圖「已驗證？」菱形直接吃本流程的結果。
- `intents/settings.py`：「檢視 / 修改基本資料」改為導向 `/liff/intake`（未驗證）或 `/liff/profile`（已驗證）的 LIFF URL。
- `intents/discharge.py` 個人化分支：查 `patient_encounters` 最新一筆的 `discharge_code`。
- `intents/medication.py` / `reminder.py`：`patient_medications` 是「病人自述」，
  第 4 項 `Prescription` 進來後以 `source` 區分，兩者並列顯示。
- `handoff_service`：轉真人時把 `patient_risk_flags` 帶進工單摘要。
- `follow.py` 封鎖 TODO：`consent_push_notification` 一併關閉。
- 綁定流程：本設計等同提出 B1（進表單）→ B6（送出）→ B7（院方核對）→ B9（完成通知）；
  若之後綁定流程改為「院方識別名單比對」自動驗證，`verify()` 改由比對服務呼叫即可，資料模型不用動。

## 7. 安全與個資

- `national_id` **不落 DB**：只存 `national_id_last4`（遮罩顯示）與 HMAC 指紋 `national_id_fingerprint`（金鑰 `PATIENT_DATA_KEY`，供重複檢查），系統沒有任何可還原明碼的資料；院方核對用末四碼 + 姓名 + 生日對照 HIS。
- 所有 `/admin/patients/*` 讀取寫稽核紀錄（`who / when / which user_id / which fields`），對應既有 `AUDIT_READ`。
- 表單輸入一律 server-side 驗證（長度、白名單 select 值、手機格式），自由文字輸出到 Jinja 依預設 autoescape。
- 藥袋照片：上傳先驗證 MIME 與大小（≤ 5 MB），儲存到物件儲存並產生短效簽名 URL，不存 DB。
- 資料保留：病人解除綁定時 A 資料軟刪除（保留快照 N 年，N 待法遵確認），B 紀錄屬院所依醫療法保存。

## 8. 實作順序（建議）

1. **模型與 migration**：§4 全部表 + 新 `Permission` + `ROLE_PERMISSIONS` + `test_rbac` 斷言。
2. **IntakeService + LIFF API**：草稿、送出、狀態轉換；單元測試走 SQLite。
3. **LIFF 表單頁**：`intake.html` 六步驟、前端即時驗證、續填。
4. **PatientRecordService + admin API**：列表、詳情、verify / reject、修正、encounters。
5. **後台頁面**：`/admin` 骨架與登入守衛 → `/admin/patients` 列表、詳情 → `/admin/handoffs`、`/admin/staff` 接既有 API。
6. **串接既有意圖**：settings / discharge / medication / handoff 的整合點。

## 9. 待確認問題

1. **院方核對身份的方式**：櫃檯人工核對（本設計預設）、健保卡讀卡、還是院方名單自動比對？影響 B1 與綁定流程。
2. **病人怎麼「指定」院所**：已定案。病人不指定；院方在後台用手機或姓名 + 末四碼找到病人「加入本院」，推播 LINE 請病人自己確認「有在這家就醫」，確認後才能核對建檔。只有新增的院所看得到。
3. **後台登入**：§5.3 預設沿用既有 `sessionStorage` + Bearer 流程；若院方需要多分頁 / 長時間登入，再改 HttpOnly cookie。
4. **家屬代填**：一個家屬可否代填多位病人？若可，`patient_profiles` 需改為 `FAMILY` 對多個 profile 的關聯表。
5. **診斷欄位**：是否要 ICD-10 代碼選單，還是先讓醫護自由輸入？
6. **未成年病人**：是否禁止本人填寫、強制家屬代填？
7. **資料保留年限**與 `consent_version` 告知事項全文由誰提供。
