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
pytest -p no:warnings                    # 隱藏 PyJWT 短金鑰警告（測試用金鑰刻意很短）

ruff check . && ruff format .            # lint + 格式化，行長 100，tests/ 不檢查 E501

./scripts/run_dev.sh                     # flask --app wsgi run --debug
flask --app wsgi routes                  # 列出路由
flask --app wsgi rbac show               # 角色權限矩陣
flask --app wsgi richmenu setup --image <png>   # 建立並設為預設 Rich Menu（2500×1686）
flask --app wsgi user set-role <user_id> <role> --hospital-id H1   # 以 SUPER_ADMIN 身分指派角色
flask --app wsgi hospital add H1 "第一醫院" --region 台北市          # 新增院所（初始化用，之後可在後台新增）
flask --app wsgi hospital list
flask --app wsgi hospital import data/pharmacy/A21030000I-D21005-001.csv --only-hospitals  # 健保署特約醫事機構 CSV
flask --app wsgi pharmacy import data/pharmacy/A21030000I-D21005-001.csv   # 健保特約藥局（同一份 CSV，只取藥局列）
flask --app wsgi pharmacy import data/pharmacy/35_2.csv                    # 衛福部藥局（只補非健保特約）
flask --app wsgi drug import data/pharmacy/A21030000I-E41001-001.csv       # 健保用藥品項
flask --app wsgi pharmacy find "台北市松山區"                               # 測試依地址找藥局
flask --app wsgi reminder run-due [--now 2026-09-28T08:31:00+08:00]        # 推播到期服藥提醒（cron 每 5 分鐘）
flask --app wsgi hospital import data/er_hospitals.csv   # 59 家重度級急救責任醫院 + 座標（找醫院依距離推薦用）
flask --app wsgi triage poll [--loop --interval 900]   # 抓健保署急診即時資料 → 評估壅塞 → 通知（cron 每 15 分鐘）
flask --app wsgi triage board [--area 台北市]          # 印出急診看板；triage advisories [--all] 列分流建議
flask --app wsgi knowledge import data/knowledge       # 匯入 AI 知識庫種子（Markdown，同檔名重匯更新）
flask --app wsgi knowledge import-health-education "data/衛教內文資料/health_education_content.jsonl"  # 全台醫院衛教內文（可重跑）
flask --app wsgi knowledge embed [--batch 100]         # 替還沒有向量的片段算 OpenAI embedding（可中斷重跑）
flask --app wsgi knowledge stats                       # 文件 / 片段 / 已有向量 數量
flask --app wsgi knowledge search "慢箋可以領幾次"      # 試搜：關鍵字 + 語意混合排序後的片段
flask --app wsgi assistant ask "我頭痛兩天" [--user-id]  # 本機直接問 AI 助理，印出脈絡與決策 JSON

./scripts/setup_db.sh                    # 本機建立 PostgreSQL 角色與資料庫（mediagent/mediagent）
flask --app wsgi db migrate -m "..."     # 依模型變更產生 migration（需連得到 DATABASE_URL）
flask --app wsgi db upgrade              # 套用 migration
```

環境設定來自 `.env`（複製 `.env.example`），`config/settings.py` 在 import 時讀取；改 `.env` 需重啟。
`FLASK_ENV=testing` 使用 `TestingConfig`，內含假的 LINE / JWT 憑證，SQLite in-memory 佔位。

## 架構重點

### 請求生命週期有兩條，共用同一套 Principal / 權限

- **LINE webhook** `POST /line/webhook`（`app/line/webhook.py`）：`WebhookParser` 驗簽 → `EventDispatcher` 依事件類別找 `EventHandler` → 建立 `BotContext`（含 `Principal`，首次接觸即建立 GUEST 使用者）→ 文字 / postback 交給 `IntentRouter` → `IntentHandler.execute()` 先檢查鎖定、綁定、權限，再呼叫子類的 `handle()`。
- **REST API** `/api/v1/*`：`Authorization: Bearer <JWT>` → `app/auth/decorators.py` 的 `current_principal()` **每次都從 Repository 重新載入使用者**，token 內的 role 只是快照不被信任 → `@require_auth / @require_permission / @require_role / @require_verified`。

### 授權集中在 `app/domain/rbac.py`

`ROLE_PERMISSIONS` 是明確列舉（非階層繼承），`AccessPolicy` 是唯一的判斷入口，包含院所隔離 `can_access_hospital()`（SUPER_ADMIN 不受限）與角色指派範圍 `can_assign_role()`。新增權限時同時改 `Permission` 列舉與此對照表，`tests/test_rbac.py` 有對角色集合關係的斷言。

`Principal.is_verified`（`BindingStatus.VERIFIED`）就是服務地圖上「基本資料已填且驗證通過？」菱形的判斷；`IntentHandler.requires_verified = True` 與 `@require_verified()` 都以此為準。狀態由 `IntakeService`（送出 → `PENDING`）與 `PatientRecordService.verify()`（院方核對 → `VERIFIED`，並把 GUEST 改為 PATIENT / FAMILY）驅動；本機要快速測個人化功能仍可直接 `container.user_repo.update()`。

### 依賴注入：`ServiceContainer`（`app/core/container.py`）

所有 Repository、Service、`JwtService`、`LineLoginService`、`LiffAuthService`、`LineMessagingClient` 在 `ServiceContainer.build(app)` 一次組裝，掛在 `app.extensions["container"]`，透過 `get_container()` 取用。測試以 `app.extensions["container"].line_client = FakeLineClient()` 替換元件（見 `tests/conftest.py`），任何對外呼叫都必須經過容器內的物件，不要直接 new SDK client。

### 資料層：PostgreSQL（SQLAlchemy + Flask-Migrate）

所有實體（`User`、`HandoffTicket`、`Hospital`、`Patient*`、`PatientRecord*`、`AuthSession`、`AuditLog`）都是 `app/models/` 下的 SQLAlchemy 模型（皆繼承 `db.Model, TimestampMixin`），`app/repositories/*.py` 裡的 `SqlAlchemy*Repository` 實作對應 Protocol，`ServiceContainer.build()` 組裝時直接用這些實作 —— Service / API 層完全不知道底層是 DB 還是記憶體。沒有記憶體版 Repository；只有 OAuth state / 對話狀態 / rate limit 在未設 `REDIS_URL` 時用單程序記憶體實作。`OAuthStateStore`（`app/auth/state_store.py`）與 rate limit 未設 `REDIS_URL` 時用單程序記憶體，設定後由容器改用 Redis 實作。

Enum 欄位（`Role`/`BindingStatus`）一律用 `sa.Enum(..., native_enum=False)` 存成 VARCHAR，不用原生 Postgres enum type —— 這樣新增列舉值（例如未來新角色）只要改 Python enum + migration 加欄位資料，不必下 `ALTER TYPE`。除了 `Role`（會持續變動，刻意不加 CHECK constraint）之外都保留 CHECK constraint。之後若有 JSON 欄位，用 `JSONB().with_variant(JSON(), "sqlite")` 讓測試環境的 SQLite 也能跑。`Hospital` 實體已存在（`app/models/hospital.py`，`hospitals.id` 就是人可讀的院所代碼，例如 `H1`），其他表的 `hospital_id` 都是指向它的外鍵，應用層另由 `HospitalService.require_active()` 在寫入時確認院所啟用中。初始化用 `flask hospital add` 或 `flask hospital import <csv>`（支援自家範本與健保署「特約醫事機構」開放資料欄位，對應表在 `HospitalService.IMPORT_COLUMN_ALIASES`，藥局等非院所列自動略過），之後 SUPER_ADMIN 可在後台「院所設定」新增或匯入；HOSPITAL_ADMIN 只能改自己院所的名稱 / 電話 / 地址。

改動模型後：`flask --app wsgi db migrate -m "..."` 產生 migration（需要連得到 `DATABASE_URL` 指到的資料庫，本機用 `./scripts/setup_db.sh` 建立）→ **人工檢查**產生的檔案（autogenerate 對 Enum/JSONB 這類型別不一定抓得對）→ `flask --app wsgi db upgrade`。`pytest` 全程走 in-memory SQLite（`TestingConfig`，`StaticPool` 讓整個 engine 共用一條連線），不需要真的 PostgreSQL，`tests/conftest.py` 的 `app`/`client` fixture 會在每次測試維持一個 app context（供測試直接呼叫 repository）但在每次送出 HTTP 請求時暫時 pop/push，避免 Flask 沿用同一個 app context 造成 `flask.g`（含快取的 `Principal`）跨請求殘留。

### 病人自填表與院方後台（取代原本的綁定流程 B1–B9）

- **欄位定義單一來源**：`app/domain/intake_schema.py` 的 `INTAKE_STEPS`（6 步）同時供 LIFF 頁面渲染（`public_schema()` 塞進模板）、伺服器端驗證（`validate_step` / `validate_all`，`partial=True` 為草稿）、後台顯示中文標籤。改欄位只改這一個檔案；`Field.storage` 決定落到哪張表（profile 欄位 / JSON / contacts / allergy:kind / condition:kind / medication / consent），`SqlAlchemyPatientRepository` 依此做 dict ↔ 多表轉換。
- **資料歸屬**：自填資料（`patient_*` 7 張）跟著 `users` 走、跨院所共用一份；院方紀錄（`patient_records` + 審核 / 就診 / 風險 / 追蹤 / 備註）跟著 `hospital_id` 走。院所只看得到「`preferred_hospital_id` 是本院的待核對病人」或「已在本院建檔的病人」。`preferred_hospital_id` 是第 6 步的下拉選單（選項來自 `GET /api/v1/liff/hospitals`，schema 上是 `options_source="hospitals"`），LIFF 連結 `?hospital=` 只負責預選（院所把連結做成 QR）。
- **身分證**：`FieldCipher`（`app/utils/crypto.py`，金鑰 `PATIENT_DATA_KEY`）加密存 `national_id_encrypted`，另存 `national_id_last4` 與 HMAC `national_id_fingerprint`；API 一律回遮罩 `******1234`，只有 `PATIENT_IDENTITY_READ` 能取明碼。前端回傳遮罩字串代表沒改。同一指紋出現在第二個帳號 → 該帳號 `LOCKED`。
- **狀態轉換**：送出 → `PENDING`；院方 verify → `VERIFIED` + 角色；reject → `UNBOUND`。送出後（`PENDING` / `VERIFIED`）病人端**唯讀**：`IntakeService._ensure_can_edit()` 對存步驟 / 送出回 403（`details.read_only=true`），`GET /liff/intake` 回 `read_only` 讓前端把表單鎖成檢視模式；資料修正只能由院方在後台做。每次送出都存 `patient_form_submissions` JSON 快照，院方修正只改結構化表並寫 `patient_record_reviews.corrections`。
- **後台**：`/admin/*`（`app/admin_pages.py`）是 Jinja + Bootstrap 骨架，頁內 JS 讀 `sessionStorage` 的 JWT 打 `/api/v1/admin/*`，沿用 LINE Login callback 存 token 的機制，沒有 cookie session。未登入 / 過期 / 非院方帳號都導到 `/admin/login`（獨立登入頁，`?next=` 只接受 `/admin` 開頭的路徑）；LIFF 端不經過這頁，維持 `liff.init()` 自動登入。選單依 `/api/v1/auth/me` 回傳的權限隱藏，真正授權在 API decorator。Service 透過 `lambda: container.line_client` 取 LINE client，測試替換 `container.line_client` 才會生效。
- **安全相關慣例**：院方查詢範圍一律用 `AccessPolicy.hospital_scope(actor)`（無 `hospital_id` 的院方帳號直接 403，不可退化成不過濾）；高敏感操作（讀身分證明碼、鎖定 / 解鎖）寫 `audit_logs`（`container.audit_repo.record()`）；容易被暴力嘗試的端點掛 `@rate_limit()`（`app/core/ratelimit.py`，測試環境 `RATELIMIT_ENABLED=False`）；「今天」一律用 `app/utils/clock.today_tw()`；LINE 推播失敗只記 log 不讓交易失敗（`_notify`）。同一身分證出現在第二個帳號只回 409 不自動鎖定（家屬與本人可能各自填），冒用由院方核對時判斷、必要時後台鎖定。
- `get_config()` 在 `FLASK_ENV` 未設定時視為 production 並拒絕範例金鑰；本機開發務必在 `.env` 設 `FLASK_ENV=development`。
- **登入工作階段**：`AuthSessionService`（`app/services/auth_session_service.py`）管理 refresh token：`start()` 建 `auth_sessions` 列並簽發 pair（refresh 的 `jti` = session id）、`refresh()` 輪替並偵測重用、`logout()` 撤銷 family。網頁版 LINE Login 的 LINE token 加密存在 session 內，refresh 時呼叫 `LineLoginService.refresh_tokens()`、登出呼叫 `revoke_token()`；LIFF 沒有 LINE refresh token，只做自家輪替。鎖定帳號會 `revoke_all()`。任何簽發 token 的地方都要走這個 Service，不要直接 `jwt.issue_pair()`。
- **Redis**：設定 `REDIS_URL` 後容器改用 `RedisOAuthStateStore` 與 `RedisRateLimiter`（啟動時 ping，連不上直接失敗）；未設定用記憶體版，只適合單程序。
- **外鍵**：`users` / `handoff_tickets` / `patient_profiles.preferred_hospital_id` / `audit_logs` 的 `hospital_id` 以 SET NULL 指向 `hospitals`，`patient_records.hospital_id` 為 RESTRICT。migration 會先為孤兒代碼建立停用的佔位院所再加外鍵。
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
  藥局 / 藥品是唯讀參考資料，由 SUPER_ADMIN 在後台「院所設定 → 參考資料」上傳 CSV（`POST /api/v1/admin/pharmacies/import`、
  `/drugs/import`，權限 `REFERENCE_DATA_MANAGE`）或 CLI 匯入（`PharmacyService.import_pharmacies / import_drugs`，upsert 可重跑）；
  `MAX_CONTENT_LENGTH` 因健保用藥 CSV 約 96MB 而設為 128MB。
- **提醒規則單一來源** `app/domain/reminder_rules.py`：頻率代碼（qd/bid/tid/qid/q12h/q8h/q6h/qw/hs/prn/other）× 飯前後 → 預設時間；
  LIFF `/liff/reminders` 也用 `public_rules()`。`REMINDER_MANAGE_OWN` 與 `PRESCRIPTION_MANAGE_OWN` 都在 `_PUBLIC`：
  提醒**不需**先驗證基本資料（`ReminderHandler.requires_verified = False`）。
- **推播** `ReminderService.run_due(now)`：由 `flask reminder run-due` + 外部 cron 每 5 分鐘執行；`reminder_deliveries`
  的 `UNIQUE(schedule_id, scheduled_for)` 保證同一時段只送一次，`REMINDER_WINDOW_MINUTES` 容許 cron 延遲；同一人同一批合併成一則 Flex
  （每顆藥有 已服用 / 30 分後 / 跳過 postback）；`users.push_enabled=False`（封鎖）略過，`FollowHandler` 重新加好友時恢復。
  慢箋：確認處方時算 `refill_reminder_on = issued_on + days_supply × refill_seq − REFILL_REMINDER_DAYS_BEFORE`，同一輪 cron 推播一次。
  所有 datetime 以 UTC 存，SQLite 讀回的 naive 值一律視為 UTC（`reminder_repository.as_utc`）。
- 尚未做：後台檢視病人處方 / 服藥紀錄；「根據症狀」的提醒（屬病況諮詢延伸）；藥袋藥錠外觀比對（`42_2.csv`）。

### AI 對話助理：RAG + 事件判斷 → 套用 LINE 模組（已實作，2026-09-27）

設計文件 `docs/ai-assistant-design.md`。AI 只用 OpenAI API（`OPENAI_API_KEY`），沒有其他後端。**所有自由文字**（含「找醫院」這類關鍵字）都先交給 AI 判斷事件，再套用既有模組回應；
程式裡不再寫規則式的意圖判斷。只有兩個例外不等模型：紅旗關鍵字 `EMERGENCY_KEYWORDS` 直接進緊急畫面（安全層），
「等藥名」狀態的文字當藥名查詢。Rich Menu / 按鈕 postback、圖片、位置訊息仍直接走模組。

- **LLM** `app/integrations/llm.py`：`OpenAiClient.complete_structured(prompt, response_model, system=, images=)`
  用 OpenAI Chat Completions 的 structured outputs 直接回 pydantic 實例；對話 `AI_MODEL`（預設 `gpt-4.1-mini`）、
  處方辨識 `OCR_MODEL`（預設 `gpt-4.1`），金鑰 `OPENAI_API_KEY`（正式環境必填，缺少啟動即失敗）。
  測試以 `tests/conftest.py` 的 `FakeLlmClient`（依關鍵字回固定決策，`decisions` / `default` / `error` 可改）替換
  `container.llm_client`。
- **檢索（RAG 的 R）= 混合檢索**：關鍵字（`app/domain/retrieval.py` 的 jieba 搜尋模式 + 字元 bigram + `CUSTOM_WORDS`
  → PostgreSQL tsvector 全文檢索，GIN 索引）+ 語意（`OpenAiClient.embed()`，`EMBEDDING_MODEL` 預設 `text-embedding-3-small`，
  pgvector `vector(1536)` + HNSW 索引），各取 20 個候選後 `rrf_fuse()` 合併、同一份文件最多 2 個片段
  （`KnowledgeService.retrieve()`、`SqlAlchemyKnowledgeRepository.search_keyword / search_vector`）。
  SQLite（測試）用記憶體 `BM25Index` 與 Python 餘弦；沒有向量或查詢向量失敗時只用關鍵字。片段用 `chunk_text()` 切約 400 字，
  `tokens` / `search_vector` / `embedding` 都存 DB；向量由 `flask knowledge embed` 補算（只算 NULL 的，可重跑）。
  SQLite 的 `embedding` 用 `JSON(none_as_null=True)`，否則 None 會存成 `'null'` 字串、`IS NULL` 濾不掉。
- **知識庫** `knowledge_documents` / `knowledge_chunks`（`app/models/knowledge.py`）：`hospital_id` 空 = 通用；院所文件只有
  常去院所 / 所屬院所是它的使用者檢索得到。`source`（檔名或網址）與 `content_hash` 去重，`meta` 存出處
  （`hospital_name` / `level` / `topics` / `forms`）。後台 `/admin/knowledge`（列表、編輯、Markdown 多檔匯入、試搜），
  API `/api/v1/admin/knowledge*`：讀取 = 院方角色，寫入 = `EDUCATION_CONTENT_MANAGE`（綁自己院所），通用文件只有 SUPER_ADMIN。
  種子 `data/knowledge/*.md`（front matter：title / category / tags / hospital_id），`flask knowledge import data/knowledge`。
  分類在 `KnowledgeService.CATEGORIES`。
- **衛教內文資料** `data/衛教內文資料/health_education_content.jsonl`（全台 129 家醫院官網衛教，35,784 篇，
  說明見該目錄 README）：`KnowledgeService.import_health_education()` 只收內文 ≥ 200 字、中文比例 ≥ 0.4、有 `topics`
  分類（`--all` 全收）、內文 SHA-1 不重複的文章，`clean_text()` 去掉 PDF 抽字的 NUL / 私有區符號，
  批次 500 篇一次 commit；一律通用文件、出處在 `meta`。目前匯入 13,742 篇 / 41,737 片段。只有 SUPER_ADMIN 能匯入（CLI）。
- **決策格式單一來源** `app/domain/assistant_schema.py`（`AssistantDecision`：event / risk / answer / slots / actions /
  citations / needs_clarification / handoff_reason）。模型只回這個結構，**不決定訊息長相**。
- **脈絡** `ConversationService.build_context()`：使用者狀態（綁定 / 住址）、最近對話（存 `conversation_state` store，
  key `<line_user_id>:history`，TTL `AI_HISTORY_TTL_SECONDS`）、知識片段（K1…）、即時資料（提到縣市 → 急診看板摘要，
  用既有快照不即時抓；像藥名的詞 → `PharmacyService.search_drugs`，只收名稱開頭相符的）。`SYSTEM_PROMPT` 規定：只能依片段
  逐條對照寫答案、有依據必標 citations、沒有依據要明說並建議找真人、先結論再 2–4 個重點、不診斷不開藥。
- **事件 → 模組** `AssistantHandler`（`intents/assistant.py`，`Intent.ASSISTANT`）：emergency → 緊急畫面；hospital →
  `FindHospitalHandler`（slots.city 有值直接 `action=area`）；medication → 藥名查詢 / 選單；pharmacy → 找藥局；
  reminder / discharge / human / intake → 對應模組；symptom / faq / smalltalk / unknown → answer + `actions` 變 Quick Reply
  （對應表在 `app/line/messages/assistant.py`）。answer 有引用（`decision.citations` → `decision.attach_sources(hits)` →
  `decision.sources`）時用 Flex：正文 + 免責 + 最多 3 個「參考來源」URI 按鈕（`hit_label()`：醫院《標題》，開原文網址）。模組回覆用 `BotContext.capture()` 收集後與 answer 合併成一次 reply。
  先顯示 loading animation（60 秒），reply token 過期改 push。AI 失敗（未啟用 / 逾時 / 格式錯）→ `TEXT_KEYWORDS` 關鍵字退路
  → 再不行回失敗訊息 + 找真人。
- **病況諮詢 / 衛教諮詢（Roadmap 第 2、3 項，2026-09-28 以最簡方式實作）**：都只是 `SYSTEM_PROMPT` 規則 + 脈絡，
  沒有新 Service。symptom：模型填 `slots.department` 與 `risk`（none / watch / urgent），watch 時 handler 補
  `find_clinic`（→ 找醫院模組 `action=clinics`）、urgent 補 119 + 找醫院。discharge：已驗證病人的 prompt 多一段
  【病人資料】（`patient_record_repo.latest_encounter()` 的科別 / 診斷 / 出院代碼 + 自填慢性病），問到照護時檢索補上診斷；
  模型有 answer 就直接回個人化衛教，answer 留空 → `DischargeHandler` 通用版；Rich Menu「我剛出院」已驗證也走
  `conversation.decide()`（`DischargeHandler.PERSONAL_QUESTION`），AI 失敗退回通用版。
  新增事件或動作：改 `assistant_schema.py` 的 Literal + `AssistantHandler._compose()` /
  `messages/assistant.py`，`tests/test_assistant.py` 對每種事件都有對應測試。

### 急診動態分流 / 找醫院（Roadmap 第 1 項，已實作）

設計文件 `docs/triage-design.md`。資料來源是健保署「急診即時訊息」API（`app/integrations/nhi_er.py`，公開、免金鑰，
全國 59 家重度級急救責任醫院，每 15 分鐘更新），**不做人工回報**。`flask triage poll` 由 cron 每 15 分鐘執行
（無 cron 可 `--loop`）：抓取 → `er_snapshots`（UNIQUE(醫院, 回報時間)，重跑不重複）→ 清除超過 `TRIAGE_RETENTION_DAYS` 的快照
→ `TriageService.evaluate()` 逐縣市判斷 → `triage_advisories` 開立 / 更新 / 解除 → 通知 `triage_subscriptions`。

- **規則單一來源** `app/domain/triage_rules.py`：地區代碼 `AREA_NAMES`（臺 → 台）、鄰近縣市 `ADJACENT_AREAS`、
  依層級的等住院門檻、等 ICU / 等看診門檻、`congestion_level()` / `congestion_score()` / `decorate()`。
  **推床數（`wait_bed`）不列入判斷**。119 滿床通報一律「壅塞」。`public_rules()` 供 LIFF / 後台顯示門檻。
- **分流建議以縣市為單位**：該縣市任一家「壅塞」→ 一筆 open advisory（同縣市同時只有一筆），`alternatives` 依
  同縣市有餘裕 → 同縣市偏忙 → 鄰近縣市有餘裕排序；`report` 是純文字報告。再通知條件：新增壅塞醫院或距上次通知超過
  `TRIAGE_RENOTIFY_MINUTES`；解除時也通知。醫院停止回報超過 2 × `TRIAGE_STALE_MINUTES` 的舊快照不當現況。
- **接收者（訂閱）** 三種：`line`（推播給已加好友的 LINE 使用者）、`webhook`（POST JSON + `X-MediAgent-Signature`
  HMAC-SHA256）、`api`（用 `X-Api-Key` 主動拉 `GET /api/v1/triage/advisories`，不推送）。secret 用 `FieldCipher` 加密存、
  另存 fingerprint 供查找，只在建立時回一次。權限 `TRIAGE_SUBSCRIPTION_MANAGE`（HOSPITAL_ADMIN / SUPER_ADMIN），
  訂閱的 `hospital_id` = 建立者院所，`AccessPolicy.hospital_scope()` 隔離；身份由建立者線下核對，不新增角色。
  `TRIAGE_READ`（所有院方角色）看後台 `/admin/triage`（看板、建議、接收者、「立即抓取並評估」）。
- **一般使用者**：`FindHospitalHandler`（`intents/hospital.py`）。**以使用者位置為準**：LINE 位置訊息自帶經緯度
  （`MessageHandler` 依 `AWAIT_LOCATION` `for=hospital` 轉來，params 帶 `address` / `lat` / `lng`；`PENDING_LOCATION` 也存座標）
  → `TriageService.board_for_location(lat, lng)` 純依距離列最近 8 家（`distance_km`）、`recommended` = 最近且未壅塞的一家，
  回 Flex + 該醫院的 `LocationMessage`（可導航）；院所主檔沒座標時退回縣市看板 `board(area)`（選縣市 4 張 Flex / 基本資料住址也走這條）。
  「非急症：找附近診所」`nearby_clinics(city, district, lat, lng)`：有座標 → 10 km 內依距離，否則 `hospitals` 主檔
  （`capacity_tier` clinic / district / regional）依縣市 + 鄉鎮市區（`HospitalRepository.search_nearby()`）。
  `emergency=1`（紅旗）改急救文案並加撥打 119。距離用 `app/utils/geo.py` 的 Haversine，不用地圖服務。
- **院所座標** `hospitals.lat / lng`：種子檔 `data/er_hospitals.csv`（59 家重度級醫院，OSM Nominatim 一次性查詢 + 人工校對），
  `flask hospital import data/er_hospitals.csv` 匯入；匯入 CSV / 後台 PATCH 都接受 `lat,lng`（`parse_coords` 驗證）。
  分流建議的 `alternatives` 在壅塞醫院有座標時改依「離壅塞醫院最近」（50 km 內）排序並在報告標示距離。
  LIFF `/liff/er?area=01`（或 `?lat=&lng=`）是**不需登入**的完整看板，「用我的位置」用瀏覽器定位，
  打公開的 `GET /api/v1/triage/board[?lat=&lng=]`（rate limit）。
- 查詢時快照超過 `TRIAGE_STALE_MINUTES` 會即時 `ingest()` 一次，但不評估、不通知（只在 cron 內做）。
  API 的 `hosP_ID` 就是健保醫事機構代碼，`hospitals.id` 相同時看板帶出地址 / 電話。
- 測試以 `tests/conftest.py` 的 `FakeErClient`（`payload` 可改）/ `FakeWebhookClient` 替換
  `container.er_client` / `container.webhook_client`。

### LINE 層慣例

- Postback data 統一用 querystring `intent=<Intent>&key=value`，以 `postback_data()` 產生、`IntentRequest.from_postback()` 解析。文字關鍵字對照在 `app/line/intents.py` 的 `TEXT_KEYWORDS`，紅旗關鍵字 `EMERGENCY_KEYWORDS` 命中直接導向緊急畫面。
- 新增意圖：加 `Intent` 列舉值 → 繼承 `IntentHandler`（設 `intent`、`required_permission`、`requires_verified`）→ 在 `IntentRouter.default()` 註冊。新增事件類型：繼承 `EventHandler` 設 `event_type` → 在 `EventDispatcher.default()` 註冊。
- 訊息文案集中在 `app/line/messages/`，透過 `MessageBuilder` 組 Flex / QuickReply；`BotContext.reply()` 自動截到 LINE 上限 5 則。
- SDK 事件模型是 pydantic v1：`FollowEvent` 需要 `follow.isUnblocked`，測試 payload 缺欄位會被解析成 `UnknownEvent` 而靜默略過。

### 錯誤與回應

業務錯誤一律 raise `app/core/exceptions.py` 的 `AppError` 子類（`ValidationError` 422、`AuthenticationError` 401、`AuthorizationError` 403、`ConflictError` 409、`ExternalServiceError` 502），由 `register_error_handlers` 統一處理：`/api/`、`/line/` 路徑或 Accept 偏好 JSON 的請求回 `{"success": false, "error": {...}}`，其他瀏覽器請求 render `app/templates/errors/error.html`（文案依狀態碼查 `ERROR_PAGE_TEXT`，500 不外露例外內容）；成功用 `app/core/responses.py` 的 `ok()`。

## 規劃中功能（Roadmap）— 技術設計

以下 6 項功能是下一階段要擴充的範圍。**第 6 項（病人表單）、第 4、5 項（藥物諮詢 / 服藥小幫手，見 `docs/medication-design.md`）與第 1 項的 `Hospital` 實體已實作**，其餘尚未實作，先寫成技術設計文件讓使用者確認方向是否正確，確認後再逐項排入開發。每項的格式一致：功能敘述 → 現況對應 → 資料模型設計 → 服務/API 設計 → 與既有架構整合點 → 外部依賴 → 待確認問題。

### 1. 急診動態分流（已實作，2026-09-26）

實作內容見上方「急診動態分流 / 找醫院」與 `docs/triage-design.md`；以下為原始規劃，保留供對照。
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
- 排程執行方式建議走 Flask CLI command（例如 `flask reminder run-due`，沿用專案既有的 `richmenu`/`rbac`/`user` CLI 慣例）搭配外部 cron，**不建議**引入 Celery/Redis 這類新的 broker 依賴 — 符合專案目前輕量、依賴少的風格，第 3 項「衛教諮詢」的每日任務推播可以共用同一套排程基礎設施。
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
- `docs/patient-form-design.md`：病人自填表 / 院方紀錄表的欄位、資料模型、API 與後台設計（Roadmap 第 6 項，已實作）。
- `docs/medication-design.md`：藥物諮詢（處方箋拍照辨識 → 找藥局）與服藥小幫手（排程提醒推播）的設計與實作決策（Roadmap 第 4、5 項，已實作）。
- `docs/ai-assistant-design.md`：AI 對話助理（jieba + BM25 檢索、OpenAI 判斷事件、套用 LINE 模組）的設計與決策（已實作）。
- `docs/triage-design.md`：急診動態分流（健保署急診即時 API 定時抓取 → 壅塞評估 → 分流建議通知 119 / 診所；LINE 找醫院看板）（Roadmap 第 1 項，已實作）。
