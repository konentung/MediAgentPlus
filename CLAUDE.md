# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概要

MediAgent+ 醫療照護 LINE 機器人後端。Python 3.11、Flask 3、line-bot-sdk 3.25.0（`linebot.v3`）、PostgreSQL（psycopg 3）。沒有獨立前端專案（no SPA），需要網頁畫面的地方（目前是 LINE Login callback）直接用 Flask 內建的 Jinja2 render `app/templates/` 下的模板；LIFF 頁面之後也會走同一套模式。
業務流程依據「服務地圖」設計：加好友 → Rich Menu 六格 → 依「基本資料已填且驗證通過？」分流。「基本資料」就是 LIFF 病人自填表（`/liff/intake`），送出後由院方在後台（`/admin`）核對身份、建檔，`binding_status` 才變成 `VERIFIED`。對照表在 `docs/architecture.md`，表單與後台設計在 `docs/patient-form-design.md`。

## 常用指令

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                   # 全部測試，不需要資料庫
pytest tests/test_webhook.py -v          # 單一檔案
pytest tests/test_rbac.py::test_hospital_scope   # 單一測試
pytest tests/test_intake.py tests/test_patient_admin.py   # 病人表單 + 院方後台
pytest tests/test_triage.py              # 急診動態分流
pytest tests/test_assistant.py           # AI 對話助理（RAG + 事件判斷）
pytest tests/test_knowledge_pdf.py       # 衛教資訊：PDF 匯入、重切、各院所統計
pytest -p no:warnings                    # 隱藏 PyJWT 短金鑰警告（測試用金鑰刻意很短）
FREEZE_AT="2030-01-01 08:00:00" pytest   # 把「現在」凍結在未來（UTC），檢查測試會不會因日期過期而失敗

ruff check . && ruff format .            # lint + 格式化，行長 100，tests/ 不檢查 E501

flask --app wsgi run --debug             # 開發伺服器（排程在 reloader 子程序啟動）
flask --app wsgi routes                  # 列出路由
flask --app wsgi db migrate -m "..."     # 依模型變更產生 migration（需連得到 DATABASE_URL）
flask --app wsgi db upgrade              # 套用 migration
```

**測試不要寫死會過期的日期**（2026-10-05）：測試裡的固定日期只要會跟真實時鐘比較（「最近 7 天」的查詢、保留 30 天的清除…），
過一陣子就會開始失敗；這類時間一律用 `now_tw()` / `datetime.now()` 相對計算，或把 `now` 當參數傳進被測函式。
新增跟時間有關的測試後，用 `FREEZE_AT=...`（`tests/conftest.py`，freezegun）挑幾個未來日期跑一次確認。

**不要測試 AI 功能**：不要為了驗證而執行任何會真的呼叫 OpenAI 的流程（後台「計算向量」、衛教 PDF 匯入（會自動算向量）、
處方箋辨識、LINE 自由文字對話等），也不要花時間驗證 AI 回答品質；
這些會產生費用且結果不穩定。`pytest` 裡的 AI 相關測試（`tests/test_assistant.py`、`tests/test_llm_backends.py`、
`tests/test_prescription.py`）全用 `FakeLlmClient` / `FakeVisionClient`，照常跑即可，但除非使用者明確要求，
不要新增或修改針對 AI 行為本身的測試。

環境設定來自 `.env`（複製 `.env.example`），`config/settings.py` 在 import 時以 `load_dotenv(override=True)` 讀取；
改 `.env` 需重啟，**環境變數會被 `.env` 蓋掉**，一次性腳本要用 `create_app("testing")` 而不是 `FLASK_ENV=testing`。

**沒有 CLI、沒有 cron**（2026-09-28 移除 `app/cli.py`；唯一的例外是 2026-10-05 使用者要求的 `scripts/import_pdfs.py`：
`python scripts/import_pdfs.py [資料夾=data] [--hospital 代碼] [--department 科別] [--chunk-size small|medium|large] [--no-embed]`
把資料夾裡的衛教 PDF 直接匯入並算向量，不經瀏覽器上傳，走同一個 `KnowledgeService.import_pdfs()`、可重跑；會連 `.env` 的資料庫並呼叫 OpenAI 算向量）：所有資料維護都在後台 `/admin/*` 做（院所 / 人員角色 / 藥局藥品 CSV /
衛教 PDF、重建片段、算向量）。定時工作由 `app/scheduler.py`（APScheduler，`create_app()` 內啟動）執行：
每 5 分鐘 `ReminderService.run_due()`、每 15 分鐘 `TriageService.poll()`；多 worker 用 Redis `SET NX` 鎖去重，
`SCHEDULER_ENABLED=false` 關閉（測試環境預設關）。第一位 SUPER_ADMIN 由 `.env` 的 `SUPER_ADMIN_LINE_USER_IDS`
（LINE userId，逗號分隔）指定，這些人登入 / 加好友時 `UserService.get_or_create_by_line()` 自動升級；其餘角色在後台指派。
Rich Menu 在 LINE Official Account Manager 建立，六格動作設「文字」且文字等於 `TEXT_KEYWORDS`（我不舒服 / 找醫院 /
我剛出院 / 去哪拿藥 / 服藥提醒 / 意見回饋；**刻意口語化**，機器人語氣，正式名稱 病況諮詢 / 急診動態分流 / 衛教諮詢 /
藥物諮詢 / 服藥小幫手 只當別名，2026-09-28 決定），六格定義在 `app/line/handlers/intents/menu.py` 的 `DEFAULT_CELLS`。
加好友訊息只有「填寫基本資料」一個按鈕（2026-09-28 拿掉「先逛逛就好」）。
`FLASK_ENV=testing` 使用 `TestingConfig`，內含假的 LINE / JWT 憑證，SQLite in-memory 佔位。

## 架構重點

### 請求生命週期有兩條，共用同一套 Principal / 權限

- **LINE webhook** `POST /line/webhook`（`app/line/webhook.py`）：`WebhookParser` 驗簽 → **立刻回 200、事件在背景執行緒處理**（`WEBHOOK_ASYNC`，測試環境關閉改同步；2026-10-01 加入：AI / 辨識流程常要 10–60 秒，同步處理會超過 LINE 等待時間，LINE 判定失敗後「重新傳送」同一事件，使用者就一直收到重複訊息）→ `EventDispatcher` 先用 `conversation_state` 的 `evt:<webhookEventId>` 去重（只有 `isRedelivery=true` 的事件會比對，第一次送達一律處理）→ 依事件類別找 `EventHandler` → 建立 `BotContext`（含 `Principal`，首次接觸即建立 GUEST 使用者）→ 文字 / postback 交給 `IntentRouter` → `IntentHandler.execute()` 先檢查鎖定、綁定、權限，再呼叫子類的 `handle()`。
- **REST API** `/api/v1/*`：`Authorization: Bearer <JWT>` → `app/auth/decorators.py` 的 `current_principal()` **每次都從 Repository 重新載入使用者**，token 內的 role 可能已過期、不拿來判斷 → `@require_auth / @require_permission / @require_role / @require_verified`。

### 授權集中在 `app/domain/rbac.py`

角色分三方（`app/domain/enums.py`）：用戶端 `END_USER_ROLES`（GUEST / PATIENT / FAMILY）、院方 `HOSPITAL_ROLES`（HOSPITAL_STAFF 管病人：客服工單 + 病人核對建檔 + 就診紀錄；MEDICAL_STAFF 管藥：`MEDICATION_MANAGE`，病人唯讀；HOSPITAL_ADMIN = 兩者 + `EDUCATION_CONTENT_MANAGE`（衛教資訊）+ 管理。後台選單（2026-10-02）：衛教資訊 = `EDUCATION_CONTENT_MANAGE`（讀寫都要，院所管理員 / 系統管理員）、藥局 = `MEDICATION_MANAGE`（院內人員看不到）、權限表 = `STAFF_MANAGE`；選單 `data-perm` 與 API decorator 用同一個權限，都必須有 `hospital_id`）、系統方 `SYSTEM_ROLES`（SUPER_ADMIN，開發 / 維運，不屬於院所、不受隔離）。`is_staff` = 院方 + 系統方（可登入後台），「必須有院所」的判斷用 `is_hospital_staff`。前端不硬編碼角色清單，用 `/api/v1/auth/me` 的 `is_staff` / `assignable_roles`。2026-09-28 `CS_AGENT` 改名 `HOSPITAL_STAFF`（併入 `0001_initial`，沒有另開 migration）。

`ROLE_PERMISSIONS` 是明確列舉（非階層繼承），`AccessPolicy` 是唯一的判斷入口，包含院所隔離 `can_access_hospital()`（SUPER_ADMIN 不受限）與角色指派範圍 `can_assign_role()`。新增權限時同時改 `Permission` 列舉與此對照表，`tests/test_rbac.py` 有對角色集合關係的斷言。

`Principal.is_verified`（`BindingStatus.VERIFIED`）就是服務地圖上「基本資料已填且驗證通過？」菱形的判斷；`IntentHandler.requires_verified = True` 與 `@require_verified()` 都以此為準。狀態由 `IntakeService`（送出 → `PENDING`）與 `PatientRecordService.verify()`（院方核對 → `VERIFIED`，並把 GUEST 改為 PATIENT / FAMILY）驅動；本機要快速測個人化功能仍可直接 `container.user_repo.update()`。

### 依賴注入：`ServiceContainer`（`app/core/container.py`）

所有 Repository、Service、`JwtService`、`LineLoginService`、`LiffAuthService`、`LineMessagingClient` 在 `ServiceContainer.build(app)` 一次組裝，掛在 `app.extensions["container"]`，透過 `get_container()` 取用。測試以 `app.extensions["container"].line_client = FakeLineClient()` 替換元件（見 `tests/conftest.py`），任何對外呼叫都必須經過容器內的物件，不要直接 new SDK client。

### 資料層：PostgreSQL（SQLAlchemy + Flask-Migrate）

所有實體（`User`、`Feedback`、`Hospital`、`Patient*`、`PatientRecord*`、`AuditLog`）都是 `app/models/` 下的 SQLAlchemy 模型（皆繼承 `db.Model, TimestampMixin`），`app/repositories/*.py` 裡的 `SqlAlchemy*Repository` 實作對應 Protocol，`ServiceContainer.build()` 組裝時直接用這些實作 —— Service / API 層完全不知道底層是 DB 還是記憶體。沒有記憶體版 Repository；只有 OAuth state / 對話狀態 / rate limit 在未設 `REDIS_URL` 時用單程序記憶體實作。`OAuthStateStore`（`app/auth/state_store.py`）與 rate limit 未設 `REDIS_URL` 時用單程序記憶體，設定後由容器改用 Redis 實作。

Enum 欄位（`Role`/`BindingStatus`）一律用 `sa.Enum(..., native_enum=False)` 存成 VARCHAR，不用原生 Postgres enum type —— 這樣新增列舉值（例如未來新角色）只要改 Python enum + migration 加欄位資料，不必下 `ALTER TYPE`。除了 `Role`（會持續變動，刻意不加 CHECK constraint）之外都保留 CHECK constraint。之後若有 JSON 欄位，用 `JSONB().with_variant(JSON(), "sqlite")` 讓測試環境的 SQLite 也能跑。`Hospital` 實體已存在（`app/models/hospital.py`，`hospitals.id` 就是人可讀的院所代碼，例如 `H1`），其他表的 `hospital_id` 都是指向它的外鍵，應用層另由 `HospitalService.require_active()` 在寫入時確認院所啟用中。SUPER_ADMIN 在後台「院所設定」新增或用 CSV 匯入（支援自家範本與健保署「特約醫事機構」開放資料欄位，對應表在 `HospitalService.IMPORT_COLUMN_ALIASES`，藥局等非院所列自動略過）；HOSPITAL_ADMIN 只能改自己院所的名稱 / 電話 / 地址。

改動模型後：`flask --app wsgi db migrate -m "..."` 產生 migration（需要連得到 `DATABASE_URL` 指到的資料庫，本機用 pgAdmin 或 `createdb` 建立）→ **人工檢查**產生的檔案（autogenerate 對 Enum/JSONB 這類型別不一定抓得對）→ `flask --app wsgi db upgrade`。歷次 migration 已於 2026-09-28 統整為單一版本 `0001_initial.py`（含 `CREATE EXTENSION vector`，**PostgreSQL 伺服器必須先裝 pgvector 擴充套件**，pip 的 `pgvector` 只是 Python 型別），之後的變更另開版本（`0002_hospital_departments`、`0003_knowledge_department`）；舊資料庫用 `db stamp --purge 0001_initial` 對齊。
**revision id 不能超過 32 字元**（`alembic_version.version_num` 是 VARCHAR(32)，超過會在最後一步回滾整個 upgrade）。`pytest` 全程走 in-memory SQLite（`TestingConfig`，`StaticPool` 讓整個 engine 共用一條連線），不需要真的 PostgreSQL，`tests/conftest.py` 的 `app`/`client` fixture 會在每次測試維持一個 app context（供測試直接呼叫 repository）但在每次送出 HTTP 請求時暫時 pop/push，避免 Flask 沿用同一個 app context 造成 `flask.g`（含暫存的 `Principal`）跨請求殘留。

### 病人自填表與院方後台（取代原本的綁定流程 B1–B9）

- **欄位定義單一來源**：`app/domain/intake_schema.py` 的 `INTAKE_STEPS`（6 步）同時供 LIFF 頁面渲染（`public_schema()` 塞進模板）、伺服器端驗證（`validate_step` / `validate_all`，`partial=True` 為草稿）、後台顯示中文標籤。改欄位只改這一個檔案；`Field.storage` 決定落到哪張表（profile 欄位 / JSON / contacts / allergy:kind / condition:kind / medication / consent），`SqlAlchemyPatientRepository` 依此做 dict ↔ 多表轉換。
- **資料歸屬**：自填資料（`patient_*` 7 張）跟著 `users` 走、跨院所共用一份；院方紀錄（`patient_records` + 審核 / 就診 / 風險 / 追蹤 / 備註）跟著 `hospital_id` 走。**院所只看得到自己新增的病人**（`preferred_hospital_id` 是本院，不論病人確認了沒）或已在本院建檔的病人；別家新增的、還沒人新增的一律看不到、不能動，只有 SUPER_ADMIN 看全部。**表單不問院所、沒有 QR 連結**：病人加好友填完表後不會出現在任何院所的清單，院方在後台「搜尋病人」用手機 / 姓名 / 身分證末四碼（至少一個、多個同時成立）**精準比對**（`GET /api/v1/admin/patients/unclaimed`，每個欄位完全相等、不做模糊搜尋）找到本人，按「加入本院並請病人確認」（`POST /api/v1/admin/patients/{id}/request-hospital-confirmation`）→ 寫入 `preferred_hospital_id` = 本院、推播兩個按鈕；病人按「是」走 `Intent.HOSPITAL_CONFIRM` → `IntakeService.confirm_hospital()` 記下 `hospital_confirmed_at`，按「不是」清掉院所（回到沒人新增）。**核對建檔的前提是病人自己確認過**：`PatientRecordService.verify()` 沒有確認就回 409。
  **就診 / 住院紀錄不需要先建檔**（2026-10-01）：`add_encounter()` 在病人還沒建檔時先把病人指定到本院（還沒人新增的病人會推播確認，需 `PATIENT_VERIFY`；SUPER_ADMIN 要帶 `hospital_id`）並開一筆 `patient_records.status="draft"` 的暫存紀錄；`verify()` 把 draft 轉成 `active`（就診紀錄保留），病人按「不是」時 `IntakeService.confirm_hospital()` 把 draft 連同底下紀錄刪除。「是否已建檔」一律看 `status != "draft"`，不要只看紀錄存不存在。
- **身分證**：**不存明碼也不存密文**（2026-09-28 移除 `national_id_encrypted` 欄位與 `PATIENT_IDENTITY_READ`）。`IntakeService._digest_national_id()` 用 `FieldCipher.fingerprint()`（HMAC，金鑰 `PATIENT_DATA_KEY`）算 `national_id_fingerprint` 並留 `national_id_last4`，所有 API、所有後台角色一律只回遮罩 `******1234`；院方核對用末四碼 + 姓名 + 生日對照 HIS。前端回傳遮罩字串代表沒改。同一指紋出現在第二個帳號 → 送出時回 409。
- **狀態轉換**：送出 → `PENDING`；院方 verify → `VERIFIED` + 角色；reject → `UNBOUND`。送出後（`PENDING` / `VERIFIED`）病人端**唯讀**：`IntakeService._ensure_can_edit()` 對存步驟 / 送出回 403（`details.read_only=true`），`GET /liff/intake` 回 `read_only` 讓前端把表單鎖成檢視模式；資料修正只能由院方在後台做。每次送出都在 `patient_form_submissions` 存一份原始 JSON，院方修正只改結構化表並寫 `patient_record_reviews.corrections`。
- **後台**：`/admin/*`（`app/admin_pages.py`）是 Jinja + Bootstrap 骨架，頁內 JS 讀 `sessionStorage` 的 JWT 打 `/api/v1/admin/*`，沿用 LINE Login callback 存 token 的機制，沒有 cookie session。未登入 / 過期 / 非院方帳號都導到 `/admin/login`（獨立登入頁，`?next=` 只接受 `/admin` 開頭的路徑）；LIFF 端不經過這頁，維持 `liff.init()` 自動登入。選單依 `/api/v1/auth/me` 回傳的權限隱藏，真正授權在 API decorator。後台每頁只載入 `GET /api/v1/admin/hospitals/options`（醫院層級、不含診所，約幾百家）供下拉選單與名稱顯示；院所 / 藥局 / 藥品等上萬筆的清單一律走分頁 API（`app/core/pagination.py` 的 `PAGE_SIZE=15` + `page_payload()`、`repositories/base.py` 的 `paginate()`），前端用 `Admin.pagination()` 畫頁碼（超過 5 頁時多一個「跳到第 N 頁」輸入框），不要一次抓全部。**後台清單頁版面統一**（2026-10-02，以藥局頁為準）：一張 card 內依序是篩選 panel（`form.row.g-2#filter-form`：關鍵字 + 下拉 + 「搜尋 / 清除」）、`table table-sm table-hover align-middle`、空資料提示 `<p>`、分頁，標題右側顯示「共 N 筆」；縣市 / 鄉鎮市區篩選一律用 `Admin.districtSelects(citySelect, districtSelect)` 連動下拉（資料來自 `GET /api/v1/admin/districts` = `tw_districts.csv`），院所清單的鄉鎮市區以「地址包含」比對、列上的 `city` / `district` 由地址解析，表格不顯示座標（在編輯視窗看）。**匯入集中在 `/admin/imports`「資料匯入」頁**（2026-10-02）：選資料類型（院所 / 藥局 / 藥品 CSV、衛教 PDF，依權限過濾）→ 選檔 → `Admin.upload()`；衛教資訊背景工作（PDF 匯入進度 / 重建片段 / 計算向量）也在這頁，各清單頁不再放匯入表單、只留連結（`?type=` 預選類型）。API 端點不變。指派院所用 `Admin.hospitalPicker(input, hidden)`（文字框 + datalist，可打名稱關鍵字或院所代碼；不在選單內的診所可直接輸入代碼）。系統方角色一律不掛院所（`assign_role` 升為 SUPER_ADMIN 時清掉 `hospital_id`），院所管理員的人員清單不列系統方帳號。後台把 `BindingStatus.LOCKED` 顯示為「黑名單」（病人端文案仍是「帳號已鎖定」）。SUPER_ADMIN 在「院所設定」可新增（代碼 / 名稱 / 區域 / 電話 / 層級 / 地址 / 科別 / 座標）與編輯任一院所（列上的「編輯」modal，走同一支 PATCH）。Service 透過 `lambda: container.line_client` 取 LINE client，測試替換 `container.line_client` 才會生效。
- **安全相關慣例**：院方查詢範圍一律用 `AccessPolicy.hospital_scope(actor)`（無 `hospital_id` 的院方帳號直接 403，不可退化成不過濾）；高敏感操作（鎖定 / 解鎖帳號）寫 `audit_logs`（`container.audit_repo.record()`）；容易被暴力嘗試的端點掛 `@rate_limit()`（`app/core/ratelimit.py`，測試環境 `RATELIMIT_ENABLED=False`）；「今天」一律用 `app/utils/clock.today_tw()`；LINE 推播失敗只記 log 不讓交易失敗（`_notify`）。同一身分證出現在第二個帳號只回 409 不自動鎖定（家屬與本人可能各自填），冒用由院方核對時判斷、必要時後台鎖定。
- `get_config()` 在 `FLASK_ENV` 未設定時視為 production 並拒絕範例金鑰；本機開發務必在 `.env` 設 `FLASK_ENV=development`。
- **登入效期**：只有 access token，**沒有 refresh token、沒有 `auth_sessions` 表、沒有伺服器端登出**（2026-09-28 移除）。`JwtService.issue(principal)` 依角色決定效期：一般使用者 `JWT_ACCESS_TOKEN_EXPIRES`（預設 1 小時）、院方角色 `JWT_STAFF_ACCESS_TOKEN_EXPIRES`（預設 8 小時）。到期後 LIFF 端 `liff/base.html` 的 `api()` 收到 401 會重跑 `login()`（`liff.init()` 自動換新 id_token）再重送；後台 `admin/base.html` 收到 401 導回 `/admin/login`。登出只是前端清 sessionStorage。帳號鎖定 / 降權靠每個請求重載使用者立即生效，不需撤銷 token。
- **Redis**：設定 `REDIS_URL` 後容器改用 `RedisOAuthStateStore` 與 `RedisRateLimiter`（啟動時 ping，連不上直接失敗）；未設定用記憶體版，只適合單程序。
- **外鍵**：`users` / `feedback` / `patient_profiles.preferred_hospital_id` / `audit_logs` 的 `hospital_id` 以 SET NULL 指向 `hospitals`，`patient_records.hospital_id` 為 RESTRICT。
### 藥物諮詢 / 服藥小幫手（Roadmap 第 4、5 項，已實作）

設計文件 `docs/medication-design.md`。兩個功能共用「處方箋」資料，流程：Rich Menu「去哪拿藥」→ Quick Reply 用 LINE 內建
`CameraAction` / `CameraRollAction` 拍處方箋 → 圖片訊息 → `MedicationHandler._action_image()` 先叫
`line_client.show_loading_animation()`（不發「辨識中」訊息）→ 下載圖片 → `PrescriptionService.create_from_image()` →
`container.vision_client` 辨識成 `PrescriptionExtraction`（pydantic v2）→ 存 `prescriptions`（`items` JSON、照片加密後存
`MEDIA_ROOT`、完整保留）→ 回辨識結果 Flex（確認找藥局 / 建立提醒 / LIFF 修改）。

- **辨識後端** `app/integrations/vision.py` 只放提示詞與 `PrescriptionExtraction`，呼叫走 `app/integrations/llm.py` 的
  `OpenAiClient`（`OCR_MODEL`，預設 `gpt-4.1`）。提示詞固定為「只抄寫、不推測、不輸出病人姓名 / 身分證」。
  辨識太久 reply token 過期時 handler 會改用 push。測試一律用 `tests/conftest.py` 的 `FakeVisionClient` 替換 `container.vision_client`。
- **對話狀態** `app/line/state_store.py`（`container.conversation_state`，Redis 或記憶體，TTL 10 分鐘）：圖片與位置訊息沒有意圖，
  `MessageHandler` 依狀態（`await_prescription_photo` / `await_location` / `await_drug_name`）決定交給誰；沒有狀態時先用 Quick Reply
  問「這是處方箋嗎？」「要找藥局還是醫院？」，不會對隨手傳的圖片花模型呼叫。
- **找藥局不用地圖服務**：LINE `LocationMessageContent.address` 自帶地址，`PharmacyService.parse_address()` 取縣市 / 鄉鎮市區
  （臺 → 台）比對 `pharmacies.city / district`；該區沒有退到同縣市；「現在營業中」依 `opening_hours` JSON 與 `now_tw()`。
  LINE 的 `address` 是選填、且手機語系是英文時會是英文地址，抓不到縣市時 `PharmacyService.find_nearby()` 改用經緯度查
  `app/utils/geo.nearest_district()`（`app/resources/tw_districts.csv`：368 個鄉鎮市區的面積中心點，離線資料）取最近的鄉鎮市區；
  兩者都失敗才回「無法判斷縣市」並記 warning。
  藥局 / 藥品是唯讀參考資料，由 SUPER_ADMIN 在後台 `/admin/pharmacies`、`/admin/drugs` 上傳 CSV（`POST /api/v1/admin/pharmacies/import`、
  `/drugs/import`，權限 `REFERENCE_DATA_MANAGE`；`PharmacyService.import_pharmacies / import_drugs`，upsert 可重跑）；
  `MAX_CONTENT_LENGTH` 因健保用藥 CSV 約 96MB 而設為 128MB；後台 multipart 上傳一律用 `admin/base.html` 的 `Admin.upload()`
  （XHR，顯示上傳進度），大檔要走本機網址而非 ngrok。
- **提醒規則單一來源** `app/domain/reminder_rules.py`：頻率代碼（qd/bid/tid/qid/q12h/q8h/q6h/qw/hs/prn/other）× 飯前後 → 預設時間；
  LIFF `/liff/reminders` 也用 `public_rules()`。`REMINDER_MANAGE_OWN` 與 `PRESCRIPTION_MANAGE_OWN` 都在 `_PUBLIC`：
  提醒**不需**先驗證基本資料（`ReminderHandler.requires_verified = False`）。
- **推播** `ReminderService.run_due(now)`：由 `app/scheduler.py` 每 5 分鐘執行；`reminder_deliveries`
  的 `UNIQUE(schedule_id, scheduled_for)` 保證同一時段只送一次，`REMINDER_WINDOW_MINUTES` 容許排程延遲；同一人同一批合併成一則 Flex
  （每顆藥有 已服用 / 30 分後 / 跳過 postback）；`users.push_enabled=False`（封鎖）略過，`FollowHandler` 重新加好友時恢復。
  慢箋：確認處方時算 `refill_reminder_on = issued_on + days_supply × refill_seq − REFILL_REMINDER_DAYS_BEFORE`，同一輪排程推播一次。
  所有 datetime 以 UTC 存，SQLite 讀回的 naive 值一律視為 UTC（`reminder_repository.as_utc`）。
- 尚未做：後台檢視病人處方 / 服藥紀錄；「根據症狀」的提醒（屬病況諮詢延伸）；藥袋藥錠外觀比對（`42_2.csv`）。

### AI 對話助理：RAG + 事件判斷 → 套用 LINE 模組（已實作，2026-09-27）

設計文件 `docs/ai-assistant-design.md`。AI 只用 OpenAI API（`OPENAI_API_KEY`），沒有其他後端。**確定進入點不經 AI**：
Rich Menu / Quick Reply / Flex 按鈕的 postback、圖片、位置訊息，以及**完全等於** `TEXT_KEYWORDS` 的文字
（主選單 / 選單 / menu、我不舒服、找醫院、我剛出院、去哪拿藥、服藥提醒、意見回饋、設定與正式名稱別名；
`keyword_intent()` 去頭尾空白、不分大小寫）都直接走功能。另外兩個不等模型：紅旗關鍵字 `EMERGENCY_KEYWORDS`
直接進緊急畫面（安全層）、「等藥名」狀態的文字當藥名查詢。**只有不是關鍵字的自由文字**才交給 AI 判斷事件再套用模組，
程式裡不寫規則式的意圖猜測。

- **LLM** `app/integrations/llm.py`：`OpenAiClient.complete_structured(prompt, response_model, system=, images=)`
  用 OpenAI Chat Completions 的 structured outputs 直接回 pydantic 實例；對話 `AI_MODEL`（預設 `gpt-4.1-mini`）、
  處方辨識 `OCR_MODEL`（預設 `gpt-4.1`），金鑰 `OPENAI_API_KEY`（正式環境必填，`ProductionConfig` 缺少即啟動失敗；開發環境留空時容器改注入
  `DisabledLlmClient`，AI 對話退回關鍵字、處方辨識回失敗訊息、語意檢索停用，app 仍可啟動）。
  測試以 `tests/conftest.py` 的 `FakeLlmClient`（依關鍵字回固定決策，`decisions` / `default` / `error` 可改）替換
  `container.llm_client`。
- **檢索（RAG 的 R）= 混合檢索**：關鍵字（`app/domain/retrieval.py` 的 jieba 搜尋模式 + 字元 bigram + `CUSTOM_WORDS`
  → PostgreSQL tsvector 全文檢索，GIN 索引）+ 語意（`OpenAiClient.embed()`，`EMBEDDING_MODEL` 預設 `text-embedding-3-small`，
  pgvector `vector(1536)` + HNSW 索引），各取 10 個候選（`CANDIDATES_PER_SOURCE`，2026-10-04 由 20 調降）後 `rrf_fuse()` 合併、同一份文件最多 2 個片段
  （`KnowledgeService.retrieve()`、`SqlAlchemyKnowledgeRepository.search_keyword / search_vector`）。
  SQLite（測試）用記憶體 `BM25Index` 與 Python 餘弦；沒有向量或查詢向量失敗時只用關鍵字。
  `retrieve(department=)` 有科別時再多兩個「只在該科別內」的排序清單一起 RRF → **同科別加權、不硬過濾**
  （科別分類正確率約 9 成）；科別來源是已驗證病人最近就診的科別（`ConversationService._patient_block()`）。
  **檢索不分院所、本院優先（2026-10-04 定案）**：衛教資訊是所有院所 + 通用的共用池，`ConversationService.build_context()`
  不限範圍檢索，並把病人自己的院所（所屬院所 / `preferred_hospital_id`）傳給 `retrieve(prefer_hospital_ids=)`：
  多兩個「只在本院內」的排序清單進 RRF，但只收**夠相關**的本院片段（分數 ≥ 全庫第一名 × `PREFER_MIN_VECTOR_RATIO` 0.85 /
  `PREFER_MIN_KEYWORD_RATIO` 0.5，經驗值、未用真實語料調過），所以本院沒有相關內容時結果就是其他院所 / 通用的，
  不會把本院不相干的文件硬排到前面。`hospital_ids` 參數仍可明確限定範圍：`None` = 不限；list = 只含列出的範圍
  （`None` 元素 = 通用文件），`[None, "H1"]` = 通用 + H1、`["H1"]` = 只有 H1。
  PostgreSQL 的向量查詢在有院所 / 科別條件時用 `ORDER BY (embedding <=> v) + 0` 讓 HNSW 不適用、
  改在過濾後的片段內精確計算（HNSW 是先取全庫近鄰再套 WHERE，限定單一院所會湊不滿）；不限範圍才走 HNSW。
  不限院所時各院轉載的相同片段只留一份（`retrieve()` 以片段內文去重）。
  `tokens` / `search_vector` / `embedding` 都存 DB；向量輸入 = `科別 › 標題 + 片段`，由後台「計算向量」補算（只算 NULL 的，可重跑）。
  SQLite 的 `embedding` 用 `JSON(none_as_null=True)`，否則 None 會存成 `'null'` 字串、`IS NULL` 濾不掉。
- **切段（2026-09-29 重寫為結構感知 recursive chunking）** `app/domain/retrieval.py` 的 `chunk_document()`：
  `clean_lines()` 先去樣板行（Qrcode / 麵包屑 / 日期 / 目錄頁 / 與標題重複的行）→ 空行切 block →
  `_classify()` 認 標題（`#`、壹、一、（一）、1.、≤20 字無句尾標點的短行；編號行後面接同家族編號行就是條列不是標題）/
  條列（連續同家族 + 以「：」結尾的引言）/ 表格（連續 6 個 ≤ 25 字短 block，多數以句號結尾則是一句一行的條列）/ 問答（Q + 下一段）
  → 超過硬上限（目標 400 × 1.5）的 block 依 換行 → 句號 → 逗號 遞迴下探、不在句中切 → 同一小節內貪婪合併到 400、
  相鄰片段重疊最後一句 → 不到 80 字的尾巴併回前一段。每個片段前綴標題路徑「章 › 節：內文」，另存 `knowledge_chunks.heading` / `kind`
  （text / list / table / qa）。**片段大小由院所管理員選**（2026-10-04）：`KnowledgeService.CHUNK_SIZES`
  small 300 / medium 400 / large 600 字（目標字數，硬上限與下限依比例），存在文件的 `meta.chunk_size`、
  `_chunks_for()` 依它切；上傳 PDF 時選，之後可在「資料匯入」頁對本院文件換大小「重建片段」（`KnowledgeJobService` kind `rechunk`）。
- **知識庫** `knowledge_documents` / `knowledge_chunks`（`app/models/knowledge.py`，migration `0003_knowledge_department`
  加了 `department` / `heading` / `kind`）：**管理以院所為單位、檢索不分院所（2026-10-04）**，每份文件用 `hospital_id` 記錄歸屬
  （誰上傳、誰管理），空 = 通用（只有 SUPER_ADMIN 看得到、能改）。**院所管理員在後台只看得到、只能改自己院所的文件**
  （`KnowledgeService.list()` / `_get_owned()`，通用與別家的單篇回 403），但**所有院所上傳的內容病人都檢索得到**（本院優先，見上），
  後台搜尋 = 本院病人會檢索到的片段。
  `source`（檔名或網址）與 `content_hash` 去重**以院所為單位**（不同院所可各有一份相同內文），`meta` 存出處
  （`hospital_name` / `department` / `imported_from` / `pages` / `chunk_size`）。後台 `/admin/knowledge`（選單名稱「衛教資訊」：列表含每篇片段數、編輯、搜尋；
  **SUPER_ADMIN 多一張「各院所的衛教資訊」**：`GET /api/v1/admin/knowledge/hospitals` 每家院所的文件 / 片段 / 已有向量數，點一列篩出該院所的文件），
  API `/api/v1/admin/knowledge*`：讀取與寫入都要 `EDUCATION_CONTENT_MANAGE`（綁自己院所）。
  後台手動新增 / 修改文件後 `_embed_quietly()` 順手算該範圍還沒有的向量（算不了只記 log）。
  科別是自由文字（上傳 PDF 時選、或在後台編輯），**各院所可以有自己的科別分類**：`KnowledgeService.detect_department()`
  先比對檢索範圍內實際用到的科別名稱，再退回通用科別表 / 口語別名（`retrieval.department_in_text()`），就診紀錄的科別也走同一套。
  **資料來源只有 PDF 上傳與後台手動新增**（2026-10-04：txt / Markdown 匯入都移除，`data/knowledge/*.md` 種子要用「新增文件」貼上）。分類在 `KnowledgeService.CATEGORIES`。`data/` 整個目錄不進版控。
- **知識庫背景工作**（`/admin/imports`，`KnowledgeJobService`）：kind `pdf_import` / `rechunk` / `embed`。
  PDF 匯入、重建片段與進度查詢是 `EDUCATION_CONTENT_MANAGE`（院所管理員只動、只看得到自己院所的工作與統計；
  別人的工作進行中只回 `busy`），全庫「計算向量」只有 SUPER_ADMIN（`REFERENCE_DATA_MANAGE`）。
  **院所範圍的匯入 / 重切做完自動接著算該院所的向量**（`_embed_scope()`；算不了不讓工作失敗，原因記在結果的 `embed_error`，
  片段照存、關鍵字檢索可用）；SUPER_ADMIN 重建全部時向量清掉、要另外按「計算向量」。
  建 `knowledge_jobs` 列後交給 `spawn`（容器預設開 daemon 執行緒 + app context）執行，進度寫回 `progress`，
  頁面每 3 秒輪詢 `GET /api/v1/admin/knowledge/jobs`（同時回片段大小選項與範圍內用過的科別）；同一時間只允許一個工作，
  超過 30 分鐘沒更新的視為中斷。上傳檔暫存 `MEDIA_ROOT/knowledge-imports/<job_id>/<序號>.pdf`，跑完即刪（**原始 PDF 不保留**，只存擷取出的文字）。
  測試以 `container.knowledge_jobs.spawn = lambda fn: fn()` 同步執行。
- **衛教 PDF 匯入（2026-10-04，取代依科別分類的 txt）**：`POST /api/v1/admin/knowledge/jobs/pdf-import`
  （multipart `files` 多檔 + `department` + `chunk_size` + SUPER_ADMIN 才有用的 `hospital_id`）→ `KnowledgeService.import_pdfs()`：
  **一個 PDF 一篇文件**，標題 = 檔名、`source` = 檔名（**同院所同檔名重傳 = 更新**）、category = education，
  科別整批套用（上傳時選，自由文字，下拉建議本院用過的）；院所管理員一律歸自己院所，SUPER_ADMIN 指定院所或留空（通用）。
  讀不了的檔（損毀 / 加密 / 掃描影像沒有文字層）列在結果的 `errors`，不影響其他檔；內文上限 `MAX_CONTENT_CHARS` 20 萬字。
  **上傳上限只對這個端點放寬**（2026-10-05）：view 內在讀 `request.files` 前設 `request.max_content_length` =
  `KNOWLEDGE_UPLOAD_MAX_MB`（預設 1024 MB，其餘端點仍是 `MAX_CONTENT_LENGTH` 128 MB），一次最多 `MAX_UPLOAD_FILES` 500 個檔；
  超過上限回 413（訊息帶實際套用的上限），資料匯入頁送出前也會先檢查總大小與檔數。
  文字擷取在 `app/utils/pdf.py`（`pypdf`）：`normalize_pdf_pages()` 先把康熙部首區的字元換回一般漢字
  （PDF 常把「高血壓」抽成字碼不同的「⾼⾎壓」，不換關鍵字檢索對不到；只對部首 / 連字區做 NFKC，全形標點不動）、
  （CJK 相容表意文字 U+F900–FAFF 也換回：「不 來 老 行」這類同形異碼字，實測一份 PDF 就有 8 千多個）、
  去掉 NUL 等控制字元（PostgreSQL 文字欄位不接受 NUL，`retrieval.clean_text()`）、
  濾掉字型沒有文字對應表造成的亂碼行（`is_garbled_text()`：一行的字母有 15% 以上不是漢字 / 拉丁 / 希臘 / 假名，且混了兩種以上文字系統；
  同一頁亂碼行 ≥ 3 行時連全是非預期文字的短行也拿掉；`_chunks_for()` 切段後再對片段的每一行檢查一次）、
  去頁碼行與每頁重複的頁首頁尾、把排版折行接回同一段（一行寫滿版面寬度且不是句尾標點結束 → 下一行同段），
  其餘每行各自成段（標題、條列因此是獨立 block）再交給 `chunk_document()`。
  **不做 OCR**（2026-10-05 試做過「掃描頁轉圖交給視覺模型抄寫」，使用者決定只匯入有文字層的 PDF，已移除）：
  掃描影像（單張、海報常見）沒有文字層，該檔列入 `errors`。
  PDF 沒有網址，LINE 回答的「參考來源」按鈕只有 `source` 是 http 開頭的文件才有。
- **決策格式單一來源** `app/domain/assistant_schema.py`（`AssistantDecision`：event / risk / answer / slots / actions /
  citations / needs_clarification）。模型只回這個結構，**不決定訊息長相**。
- **脈絡** `ConversationService.build_context()`：使用者狀態（綁定 / 住址）、最近對話（存 `conversation_state` store，
  key `<line_user_id>:history`，TTL `AI_HISTORY_TTL_SECONDS`；**只供理解上下文**：prompt 標明不要重複其中內容、
  助理回答只留前 120 字、Rich Menu 關鍵字視為換話題會 `clear_history()`，2026-10-01 修正模型一直重講舊內容）、
  知識片段（K1…；文字裡出現知識庫科別名 `retrieval.department_in_text()` 時做同科別加權）、即時資料
  （提到縣市 → 急診看板摘要，用已存的資料不即時抓；藥名 → `PharmacyService.mentioned_drugs`（資料庫比對句中的中文商品名）
  + jieba 詞 `search_drugs`，只收名稱開頭相符的）。`SYSTEM_PROMPT` 規定：只能依片段
  逐條對照寫答案、有依據必標 citations、沒有依據要明說並建議詢問原院所、先結論再 2–4 個重點、不診斷不開藥、
  **answer 是純文字不用 Markdown**（規則 4；另有保險：`assistant_schema.plain_text()` 在 `AssistantDecision.answer`
  的 validator 去掉粗體 / 標題 / 引言 / 程式碼符號、清單符號改「・」、連結只留文字，2026-10-06）。
- **事件 → 模組** `AssistantHandler`（`intents/assistant.py`，`Intent.ASSISTANT`）：emergency → 緊急畫面；hospital →
  `FindHospitalHandler`（slots.city 有值直接 `action=area`）；medication → 藥名查詢 / 選單；pharmacy → 找藥局；
  clinic（2026-10-05 新增：「先看診所」「附近有診所嗎」，原本會被判成 hospital 而走急診醫院）→ 找診所
  （`action=clinics`，`slots.symptom` / `slots.department` 由模型從最近對話帶上）；
  reminder / discharge / human / intake → 對應模組；symptom / faq / smalltalk / unknown → answer + `actions` 變 Quick Reply
  （對應表在 `app/line/messages/assistant.py`）。answer 有引用（`decision.citations` → `decision.attach_sources(hits)` →
  `decision.sources`）時用 Flex：正文 + 最多 3 個「參考來源」URI 按鈕（`hit_label()`：醫院《標題》，開原文網址）。模組回覆用 `BotContext.capture()` 收集後與 answer 合併成一次 reply。
  先顯示 loading animation（60 秒），reply token 過期改 push。AI 失敗（未啟用 / 逾時 / 格式錯）→ `TEXT_KEYWORDS` 關鍵字退路
  → 再不行回失敗訊息 + 主選單。
- **病況諮詢 / 衛教諮詢（Roadmap 第 2、3 項，2026-09-28 以最簡方式實作）**：都只是 `SYSTEM_PROMPT` 規則 + 脈絡，
  沒有新 Service。symptom：模型填 `slots.department`（片段沒提時依症狀判斷，判斷不出來填家庭醫學科）與
  `risk`（none / watch / urgent）。**非急症（none / watch）回答後直接接著推薦診所，不用按按鈕**（2026-10-05，
  `AssistantHandler._symptom_with_clinics()` → 找醫院模組 `action=clinics` + 科別）：使用者在對話裡說了所在縣市
  （`slots.city` / `slots.district`，不拿基本資料的居住地代填）→ 同一次回覆列出院所；沒說 → 多一則「請分享位置」
  並記下 `AWAIT_LOCATION` `for=clinics` + 科別 + 症狀摘要，位置訊息回來由模型挑診所（見「急診分流 / 找醫院」的
  `recommend_clinics()`）。模型要再問清楚
  （`needs_clarification`）且沒給科別時不推薦（給了科別就照樣推薦，模型有時兩者並存）。urgent 補 119 + 找醫院 + 附近診所按鈕。
  `AssistantHandler` 每次決策記一行 INFO log（event / risk / department / clarify / actions，不含使用者文字）。

**沒有真人客服，只有意見回饋（2026-09-28）**：`handoff_tickets` / `HandoffService` / `Intent.HUMAN` 都移除。`Intent.FEEDBACK`（Rich Menu 第六格「意見回饋」、AI `event=feedback`）只回 LIFF `/liff/feedback` 的按鈕；**「意見回饋」按鈕只放在 Rich Menu**，主選單訊息（`MENU_CELLS`）、預設回覆與各功能流程的 Quick Reply 都不放；`FeedbackService.submit()` 任何登入的 LINE 使用者都能用（`FEEDBACK_SUBMIT` 在 `_PUBLIC`，不需填過病人表單），歸屬院所 = 病人已確認的就醫院所或院方帳號的院所，沒有就只有 SUPER_ADMIN 看得到。後台 `/admin/feedback`（`FEEDBACK_MANAGE`：HOSPITAL_STAFF / HOSPITAL_ADMIN）列表與標記處理。Bot 不會因為使用者送回饋而閉嘴。discharge：已驗證病人的 prompt 多一段
  【病人資料】（`patient_record_repo.latest_encounter()` 的科別 / 診斷 / 出院代碼 + 自填慢性病），問到照護時檢索補上診斷；
  模型有 answer 就直接回個人化衛教，answer 留空 → `DischargeHandler` 通用版；Rich Menu「我剛出院」已驗證也走
  `conversation.decide()`（`DischargeHandler.PERSONAL_QUESTION`），AI 失敗退回通用版。
  **通用版「選擇科別」**（`DischargeHandler.DEPARTMENTS`，名稱對齊知識庫科別）送出「我剛從 X 出院…」後，
  模型有 answer 就直接回（不論驗證與否），留空則回 `NO_DISCHARGE_DATA_TEXT`，**不再退回選科別卡片**
  （2026-10-01 修正：原本未驗證一律回卡片，選完又是同一張，形成迴圈）。
  新增事件或動作：改 `assistant_schema.py` 的 Literal + `AssistantHandler._compose()` /
  `messages/assistant.py`，`tests/test_assistant.py` 對每種事件都有對應測試。

### 急診分流 / 找醫院（只依使用者位置推薦，2026-09-28 重新實作）

設計文件 `docs/triage-design.md`。**只做一件事：依使用者位置推薦適合去的急救責任醫院**。沒有分流建議、沒有 119 / 診所接收者、
沒有院方看板（`triage_advisories` / `triage_subscriptions` / `app/integrations/webhook.py` / `/admin/triage` /
`TRIAGE_*` 權限都已移除）。資料來源是健保署「急診即時訊息」API（`app/integrations/nhi_er.py`，公開、免金鑰，
59 家重度級急救責任醫院，每 15 分鐘更新）。`app/scheduler.py` 每 15 分鐘（啟動時先一次）呼叫 `TriageService.poll()`：抓取 → `er_snapshots`
（UNIQUE(醫院, 回報時間)，重跑不重複）→ 清除超過 `TRIAGE_RETENTION_DAYS` 的快照。查詢時快照超過 `TRIAGE_STALE_MINUTES` 會即時補抓一次。

- **規則單一來源** `app/domain/triage_rules.py`：地區代碼 `AREA_NAMES`（臺 → 台）、依層級的等住院門檻、等 ICU / 等看診門檻、
  `congestion_level()` / `decorate()`。**推床數（`wait_bed`）不列入判斷**。119 滿床通報一律「壅塞」。`public_rules()` 供 LIFF 顯示。
- **LINE** `FindHospitalHandler`（`intents/hospital.py`）**只認使用者位置**：入口只請對方「分享位置」（紅旗多「撥打 119」），
  沒有選縣市、沒有用基本資料住址。位置訊息由 `MessageHandler` 依 `AWAIT_LOCATION` `for=hospital` 轉來（params 帶
  `address` / `lat` / `lng`；`PENDING_LOCATION` 也存座標）→ `TriageService.board_for_location(lat, lng)` 依距離列最近 8 家
  （`distance_km`）、`recommended` = 最近且未壅塞的一家，回 Flex + 該醫院的 `LocationMessage`（可導航）；
  AI 症狀諮詢給了科別時（`slots.department` → 「找醫院」按鈕與 `AWAIT_LOCATION` payload 都帶 `department`）
  `board_for_location(department=)` 每家標 `department_matched`、推薦優先「最近、未壅塞、有該科」，
  都沒有再退回最近的並在標題說明（2026-10-01）；**院所主檔沒座標時用地址所在鄉鎮市區的中心點估距離**
  （2026-10-05，`geo.district_centroid()`，列上 `distance_approx=True`、顯示「約 X km」、不附導航用的 `LocationMessage`；
  估的座標不寫回 `lat` / `lng`），地址也認不出鄉鎮市區才回「座標尚未建置」+ 119；急診快照是空的（健保署 API 抓不到）時回 `unavailable=True` → 「資料暫時無法取得」+ 119 +
  找附近診所（縣市 / 鄉鎮市區由地址或 `nearest_district()` 反推）。AI 的 `hospital` 事件不論有沒有提到縣市都走這條。
  「非急症：找附近診所」`nearby_clinics(city, district, lat, lng, department)`：`hospitals` 主檔（`capacity_tier`
  clinic / district / regional）先依地址比對同鄉鎮市區（沒有退到同縣市），有座標的算距離並補 10 km 內有座標的院所；
  排序診所 → 地區 → 區域醫院、再依距離、名稱。**院所主檔的診所大多沒座標**（衛福部資料沒提供），所以不能只看有座標的。
  `department` = AI 症狀諮詢建議的科別（`slots.department` → 「附近診所」postback 帶 `department=`），
  用 `triage_rules.department_terms()` 對應衛福部科別用詞（家醫科 → 家庭醫學科 + 西醫一般科、神經內科 → 神經科…）
  比對 `hospitals.departments`（逗號分隔），一家都沒有就回空清單（`department_matched=False`）、訊息明說
  「找不到標示該科的診所」，**不放寬成不限科別**（2026-10-05：院所主檔沒匯入科別時，放寬後只會照名稱列出
  不相干的診所，肚子痛卻推薦醫美診所）。
  **依症狀推薦診所由模型挑（2026-10-05）** `TriageService.recommend_clinics(city, district, lat, lng, symptom, department)`：
  AI 症狀諮詢帶了症狀 / 科別時（`FindHospitalHandler._reply_clinics()` 的 `symptom` / `department`），把同鄉鎮市區的院所
  （不先用科別過濾，上限 `CLINIC_CANDIDATE_LIMIT` 600 家，大安區約 510 家）以「編號｜名稱｜層級｜登記科別｜距離」交給模型，
  `CLINIC_PICK_PROMPT` 規定只依名稱與登記科別判斷、排除醫美 / 牙科等不相干的、看不出科別的不挑；模型只回編號
  （`assistant_schema.ClinicPicks`），院所資料由程式依編號取回（超出範圍 / 重複的丟掉），結果多 `selected_by="ai"`、
  訊息標明「依名稱與登記科別判斷，請先電話確認」。原因：院所主檔的科別常是空的，醫美診所也會登記家醫科，
  光比對科別欄位不準。沒有症狀也沒有科別（單純「附近診所」）時模型挑一般看診的院所；模型不可用退回上面的科別比對；
  一家都沒挑中回「找不到適合的診所」。測試用 `FakeLlmClient.clinic_picks`（沒設定 = 模型不可用）。
  按鈕沒帶縣市 / 座標時 `FindHospitalHandler` 先請分享位置（狀態 `AWAIT_LOCATION` `for=clinics` + `department`）。
  距離用 `app/utils/geo.py` 的 Haversine，不用地圖服務。
- **院所科別** `hospitals.departments`：來自衛福部「醫療機構與人員基本資料」CSV（`data/醫療機構與人員基本資料_*.csv`，
  欄名 機構代碼 / 機構名稱 / 縣市區名 / 地址 / 科別）在後台「院所設定」匯入；該檔沒有層級欄位，診所從名稱認、
  醫院層級留空不覆蓋既有值，所以先匯健保署特約機構檔（層級）再匯這份（科別）。migration `0002_hospital_departments`。
- **院所座標** `hospitals.lat / lng`：種子檔 `data/er_hospitals.csv`，在後台「院所設定」用 CSV 匯入；
  匯入 CSV / 後台 PATCH 都接受 `lat,lng`（`parse_coords` 驗證）。
- **公開 API / LIFF**：`GET /api/v1/triage/board?lat=&lng=`（依距離）或 `?area=01`（縣市看板，只給 LIFF 切換與 AI 脈絡用）、
  `GET /api/v1/triage/areas`，都不需登入、只有 rate limit。LIFF `/liff/er` 的「用我的位置」用瀏覽器定位。
- 測試以 `tests/conftest.py` 的 `FakeErClient`（`payload` 可改）替換 `container.er_client`。

### LINE 層慣例

- Postback data 統一用 querystring `intent=<Intent>&key=value`，以 `postback_data()` 產生、`IntentRequest.from_postback()` 解析。文字關鍵字對照在 `app/line/intents.py` 的 `TEXT_KEYWORDS`（完全相等才命中，`keyword_intent()`），是不依賴 AI 的確定進入點；「主選單」→ `MenuHandler` 列出功能按鈕（不含意見回饋）；紅旗關鍵字 `EMERGENCY_KEYWORDS`（`is_emergency_text()`，包含比對）命中直接導向緊急畫面，但有 10 分鐘冷卻（`app/line/emergency.py`，`conversation_state` 的 `<line_user_id>:emergency` key）：送過緊急畫面後同一人再打含紅旗的字不再短路、交給 AI 依脈絡判斷，AI 仍判 emergency 只回一句話 + 119 / 找醫院按鈕，避免反覆重傳同一則。
- 加好友（`FollowHandler`）當下用 `line_client.get_profile()` 取顯示名稱 / 頭像存進 `users`（失敗只記 log），後台才認得出是誰。
- 新增意圖：加 `Intent` 列舉值 → 繼承 `IntentHandler`（設 `intent`、`required_permission`、`requires_verified`）→ 在 `IntentRouter.default()` 註冊。新增事件類型：繼承 `EventHandler` 設 `event_type` → 在 `EventDispatcher.default()` 註冊。
- 訊息文案集中在 `app/line/messages/`，透過 `MessageBuilder` 組 Flex / QuickReply；`BotContext.reply()` 自動截到 LINE 上限 5 則。**Flex 樣式單一來源 `app/line/messages/theme.py`**（2026-10-03）：色票取自 `svg/` 的 logo，與後台 `admin/_theme.html` 的 `--ma-*` 同一組（墨藍 `INK` 表頭 / 主要按鈕、灰藍 `GRAY` / `MUTED` 次要文字、白金 `PLATINUM` 次要按鈕與分隔線、紅 `RED` / `RED_STRONG` 緊急與警示）；有標題的卡片用 `theme.header()`（墨藍底、右上角用兩個絕對定位 box 拼出 logo 的紅色「+」，`contents[0]` 固定是標題文字），沒標題的用 `theme.brand_strip()`，整張卡用 `theme.bubble(body, header=, footer=)`，按鈕用 `primary_button / secondary_button / link_button`，清單列用 `theme.row()`（列間白金細線）。各訊息模組與 handler **不要再硬寫色碼**；**Flex 只放要給使用者看的內容，不放說明用的小字**（2026-10-06：資料來源、燈號判斷方式、推薦依據、
免責註腳都拿掉；需要的脈絡用表頭副標一小段，例如診所清單的「地區・科別」）；`M.button_bubble()` 第一個按鈕為主要、其餘次要，`alert=True` 為紅表頭。
- SDK 事件模型是 pydantic v1：`FollowEvent` 需要 `follow.isUnblocked`，測試 payload 缺欄位會被解析成 `UnknownEvent` 而靜默略過。

### 錯誤與回應

業務錯誤一律 raise `app/core/exceptions.py` 的 `AppError` 子類（`ValidationError` 422、`AuthenticationError` 401、`AuthorizationError` 403、`ConflictError` 409、`ExternalServiceError` 502），由 `register_error_handlers` 統一處理：`/api/`、`/line/` 路徑或 Accept 偏好 JSON 的請求回 `{"success": false, "error": {...}}`，其他瀏覽器請求 render `app/templates/errors/error.html`（文案依狀態碼查 `ERROR_PAGE_TEXT`，500 不外露例外內容）；成功用 `app/core/responses.py` 的 `ok()`。

## 規劃中功能（Roadmap）— 技術設計

以下 6 項功能是下一階段要擴充的範圍。**第 6 項（病人表單）、第 4、5 項（藥物諮詢 / 服藥小幫手，見 `docs/medication-design.md`）與第 1 項的 `Hospital` 實體已實作**，其餘尚未實作，先寫成技術設計文件讓使用者確認方向是否正確，確認後再逐項排入開發。每項的格式一致：功能敘述 → 現況對應 → 資料模型設計 → 服務/API 設計 → 與既有架構整合點 → 外部依賴 → 待確認問題。

### 1. 急診動態分流（已實作，2026-09-26；2026-09-28 縮減為只依使用者位置推薦）

實作內容見上方「急診分流 / 找醫院」與 `docs/triage-design.md`；以下為原始規劃，保留供對照。
**接收者（119 / 診所）與分流建議報告已決定不做**，只推薦給使用者。
主要差異：壅塞資料改用健保署急診即時 API（不做人工回報 / HIS）、不新增 `DISPATCH_OPERATOR` 角色（改為後台管理的訂閱名單）、
分流建議以縣市為單位。

**功能敘述**：當偵測到某區一家醫院陷入壅塞狀態，自動生成分流建議報告，透過 API（或 LINE）發送給該區 119 勤務指揮中心或基層診所醫師，建議暫時將病患轉往仍有餘裕的中大型醫院。

**現況對應**：目前沒有「急診」「分流」相關程式碼。`Hospital` 實體已在病人表單功能時補上（`hospitals` 表含 `region`、`capacity_tier`、`contact_phone` 欄位，`lat`/`lng` 尚未加），可直接掛 `HospitalCongestionSnapshot`。

**資料模型設計**：
- `Hospital`（已存在：`id`、`name`、`region`、`capacity_tier`、`contact_phone`、`address`、`active`；需再加 `lat`/`lng`）— 其餘表的 `hospital_id` 之後可考慮補上外鍵（現有資料需先清洗確保值域一致）。
- `HospitalCongestionSnapshot`（`hospital_id` FK、`reported_at`、`occupancy_level`：normal/busy/critical、`source`：his_api/manual、`note`）— 壅塞狀況的時序紀錄。

**服務/API 設計**：
- `TriageAdvisoryService`：定期（cron 或排程 job）評估某區域內醫院的最新 `HospitalCongestionSnapshot`，若超過閾值，比對同區其他有餘裕的醫院，產生分流建議。
- 送達管道（a）LINE push 給訂閱的帳號（需要新角色，例如 `DISPATCH_OPERATOR`，訂閱名單需要驗證身份，不能讓一般使用者訂閱）；（b）`GET /api/v1/admin/triage/advisories` 給外部系統（119 調度系統）主動 pull。
- 壅塞資料寫入：`POST /api/v1/admin/hospitals/{id}/congestion`（`HOSPITAL_ADMIN`/`MEDICAL_STAFF` 權限）供人工回報，或未來串 HIS API。

**與既有架構整合點**：沿用 `app/domain/rbac.py` 的 `Permission`/`Role` 模式新增權限；`AccessPolicy.can_access_hospital()` 的院所隔離邏輯可直接套用在壅塞回報 API 上。

**外部依賴**：無（除非要串接真實 HIS 系統 API）。

**待確認問題**：壅塞資料來源（HIS API？人工回報？兩者都要？）；119/診所帳號的訂閱名單怎麼建立與驗證身份；觸發分流建議的閾值怎麼定義（單一醫院層級？區域層級？）。

### 2. 病況諮詢（已實作最簡版，2026-09-28）

實作內容見上方「AI 對話助理」的病況諮詢段落；未做 `HospitalDepartment`（科別等候 / 掛號連結），科別只由模型依知識庫判斷、
就醫地點用既有找醫院 / 附近診所模組。以下為原始規劃。

**功能敘述**：意圖分類 → RAG 查院內 FAQ、症狀說明，判斷可能需要掛的科別後，透過醫院與診所的資料決定要去哪裡掛號最快能解決問題。

**現況對應**：延伸既有 `Intent.SYMPTOM` / `app/line/handlers/intents/symptom.py`（目前是 stub）。紅旗關鍵字短路機制（`EMERGENCY_KEYWORDS`）已經存在，不用重做。免關鍵字比對到的自由文字，接進 `app/line/handlers/message.py` 裡已經寫好的掛勾點（TODO：「未命中關鍵字時交由 AI 對話引擎判斷意圖」）。

**資料模型設計**：
- `KnowledgeDocument`（`id`、`category`、`title`、`content`、`embedding`、`hospital_id` nullable[通用內容為 null]）— RAG 知識庫，`embedding` 走 pgvector 擴充套件或外部向量庫。
- `HospitalDepartment`（`hospital_id` FK、`department_name`、`avg_wait_minutes`、`accepts_walkin`、`registration_url`）— 依附第 1 項的 `Hospital` 模型，記錄各科別掛號/等候資訊。

**服務/API 設計**：
- `RagService`（`app/services/rag_service.py`）：向量檢索 + LLM 生成答案，輸入症狀文字，輸出 FAQ 回答 + 建議科別。
- `SymptomConsultationService`：串接 `RagService` 判斷科別後，查 `HospitalDepartment` 排序推薦掛號地點，組成 LINE Flex 回覆（含「立即掛號」連結）。

**與既有架構整合點**：`app/line/handlers/message.py` 的 AI 對話引擎掛勾點；`SymptomHandler.handle()` 改呼叫 `SymptomConsultationService`。

**外部依賴**：LLM / embedding 供應商 API（金鑰依 CLAUDE.md 規範放 `.env`，不可硬編碼）；向量檢索元件（pgvector 或外部服務）。

**待確認問題**：LLM/embedding 供應商選型；症狀/科別對應內容由誰審核把關醫療正確性；掛號連結格式（各診所自有系統 API 還是純連結/電話）。

### 3. 衛教諮詢（已實作最簡版，2026-09-28）

實作內容見上方「AI 對話助理」的衛教諮詢段落；未做 `EducationContent` / `PatientEducationAssignment` 與每日任務推播，
個人化衛教由模型依「最近就診診斷 + 慢性病」對照衛教知識庫即時生成。以下為原始規劃。

**功能敘述**：針對不同醫院及不同病因出院的患者，提供 Agentic AI 的衛教資訊，讓病人可以即時知道自己需要做什麼樣的動作。

**現況對應**：延伸既有 `Intent.DISCHARGE` / `app/line/handlers/intents/discharge.py`（stub 已經依 `Principal.is_verified` 區分「已驗證→個人化」vs「未驗證→通用版」，TODO 明確寫著「個人化衛教 + 每日任務推播」尚未實作）。病人的 `discharge_code` 已可由院方在後台「就診 / 住院紀錄」填入（`PatientRecordRepository.latest_discharge_code()` 取最近一次）。

**資料模型設計**：
- `EducationContent`（`id`、`hospital_id` nullable、`discharge_code` nullable、`title`、`body`/結構化步驟、`media_url`）。
- `PatientEducationAssignment`（`user_id` FK、`content_id` FK、`assigned_at`、`schedule`：多日排程、`completed_days`：病人回報進度，選配）。

**服務/API 設計**：
- 綁定驗證通過（B7）時，可依 `discharge_code` 自動配對 `EducationContent` 並建立 `PatientEducationAssignment`（掛在未來綁定流程完成的 hook 上）。
- `DailyTaskPushService`：排程掃描到期的每日任務，透過 `container.line_client.push()` 推播。排程機制建議跟第 5 項「服藥小幫手」共用（見下）。
- 內容管理：`MEDICAL_STAFF` 角色的 CRUD API（`app/api/v1/education.py`），對應既有 `Permission.EDUCATION_CONTENT_MANAGE`（已經在 `app/domain/enums.py` 定義，目前沒有對應 API）。

**與既有架構整合點**：`Permission.EDUCATION_PERSONAL`（已存在）就是這個功能的個人化版權限；`discharge.py` 的既有分支邏輯直接沿用。

**外部依賴**：與第 5 項共用的排程機制（見下）。

**待確認問題**：「Agentic AI」具體互動模式（單向推播 vs. 可雙向問答調整）；衛教內容審核流程；是否要病人回報每日任務完成度。

### 4. 藥物諮詢

**功能敘述**：根據醫生開的處方箋給予購買藥物的地點資訊。

**現況對應**：延伸既有 `Intent.MEDICATION` / `app/line/handlers/intents/medication.py`（stub）。呼應 `app/line/handlers/message.py` 的兩個既有 TODO：「圖片（藥袋辨識）」與「位置訊息（找醫院）」— 這個功能會同時用到兩者。

**資料模型設計**：
- `Prescription`（`user_id` FK、`hospital_id`、`issued_at`、`items`：JSONB 藥品清單、`source`：manual/his_sync/ocr）。
- `Pharmacy`（`id`、`name`、`hospital_id` nullable[院內藥局為某醫院、社區藥局為 null]、`address`、`lat`/`lng`、`contact_phone`）。

**服務/API 設計**：
- `PharmacyLocatorService`：依處方內容 + 病人位置（LINE 位置訊息）排序推薦藥局，回覆用 Flex + 地理位置訊息。
- 處方輸入管道：手動輸入 API、或未來串院方 HIS、或藥袋照片 OCR（三選一或並行，見待確認問題）。

**與既有架構整合點**：`Permission.CONSULT_MEDICATION_ONCE`（已存在）對應未綁定使用者的單次查詢；`MedicationHandler.handle()` 改呼叫 `PharmacyLocatorService`。

**外部依賴**：藥局資料來源（政府開放資料 API 或合作藥局清單）；若要做藥袋 OCR，需要影像辨識服務。

**待確認問題**：處方資料來源；藥局資料來源；是否需要即時庫存查詢。

### 5. 服藥小幫手（已實作，2026-09-26）

實作內容見上方「藥物諮詢 / 服藥小幫手」；以下為原始規劃，保留供對照。

**功能敘述**：根據病人的處方簽或者是症狀，依據藥物的種類提醒該病人需要何時服用藥物。

**現況對應**：延伸既有 `Intent.REMINDER`（已經是 `requires_verified=True`）/ `app/line/handlers/intents/reminder.py`（stub，`feature_name` 已經是「服藥小幫手」）。`app/line/handlers/follow.py` 已經有一個 TODO（使用者封鎖時應停止服藥提醒推播）跟這個功能直接相關，實作時要一併處理。

**資料模型設計**：
- 共用第 4 項的 `Prescription`，解析成 `MedicationSchedule`（`user_id` FK、`drug_name`、`times_per_day`、`scheduled_times`：時間點清單、`start_date`、`end_date`、`active`）。
- `ReminderDeliveryLog`（`schedule_id` FK、`sent_at`、`status`）— 避免同一個時段重複推播，也可做為 LIFF 端的提醒歷史紀錄。

**服務/API 設計**：
- 排程執行方式（實作時改為程序內 APScheduler，見 `app/scheduler.py`；原規劃為 Flask CLI + cron）：**不建議**引入 Celery 這類新的 broker 依賴 — 符合專案目前輕量、依賴少的風格，第 3 項「衛教諮詢」的每日任務推播可以共用同一套排程基礎設施。
- `ReminderHandler.handle()`：查看/新增/暫停提醒；後端 cron 掃描到期的 `MedicationSchedule`，透過 `container.line_client.push()` 發送提醒並寫入 `ReminderDeliveryLog`。

**與既有架構整合點**：`Permission.REMINDER_MANAGE_OWN`（已存在）；`follow.py` 的封鎖處理 TODO。

**外部依賴**：與第 4 項共用的 `Prescription` 資料。

**待確認問題**：處方頻率文字（例如「早晚各一顆」）怎麼自動解析成具體提醒時間，要不要讓病人手動確認/調整；cron 執行頻率與精準度需求。

### 6. LIFF 病人表單

**功能敘述**：使用者提供的規劃表格只有功能名稱、沒有說明文字。技術設計先合理推論為「多步驟 LIFF 表單」—— 綁定流程 B1–B9 之後也會是這類表單之一，兩者可能共用同一套「LIFF 頁面 + 逐步提交 API」模式。

**現況對應（已實作）**：`app/api/v1/liff.py` 有 intake 表單端點，LIFF 頁面在 `app/liff_pages.py`，後台在 `app/admin_pages.py`；原本的綁定流程 B1–B9 由此表單取代。

**資料模型設計**：
- `PatientFormDefinition`（`id`、`hospital_id` nullable、`title`、`schema`：JSONB 描述欄位與驗證規則）— 供院所自訂表單（例如入院前健康問卷、用藥史調查）。
- `PatientFormSubmission`（`id`、`user_id` FK、`form_id` FK、`answers`：JSONB、`submitted_at`、`status`）。

**服務/API 設計**：
- 這個專案沒有獨立前端（見上方架構說明），LIFF 頁面本身就是 Flask 用 Jinja render 出來的 HTML（放在 `app/templates/liff/`），頁面內的 `<script>` 呼叫 `liff.init()` 取得 `id_token` 後再打自家 API —— 不是「後端出一份 JSON schema、前端另外動態渲染」的兩段式設計。
- `GET /liff/forms/<form_id>`：render 表單頁面（依 `PatientFormDefinition.schema` 產生欄位）。`POST /api/v1/liff/forms/<form_id>/submit`：接收表單送出的 JSON，沿用既有的 `require_auth`/`require_verified` decorator 模式。
- 表單建立/管理：`MEDICAL_STAFF`/`HOSPITAL_ADMIN` 角色的 CRUD API。

**與既有架構整合點**：綁定流程與病人表單若都走多步驟表單，可考慮抽成共用元件，但**這次不強求**，先讓 `PatientFormSubmission` 獨立運作，等表單種類變多、真的有重複邏輯時再抽共用 framework（避免過早抽象）。

**外部依賴**：無。

**待確認問題（本項最需要使用者澄清方向）**：這個表單具體要蒐集什麼資料 —— 是既有綁定流程的延伸，還是全新的問卷類表單？是否需要院所自訂欄位，還是先做固定幾種表單即可？

**已實作（2026-09-23）**：固定的「病人自填表（intake）」與「院方紀錄表」兩種，不做院所自訂 schema。欄位、資料模型、API 與後台設計見 `docs/patient-form-design.md`；程式位置見上方「病人自填表與院方後台」。

---

以上第 1 項的 `Hospital` 與第 6 項已實作，其餘為規劃階段的技術設計文件；待使用者逐項確認方向後，再個別排入實作。

## 文件

- `README.md`：端點表、角色摘要、資料庫現況。
- `docs/local-operations.md`：本機安裝（pgAdmin 為主）、LINE Developers Console 設定、ngrok、curl 手動測試、常見問題。
- `docs/architecture.md`：服務地圖節點與程式位置對照、狀態機圖、擴充點。
- `docs/database-models.md`：所有資料表逐欄位說明（值域、用途、外鍵）、關聯總圖與 migration 對照；改模型時一併更新。
- `docs/patient-form-design.md`：病人自填表 / 院方紀錄表的欄位、資料模型、API 與後台設計（Roadmap 第 6 項，已實作）。
- `docs/medication-design.md`：藥物諮詢（處方箋拍照辨識 → 找藥局）與服藥小幫手（排程提醒推播）的設計與實作決策（Roadmap 第 4、5 項，已實作）。
- `docs/ai-assistant-design.md`：AI 對話助理（jieba + BM25 檢索、OpenAI 判斷事件、套用 LINE 模組）的設計與決策（已實作）。
- `docs/triage-design.md`：急診分流 / 找醫院（健保署急診即時 API 定時抓取 → 依使用者位置推薦最近未壅塞的急救責任醫院）。
