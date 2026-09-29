# 資料庫模型說明

MediAgent+ 的所有資料表都是 `app/models/` 下的 SQLAlchemy 模型，正式環境跑 PostgreSQL（含 pgvector），
測試環境跑 in-memory SQLite。本文件逐表、逐欄位說明每個欄位在做什麼、值域是什麼、被哪個功能使用。
模型改了請一併更新這份文件。

## 共通慣例

| 慣例 | 說明 |
|---|---|
| 主鍵 | 除了 `hospitals.id`（人可讀院所代碼）、`pharmacies.id`（醫事機構代碼）、`drugs.nhi_code`（健保藥品代號）、`patient_profiles.user_id`（一人一列）之外，全部是 32 字元的 UUID hex 字串。 |
| `created_at` / `updated_at` | 所有表都繼承 `TimestampMixin`，UTC 帶時區；`updated_at` 在每次 UPDATE 自動更新。下文各表不再重複列出。 |
| 時間 | 所有 `DateTime` 欄位一律存 UTC；SQLite 讀回的 naive 值視為 UTC。日期（`Date`）欄位是台灣日曆日。 |
| Enum 欄位 | `Role` / `BindingStatus` 用 `sa.Enum(native_enum=False)` 存成 VARCHAR，不用 PostgreSQL 原生 enum type。**資料庫裡存的是列舉名稱（大寫，例 `GUEST`、`VERIFIED`）**，API 與本文件寫的是 `.value`（小寫）。其他狀態欄位是純字串 + 應用層白名單。 |
| JSON 欄位 | PostgreSQL 用 `JSONB`，SQLite 退回 `JSON`（`JSONB().with_variant(JSON(), "sqlite")`）。 |
| `hospital_id` 外鍵 | 指向 `hospitals.id`。`users` / `feedback` / `patient_profiles.preferred_hospital_id` / `audit_logs` / `knowledge_documents` 為 `SET NULL`；`patient_records.hospital_id` 為 `RESTRICT`（有病歷的院所不能刪）。 |
| `user_id` 外鍵 | 指向 `users.id`，幾乎都是 `CASCADE`（刪除使用者時連帶清掉自填資料、處方、提醒）。 |
| `created_by` / `reviewed_by` / `author_id` 等 | 存操作者的 `users.id`，CLI 操作時為 `"system"`；不設外鍵，避免人員離職刪帳號後紀錄失去意義。 |

## 資料表總覽

| 領域 | 資料表 | 一句話 |
|---|---|---|
| 身分與院所 | `users` | 每一位 LINE 使用者（含院方人員）一列：角色、綁定狀態、所屬院所 |
| | `hospitals` | 院所主檔，`id` 就是院所代碼 |
| | `audit_logs` | 高敏感操作稽核 |
| | `feedback` | 使用者意見回饋 |
| 病人自填資料（跟著 `users` 走，跨院所共用） | `patient_profiles` | LIFF 自填表的純量欄位攤平 |
| | `patient_emergency_contacts` | 緊急聯絡人 |
| | `patient_allergies` | 藥物 / 食物過敏 |
| | `patient_conditions` | 慢性病 / 手術史 / 家族史 |
| | `patient_medications` | 目前用藥 |
| | `patient_consents` | 同意紀錄（只新增不覆寫） |
| | `patient_form_submissions` | 每次送出的原始 JSON 快照 |
| 院方病人紀錄（跟著 `hospital_id` 走，院所互不可見） | `patient_records` | 某院所為某病人建的檔 |
| | `patient_record_reviews` | 院方對自填資料的審核 / 修正 |
| | `patient_encounters` | 就診 / 住院紀錄與出院代碼 |
| | `patient_risk_flags` | 風險警示 |
| | `patient_follow_ups` | 追蹤事項 |
| | `patient_record_notes` | 內部備註 |
| 藥物諮詢 / 服藥小幫手 | `prescriptions` | 處方箋（辨識結果 + 藥品清單 JSON） |
| | `pharmacies` | 藥局主檔（唯讀參考資料） |
| | `drugs` | 健保用藥品項（唯讀參考資料） |
| | `medication_schedules` | 每一項藥的提醒排程 |
| | `reminder_deliveries` | 每一次提醒推播與回報 |
| 急診分流（找醫院） | `er_snapshots` | 健保署急診即時資料的時序快照，依使用者位置推薦醫院用 |
| AI 知識庫 | `knowledge_documents` | RAG 文件 |
| | `knowledge_chunks` | 文件切段後的檢索片段（tsvector + 向量） |
| | `knowledge_jobs` | 後台背景維護工作（衛教 txt 匯入、重建片段、算向量） |

---

## 身分與院所

### `users` — 使用者（`app/models/user.py`）

每一位接觸過官方帳號的人一列：加好友、第一次打 webhook 或 LINE Login 時就建立（GUEST）。院方人員也是 LINE 使用者，只是角色不同。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | UUID hex。其他表的 `user_id` / `actor_id` / `created_by` 都存這個。 |
| `line_user_id` | String(64) unique | LINE 的 `userId`（`U` 開頭）。推播、對話狀態 key 都用它。理論上不會空。 |
| `display_name` | String(255) | LINE 顯示名稱，登入 / 加好友時同步。 |
| `picture_url` | String(512) | LINE 大頭貼網址。 |
| `role` | Enum VARCHAR(32) | 存 enum **名稱**（`GUEST` / `PATIENT` / `FAMILY` / `HOSPITAL_STAFF` / `MEDICAL_STAFF` / `HOSPITAL_ADMIN` / `SUPER_ADMIN`）。權限由 `app/domain/rbac.py` 的 `ROLE_PERMISSIONS` 決定。刻意不加 CHECK constraint，新增角色只要改 Python enum。 |
| `binding_status` | Enum VARCHAR(16) | 服務地圖上「基本資料已填且驗證通過？」：`unbound`（未填 / 被退回）→ `pending`（已送出待院方核對）→ `verified`（院方核對通過）；`locked` 為異常鎖定。有 CHECK constraint。 |
| `hospital_id` | String(32) FK → hospitals (SET NULL) | 院方人員所屬院所；一般使用者為 NULL。院所隔離（`AccessPolicy.hospital_scope()`）以此為準。 |
| `push_enabled` | Boolean, default true | 使用者封鎖官方帳號（unfollow）時設為 false，所有主動推播（服藥提醒、分流建議）略過；重新加好友時恢復 true。 |

### `hospitals` — 院所主檔（`app/models/hospital.py`）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | 人可讀院所代碼（例：`H1`、健保醫事機構代碼 `0401180014`）。與健保署急診 API 的 `hosP_ID` 相同時，急診看板可帶出地址 / 電話。 |
| `name` | String(100) | 院所名稱。 |
| `region` | String(50) | 縣市（臺 → 台 正規化），找醫院 / 分流依縣市篩選用。 |
| `address` | String(200) | 地址；找附近診所時會解析鄉鎮市區。 |
| `contact_phone` | String(30) | 聯絡電話，Flex 卡片上的「撥打電話」。 |
| `capacity_tier` | String(16) | 層級：`medical_center` / `regional` / `district` / `clinic`（匯入時由健保署「醫事機構種類」對應；沒有該欄位時名稱含「診所」才視為 clinic）。找附近診所只取 clinic / district / regional。 |
| `departments` | Text | 診療科別，逗號分隔（衛福部「醫療機構與人員基本資料」的「科別」用詞，例 `家庭醫學科,內科`）。找附近診所依 AI 建議的科別篩選（`triage_rules.department_terms()` 對應同義詞）。migration `0002_hospital_departments`。 |
| `lat` / `lng` | Float | 座標，找醫院依距離排序用；找附近診所以鄉鎮市區為主、有座標才算距離（診所大多沒有座標）。來源 `data/er_hospitals.csv` 或後台手填。 |
| `active` | Boolean, default true | 停用的院所不出現在下拉選單、不能被寫入（`HospitalService.require_active()`）。只有 SUPER_ADMIN 能改。 |
| `created_by` | String(32) | 建立者 `users.id` 或 `system`。 |

### `audit_logs` — 稽核紀錄（`app/models/audit.py`）

只記高敏感操作，由 `container.audit_repo.record()` 寫入，後台「稽核紀錄」頁依院所查看。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `actor_id` | String(32) index | 操作者 `users.id`。 |
| `action` | String(48) index | 動作代碼：`patient.lock` / `patient.unlock`（鎖定 / 解鎖帳號）。新增動作請在此補上。 |
| `target_user_id` | String(32) index | 被操作的病人 `users.id`。 |
| `hospital_id` | String(32) FK (SET NULL) | 操作者當時的院所，後台依此隔離。 |
| `ip_address` | String(45) | 請求來源 IP。 |
| `detail` | JSON | 補充資訊（例：鎖定原因）。不放個資明碼。 |

### `feedback` — 使用者意見回饋（`app/models/feedback.py`）

沒有真人客服（2026-09-28 移除 `handoff_tickets`）。Rich Menu「意見回饋」、各功能的 Quick Reply、AI 判斷 `event=feedback`
都只是給 LIFF `/liff/feedback` 的連結；任何登入的 LINE 使用者都能送，不需填過病人表單。後台「意見回饋」頁依院所查看並標記處理。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | String(32) FK → users (SET NULL) | 送出的使用者；帳號刪除後保留回饋。 |
| `display_name` | String(255) | 送出當下的 LINE 名稱快照。 |
| `hospital_id` | String(32) FK (SET NULL) | 病人已確認的就醫院所或院方帳號的院所；NULL 只有 SUPER_ADMIN 看得到。 |
| `category` | String(32) | `bug` / `content` / `suggestion` / `other`（`FeedbackService.CATEGORIES`）。 |
| `content` | Text | 5–2000 字。 |
| `contact` | String(120) | 選填聯絡方式（電話 / Email）。 |
| `status` | String(16) | `open`（待處理）/ `resolved`（已處理），有索引。 |
| `resolved_by` / `resolved_at` / `resolver_note` | | 標記處理的院方帳號、時間、備註。 |

---

## 病人自填資料（Intake）

LIFF `/liff/intake` 六步驟表單的落點。欄位定義單一來源是 `app/domain/intake_schema.py` 的 `INTAKE_STEPS`，
每個 `Field.storage` 決定落到哪張表：`profile`（純量欄位）、`json`（多選存 JSON）、`contacts`、`allergy:kind`、
`condition:kind`、`medication`、`consent`。`SqlAlchemyPatientRepository` 負責 dict ↔ 多表轉換。
這組資料**跟著 `users` 走、跨院所共用一份**。

### `patient_profiles` — 基本資料（`app/models/patient.py`）

一人一列，`user_id` 就是主鍵。

| 欄位 | 型別 | 表單步驟 | 說明 |
|---|---|---|---|
| `user_id` | String(32) PK, FK → users (CASCADE) | | |
| `filler_relation` | String(16) | 1 填表人 | `self`（病人本人）/ `family`（家屬代填）。 |
| `filler_name` / `filler_relationship` / `filler_phone` | String | 1 | 代填時的家屬姓名、關係（`RELATIONSHIPS` 選項）、電話；本人填表時為 NULL。 |
| `full_name` | String(50) | 2 病人身分 | 病人姓名。 |
| `national_id_last4` | String(4) | 2 | 末四碼，遮罩顯示 `******1234` 用。**系統不保存身分證明碼或密文**，這裡與指紋是唯一落地的形式。 |
| `national_id_fingerprint` | String(64) unique | 2 | HMAC 指紋（`FieldCipher.fingerprint()`，金鑰 `PATIENT_DATA_KEY`），供重複檢查：同一指紋出現在第二個帳號 → 送出時回 409，由院方核對時判斷是否冒用。 |
| `birth_date` | Date | 2 | 出生日期。 |
| `sex` | String(8) | 2 | `male` / `female` / `other`。 |
| `preferred_language` | String(16) | 2 | 慣用語言選項值（國語 / 台語 / 客語 / 英語…）。 |
| `phone` | String(20) index | 2 | 手機，院方搜尋病人用。 |
| `email` | String(255) | 2 | |
| `address_city` / `address_district` / `address_street` | String | 2 | 住址三段。找藥局 / 找醫院可直接用住址（不必分享位置）。 |
| `height_cm` / `weight_kg` | Numeric(5,1) | 3 健康狀況 | 身高體重。 |
| `blood_type` | String(8) | 3 | `A` / `B` / `AB` / `O` / `unknown`。 |
| `rh` | String(8) | 3 | Rh 陽性 / 陰性 / 不知道。 |
| `pregnancy_status` | String(16) | 3 | 懷孕 / 哺乳狀態選項值。 |
| `smoking` | String(8) | 3 | `never` / `former` / `current`。 |
| `smoking_per_day` | Integer | 3 | 每日支數（`smoking=current` 時）。 |
| `drinking` | String(16) | 3 | `never` / `occasional` / `frequent`。 |
| `betel_nut` | String(8) | 3 | `never` / `former` / `current`。 |
| `drug_allergies_none` / `food_allergies_none` / `chronic_conditions_none` | Boolean | 4 過敏與病史 | 「無」勾選框。為 true 時對應的明細表應為空；用來區分「沒填」與「填了沒有」。 |
| `other_allergies` | JSON list | 4 | 其他過敏多選：`contrast`（顯影劑）/ `latex` / `anesthesia` / `other`。 |
| `condition_notes` | String(500) | 4 | 病史補充說明。 |
| `has_current_medications` | String(8) | 5 目前用藥 | `yes` / `no`，決定 `patient_medications` 是否要填。 |
| `supplements` | String(300) | 5 | 保健食品 / 中藥自由文字。 |
| `preferred_hospital_id` | String(32) FK → hospitals (SET NULL) | 非表單欄位 | 「新增」這位病人的院所。**表單不問、沒有 QR 連結**：院方在後台用手機或姓名 + 末四碼精準找到病人按「加入本院」時寫入（`PatientRecordService.request_hospital_confirmation()`），病人在 LINE 上按「不是」時清空。**只有這家院所看得到這位待核對病人**，其他院所看不到也不能動；NULL = 還沒被任何院所新增，只能被精準查到。 |
| `hospital_confirmed_at` | DateTime | 非表單欄位 | 病人**自己**在 LINE 上按下「是，我在這裡就醫」的時間。NULL = 尚未確認，`verify()` 會拒絕建檔。API 以 `hospital_confirmed` 布林呈現。 |
| `nhi_status` | String(16) | 6 | `insured` / `uninsured` / `unknown`。 |
| `catastrophic_illness_card` / `disability_card` | Boolean | 6 | 重大傷病卡 / 身心障礙證明。 |
| `accessibility_needs` | JSON list | 6 | 無障礙需求多選：`wheelchair` / `visual` / `hearing` / `interpreter` / `other`。 |
| `completed_steps` | JSON list | 進度 | 已存過的步驟編號（含草稿），LIFF 續填與進度條用。 |
| `submitted_at` | DateTime | 狀態 | 最近一次送出時間；NULL 代表草稿。院方退回時清空，讓病人能重新送出。 |

### `patient_emergency_contacts` — 緊急聯絡人（步驟 2）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `name` | String(50) | 姓名。 |
| `relationship` | String(16) | 關係（`RELATIONSHIPS` 選項值：配偶 / 子女 / 父母…）。 |
| `phone` | String(20) | 電話。 |
| `is_primary` | Boolean | 第一順位聯絡人。 |
| `position` | Integer | 表單內順序（每次儲存整批重寫）。 |

### `patient_allergies` — 過敏（步驟 4）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `kind` | String(8) | `drug`（藥物）/ `food`（食物），對應 schema 的 `allergy:drug` / `allergy:food`。 |
| `substance` | String(100) index | 過敏原名稱；index 讓院方能跨病人查「對 penicillin 過敏的人」。 |
| `reaction` | String(16) | 反應選項值（`REACTIONS`：皮疹 / 呼吸困難 / 過敏性休克…）。 |
| `severity` | String(8) | `SEVERITY` 選項值（輕 / 中 / 重）。 |
| `position` | Integer | 表單內順序。 |

### `patient_conditions` — 病史（步驟 4）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `kind` | String(8) | `chronic`（慢性病多選）/ `surgery`（手術史）/ `family`（家族史多選）。 |
| `code` | String(32) index | 多選的選項值（`CHRONIC_CONDITIONS` / `FAMILY_HISTORY`，例 `diabetes`、`hypertension`）。AI 個人化衛教會讀慢性病代碼。 |
| `description` | String(200) | 手術名稱或「其他」的說明。 |
| `year` | Integer | 手術年份。 |
| `hospital_name` | String(100) | 手術醫院（自由文字，不連 `hospitals`）。 |
| `position` | Integer | 表單內順序。 |

### `patient_medications` — 目前用藥（步驟 5）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `drug_name` | String(100) index | 藥名（自由文字）。 |
| `dose` | String(50) | 劑量文字。 |
| `frequency` | String(8) | 頻率選項值（`MED_FREQUENCY`：qd / bid / tid…）。 |
| `purpose` | String(100) | 用途。 |
| `source` | String(16) | `self_reported`（病人自填，目前唯一值）；保留 `prescription` 給之後由處方同步。 |
| `active` | Boolean | 是否仍在服用。 |
| `position` | Integer | 表單內順序。 |

### `patient_consents` — 同意紀錄（步驟 6）

每次變更新增一列、不覆寫，方便舉證。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `consent_type` | String(32) | `consent_data_collection`（個資蒐集，必須為 true 才能送出）/ `consent_share_with_hospitals`（提供給常去院所）/ `consent_push_notification`（接受推播）。 |
| `granted` | Boolean | 同意 / 不同意。 |
| `consent_version` | String(16) | 告知事項文案版本（`CONSENT_VERSION`，例 `2026-09-01`），文案更新時要一併改。 |
| `ip_address` | String(45) | 送出時 IP。 |
| `line_user_id` | String(64) | 送出時的 LINE userId（即使之後 `users` 列被改也能對回）。 |

### `patient_form_submissions` — 送出快照

每次按「送出」存一份完整答案 JSON。院方修正只改結構化表並寫 `patient_record_reviews.corrections`，**不改這裡**。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `form_type` | String(16) | 目前固定 `intake`。 |
| `answers` | JSON | 送出時所有欄位的原始值。身分證明碼不會出現在這裡：`IntakeService` 在存步驟時就把明碼換成 NULL、只留加密欄位與末四碼。 |
| `status` | String(16) | `submitted`（待核對）→ `reviewed`（院方 verify 通過）或 `rejected`（退回）。 |
| `reviewed_by` | String(32) | 核對者 `users.id`。 |
| `reviewed_at` | DateTime | 核對時間。 |
| `review_note` | Text | 核對備註或退回原因（退回原因會推播給病人）。 |

---

## 院方病人紀錄

`patient_records` 是「某院所為某病人建檔」的那一筆，其餘五張表都掛在它底下（`record_id` CASCADE）。
**跟著 `hospital_id` 走**：院所只看得到自己的紀錄，查詢一律經 `AccessPolicy.hospital_scope()`。

### `patient_records` — 建檔（`app/models/patient_record.py`）

`(user_id, hospital_id)` 唯一：同一病人在同一院所只有一份檔。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | 病人。 |
| `hospital_id` | FK → hospitals (RESTRICT) | 建檔院所。 |
| `medical_record_no` | String(32) index | 院內病歷號，核對時由院方填。 |
| `source` | String(16) | 建檔來源，目前固定 `liff_form`（由自填表核對而來）。 |
| `status` | String(16) | 檔案狀態，目前固定 `active`；保留給日後停用 / 合併。 |
| `identity_method` | String(16) | 身份核對方式：`nhi_card`（健保卡）/ `id_card`（身分證）/ `in_person`（現場）/ `phone`（電話）。 |
| `identity_verified_at` | DateTime | 核對通過時間；`PatientRecordService.verify()` 同時把 `users.binding_status` 改為 `verified`、角色改為 `patient` / `family`。 |
| `identity_verified_by` | String(32) | 核對者。 |
| `created_by` | String(32) | 建檔者。 |

### `patient_record_reviews` — 審核 / 修正

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `record_id` | FK → patient_records (CASCADE) | |
| `reviewed_by` | String(32) | 操作者。 |
| `review_note` | Text | 備註。 |
| `corrections` | JSON | 院方修正了自填資料的哪些欄位：`{欄位: {"from": 舊值, "to": 新值}}`。 |

### `patient_encounters` — 就診 / 住院紀錄

AI 個人化衛教（「我剛出院」）會讀最近一筆的科別、診斷、出院代碼。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `record_id` | FK → patient_records (CASCADE) | |
| `encounter_type` | String(16) | `opd`（門診）/ `er`（急診）/ `inpatient`（住院）。 |
| `visit_date` | Date | 就診 / 入院日。 |
| `department` | String(50) | 科別。 |
| `attending_physician` | String(50) | 主治醫師。 |
| `diagnoses` | JSON list | `[{"icd10_code": "...", "description": "..."}]`。 |
| `admitted_at` / `discharged_at` | Date | 住院起訖。 |
| `discharge_code` | String(32) index | 出院代碼（院所自訂），個人化衛教的配對鍵。 |
| `patient_visible_summary` | Text | 病人可見的就診摘要。 |
| `created_by` | String(32) | |

### `patient_risk_flags` — 風險警示

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `record_id` | FK → patient_records (CASCADE) | |
| `flag_type` | String(32) | `fall`（跌倒）/ `allergy` / `infection`（感染管制）/ `cognition`（認知）/ `self_harm` / `other`。 |
| `level` | String(8) | `low` / `medium` / `high`。 |
| `note` | String(500) | 說明。 |
| `active` | Boolean | 解除時設 false，不刪。 |
| `created_by` | String(32) | |

### `patient_follow_ups` — 追蹤事項

後台儀表板的「到期追蹤」就是查 `due_date <= 今天 且 status=open`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `record_id` | FK → patient_records (CASCADE) | |
| `due_date` | Date index | 到期日（台灣日）。 |
| `kind` | String(16) | `revisit`（回診）/ `exam`（檢查）/ `call`（電訪）/ `dressing`（換藥）/ `other`。 |
| `description` | String(500) | |
| `status` | String(16) | `open` / `done` / `cancelled`。 |
| `assignee_id` | String(32) | 負責人員。 |
| `created_by` | String(32) | |

### `patient_record_notes` — 內部備註

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `record_id` | FK → patient_records (CASCADE) | |
| `body` | Text | 內容。 |
| `visibility` | String(16) | `staff_only`（僅院方）/ `patient_visible`（病人可見）。 |
| `author_id` | String(32) | 作者。 |

---

## 藥物諮詢 / 服藥小幫手

設計文件 `docs/medication-design.md`。處方的藥品清單存 JSON 欄位、照片路徑直接掛在處方上。

### `prescriptions` — 處方箋（`app/models/medication.py`）

Rich Menu「去哪拿藥」拍照 → `PrescriptionService.create_from_image()` 辨識後建立。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | 上傳者。 |
| `status` | String(16) | `draft`（剛辨識）→ `confirmed`（使用者確認）；`discarded` 為刪除（軟刪）。 |
| `source` | String(16) | `ocr`（拍照辨識）/ `manual`（保留給手動輸入）。 |
| `hospital_name` | String(100) | 辨識出的開立院所名稱（自由文字，不連 `hospitals`）。 |
| `issued_on` | Date | 開立日期。 |
| `days_supply` | Integer | 給藥天數。 |
| `is_chronic_refill` | Boolean | 是否慢性病連續處方箋。 |
| `refill_seq` / `refill_total` | Integer | 慢箋第幾次 / 共幾次。 |
| `refill_reminder_on` | Date | 慢箋下次領藥提醒日：確認時算 `issued_on + days_supply × refill_seq − REFILL_REMINDER_DAYS_BEFORE`。 |
| `refill_reminded_at` | DateTime | 已推播過領藥提醒的時間，避免重送。 |
| `items` | JSON list | 藥品清單，每項：`drug_name`（比對到健保藥品後的英文名）、`drug_name_raw`（辨識原文）、`dose_text`、`frequency_code`、`timing`、`duration_days`、`quantity`、`instructions`、`nhi_code`、`category`（ATC 大類中文）、`confidence`。 |
| `warnings` | JSON list | 辨識時的提醒文字（例：字跡不清、可能含管制藥）。 |
| `image_path` | String(255) | 加密後的原始照片檔路徑（`MEDIA_ROOT/<user_id>/<id>.bin`）。API 不外露。 |
| `image_content_type` | String(64) | 照片 MIME。 |
| `line_message_id` | String(64) | 來源 LINE 圖片訊息 id。 |
| `ocr_model` | String(64) | 辨識用的模型名稱（`OCR_MODEL`）。 |
| `ocr_raw` | JSON | 模型原始輸出，除錯用，API 不外露。 |
| `confirmed_at` | DateTime | 確認時間。 |

### `pharmacies` — 藥局主檔

由 SUPER_ADMIN 在後台上傳 CSV 匯入（健保署「特約醫事機構」只取藥局列 + 衛福部「藥局基本資料」補非特約），upsert 可重跑。沒有經緯度，用縣市 / 鄉鎮市區配對。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(24) PK | 醫事機構代碼；衛福部來源沒有代碼時用衍生代碼。 |
| `name` | String(100) | |
| `phone` | String(30) | |
| `address` | String(200) | |
| `city` | String(10) index | 縣市（臺 → 台 正規化）。 |
| `district` | String(10) index | 鄉鎮市區。 |
| `nhi_contracted` | Boolean | 是否健保特約（慢箋只能在特約藥局領）。 |
| `opening_hours` | JSON | `{"mon": ["am", "pm", "night"], ...}`，由「固定看診時段」文字解析；「現在營業中」依此與 `now_tw()` 判斷。 |
| `active` | Boolean | 歇業設 false。 |

### `drugs` — 健保用藥品項

健保署「健保用藥品項」CSV 匯入，供處方辨識結果比對藥名、藥名查詢、顯示 ATC 類別。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `nhi_code` | String(12) PK | 健保藥品代號。 |
| `name_en` | String(150) index | 英文品名。 |
| `name_zh` | String(150) index | 中文品名。 |
| `ingredient` | String(300) | 成分。 |
| `dosage_form` | String(50) | 劑型。 |
| `atc_code` | String(10) index | ATC 代碼，第一碼對應 `ATC_CATEGORIES` 中文大類。 |

### `medication_schedules` — 提醒排程

一項藥一列。規則單一來源 `app/domain/reminder_rules.py`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `user_id` | FK → users (CASCADE) | |
| `prescription_id` | FK → prescriptions (SET NULL) | 來源處方；手動建立時 NULL。處方被 `discarded` 時排程自動略過。 |
| `drug_name` | String(150) | 藥名。 |
| `dose_text` | String(50) | 每次劑量文字（例 `1 顆`）。 |
| `frequency_code` | String(8) | `qd` / `bid` / `tid` / `qid` / `q12h` / `q8h` / `q6h` / `qw` / `hs` / `prn` / `other`。 |
| `timing` | String(8) | `ac`（飯前）/ `pc`（飯後）/ `hs`（睡前）/ `stat`（立即）/ NULL。 |
| `times` | JSON list | 台灣時間 `HH:MM` 清單，由頻率 × 飯前後算出預設值，使用者可改。 |
| `start_date` / `end_date` | Date | 提醒起訖（台灣日）；`end_date` NULL 為持續。 |
| `active` | Boolean | 使用者刪除 / 停用。 |
| `paused_until` | Date | 暫停到某日（含）。 |

### `reminder_deliveries` — 推播紀錄

`(schedule_id, scheduled_for)` 唯一，保證排程重跑同一時段只送一次。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `schedule_id` | FK → medication_schedules (CASCADE) | |
| `scheduled_for` | DateTime | 這一次提醒的預定時間（UTC）。 |
| `status` | String(16) | `pending` → `sent`（已推播）/ `failed`（推播失敗）/ `skipped`（使用者封鎖或按「跳過」）；使用者回報後 `taken`（已服用）/ `snoozed`（30 分後再提醒）。 |
| `sent_at` | DateTime | 推播時間。 |
| `snooze_until` | DateTime | 延後到的時間，`run_due` 到時再送一次。 |
| `responded_at` | DateTime | 使用者按 postback 的時間。 |

---

## 急診分流（找醫院）

設計文件 `docs/triage-design.md`。資料來源是健保署急診即時 API（59 家重度級急救責任醫院），`app/scheduler.py` 每 15 分鐘抓一次；只用來依使用者位置推薦醫院，沒有分流建議與接收者。規則在 `app/domain/triage_rules.py`。

### `er_snapshots` — 急診即時快照（`app/models/triage.py`）

`(nhi_hospital_id, reported_at)` 唯一，重跑不重複；超過 `TRIAGE_RETENTION_DAYS` 的舊資料由排程清除。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `nhi_hospital_id` | String(16) index | 醫事機構代碼（API 的 `hosP_ID`）；與 `hospitals.id` 相同時看板帶出地址 / 電話 / 座標。 |
| `hospital_name` | String(100) | |
| `area_code` | String(4) | 健保署地區代碼（對照 `AREA_NAMES`，例 `01` 台北市）。 |
| `hosp_type` | String(2) | `1` 醫學中心 / `2` 區域醫院 / `3` 地區醫院；等住院門檻依層級不同。 |
| `full_119` | Boolean | 119 滿床通報，一律視為「壅塞」。 |
| `wait_see` | Integer | 等看診人數。 |
| `wait_bed` | Integer | 推床數。**不列入壅塞判斷**（資料不可靠），僅保留。 |
| `wait_admit` | Integer | 等住院人數（主要判斷指標）。 |
| `wait_icu` | Integer | 等加護病房人數。 |
| `reported_at` | DateTime index | 醫院回報時間；停止回報超過 2 × `TRIAGE_STALE_MINUTES` 的不當現況。 |
| `fetched_at` | DateTime | 我方抓取時間。 |

---

## AI 知識庫（RAG）

設計文件 `docs/ai-assistant-design.md`。`hospital_id` 空 = 通用；院所文件只有常去院所 / 所屬院所是它的使用者檢索得到。

### `knowledge_documents` — 文件（`app/models/knowledge.py`）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `title` | String(200) | 標題；檢索結果的「參考來源」按鈕顯示《標題》。 |
| `category` | String(32) | 分類代碼，見 `KnowledgeService.CATEGORIES`：`service`（服務說明）/ `faq`（常見問題）/ `symptom`（症狀與就醫）/ `medication`（用藥）/ `education`（衛教）/ `hospital`（院所資訊）。 |
| `hospital_id` | FK → hospitals (SET NULL) | NULL = 通用（只有 SUPER_ADMIN 能改）；有值 = 該院所專屬。 |
| `content` | Text | 全文（Markdown 或純文字），最長 `MAX_CONTENT_CHARS`。 |
| `department` | String(50) index | 科別（衛教 txt 檔名「01_心臟內科」的科別；未分類 / 手動建立的為 NULL）。檢索時同科別片段加權。 |
| `tags` | JSON list | 標籤；衛教匯入時 = [科別]。 |
| `source` | String(500) index | 來源：Markdown 檔名或原文網址。匯入時以此去重（同檔名 / 同網址都更新同一份）。 |
| `content_hash` | String(40) index | 內文 SHA-1，跨醫院轉載的相同內容只收一份。 |
| `meta` | JSON | 出處資訊（衛教匯入時填）：`hospital_name`、`level`（醫學中心 / 區域 / 地區）、`department`、`source_category`（來源站台分類）、`imported_from`（txt 檔名）。 |
| `active` | Boolean | 停用後不參與檢索。 |
| `created_by` | String(32) | 建立者或 `system`。 |

### `knowledge_chunks` — 檢索片段

文件新增 / 修改時由 `chunk_document()`（結構感知 recursive chunking，目標 400 字、硬上限 600、句界收尾、重疊一句）重建
（`replace_chunks`），刪文件 CASCADE；後台「重建片段」用目前規則重切全部（向量清空）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `document_id` | FK → knowledge_documents (CASCADE) | |
| `seq` | Integer | 片段在文件內的順序。 |
| `heading` | String(200) | 片段所在的標題路徑「章 › 節」，顯示用；內容已含同樣的前綴。 |
| `kind` | String(8) | `text` / `list`（條列）/ `table`（表格攤平成「｜」分隔）/ `qa`（問答）。 |
| `content` | Text | 片段文字（含「標題路徑：」前綴），就是餵給模型的 K1…Kn。 |
| `tokens` | JSON list | jieba 搜尋模式 + 字元 bigram 的斷詞結果；SQLite 測試環境的記憶體 BM25 用。 |
| `search_vector` | TSVECTOR（SQLite 為 Text） | 同一組 tokens 的 tsvector，PostgreSQL 全文檢索，GIN 索引。 |
| `embedding` | vector(1536)（SQLite 為 JSON） | OpenAI `text-embedding-3-small` 向量，pgvector HNSW 索引；NULL 代表還沒算（`knowledge embed` / 後台「計算向量」只算 NULL 的）。SQLite 用 `JSON(none_as_null=True)` 讓 NULL 真的是 NULL。 |

### `knowledge_jobs` — 後台維護工作

後台 `/admin/knowledge`「系統資料匯入」（只有 SUPER_ADMIN）啟動的背景工作，由 `KnowledgeJobService` 管理；同一時間只允許一個 `queued` / `running`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | String(32) PK | |
| `kind` | String(32) | `department_txt`（衛教 txt 匯入）/ `rechunk`（重建片段）/ `embed`（計算向量）。 |
| `status` | String(16) | `queued` → `running` → `succeeded` / `failed`。超過 30 分鐘沒更新的 running 工作會被標成 failed（程序中斷）。 |
| `params` | JSON | 啟動參數：匯入 = `filename`、`min_chars`、`include_all`、`limit`、`path`（伺服器暫存路徑，API 不外露）；向量 = `batch`、`pending`（啟動時待算數）。 |
| `progress` | JSON | 執行中的計數：匯入 = `{"created", "read"}`（每 500 篇更新）；向量 = `{"done", "total"}`（每批更新）。頁面每 3 秒輪詢。 |
| `result` | JSON | 完成摘要：匯入 = `{"created", "skipped", "duplicates", "read"}`；向量 = `{"embedded"}`。 |
| `error` | Text | 失敗原因。 |
| `created_by` | String(32) | 啟動者。 |
| `started_at` / `finished_at` | DateTime | 實際開始 / 結束時間（`created_at` 是排入時間）。 |

---

## 關聯總圖

```
hospitals ─┬─< users ─────────┬─< patient_profiles (1:1) ─┬─< patient_emergency_contacts
           │                  │                           ├─< patient_allergies
           │                  │                           ├─< patient_conditions
           │                  │                           ├─< patient_medications
           │                  │                           ├─< patient_consents
           │                  │                           └─< patient_form_submissions
           │                  ├─< feedback
           │                  ├─< prescriptions ──< medication_schedules ──< reminder_deliveries
           │                  └─  audit_logs（actor_id / target_user_id 不設 FK）
           ├─< patient_records ─┬─< patient_record_reviews
           │   (user_id × hospital_id 唯一)
           │                    ├─< patient_encounters
           │                    ├─< patient_risk_flags
           │                    ├─< patient_follow_ups
           │                    └─< patient_record_notes
           ├─< knowledge_documents ──< knowledge_chunks
           └─< audit_logs

獨立參考資料：pharmacies、drugs、er_snapshots、knowledge_jobs
```

## Migration

歷次 migration 已於 2026-09-28 統整為**單一版本** `migrations/versions/0001_initial.py`（revision `0001_initial`），
內容由 `app/models/` 自動產生並人工補上 `CREATE EXTENSION IF NOT EXISTS vector`。

- 全新資料庫：`flask --app wsgi db upgrade` 一次建齊。
- 統整前已存在的資料庫（版本指標指向舊 revision）：`flask --app wsgi db stamp --purge 0001_initial`，
  再用 `flask --app wsgi db check` 確認與模型無差異。
- 之後改模型：`flask --app wsgi db migrate -m "..."` 產生新的 migration 疊在 `0001_initial` 之後。
  目前：`0002_hospital_departments`（院所科別）、`0003_knowledge_department`
  （`knowledge_documents.department`、`knowledge_chunks.heading / kind`）。
