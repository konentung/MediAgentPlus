# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概要

MediAgent+ 醫療照護 LINE 機器人後端。Python 3.11、Flask 3、line-bot-sdk 3.25.0（`linebot.v3`）、PostgreSQL（psycopg 3）。沒有獨立前端專案（no SPA），需要網頁畫面的地方（目前是 LINE Login callback）直接用 Flask 內建的 Jinja2 render `app/templates/` 下的模板；LIFF 頁面之後也會走同一套模式。
業務流程依據「服務地圖」設計：加好友 → Rich Menu 六格 → 依「基本資料已填且驗證通過？」分流。LIFF 綁定流程 B1–B9 **尚未設計**（曾有一版狀態機實作，已整個移除，待流程定案後重做）。對照表在 `docs/architecture.md`。

## 常用指令

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                   # 全部測試，不需要資料庫
pytest tests/test_webhook.py -v          # 單一檔案
pytest tests/test_rbac.py::test_hospital_scope   # 單一測試
pytest -p no:warnings                    # 隱藏 PyJWT 短金鑰警告（測試用金鑰刻意很短）

ruff check . && ruff format .            # lint + 格式化，行長 100，tests/ 不檢查 E501

./scripts/run_dev.sh                     # flask --app wsgi run --debug
flask --app wsgi routes                  # 列出路由
flask --app wsgi rbac show               # 角色權限矩陣
flask --app wsgi richmenu setup --image <png>   # 建立並設為預設 Rich Menu（2500×1686）
flask --app wsgi user set-role <user_id> <role> # 以 SUPER_ADMIN 身分指派角色

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

`Principal.is_verified`（`BindingStatus.VERIFIED`）就是服務地圖上「基本資料已填且驗證通過？」菱形的判斷；`IntentHandler.requires_verified = True` 與 `@require_verified()` 都以此為準。目前沒有任何程式碼會把 `binding_status` 改成 `VERIFIED`（綁定流程未設計），本機測試個人化功能請直接改 DB 或用 `container.user_repo.update()`。

### 依賴注入：`ServiceContainer`（`app/core/container.py`）

所有 Repository、Service、`JwtService`、`LineLoginService`、`LiffAuthService`、`LineMessagingClient` 在 `ServiceContainer.build(app)` 一次組裝，掛在 `app.extensions["container"]`，透過 `get_container()` 取用。測試以 `app.extensions["container"].line_client = FakeLineClient()` 替換元件（見 `tests/conftest.py`），任何對外呼叫都必須經過容器內的物件，不要直接 new SDK client。

### 資料層：PostgreSQL（SQLAlchemy + Flask-Migrate）

`User`、`HandoffTicket` 都有對應的 SQLAlchemy 模型（`app/models/user.py`、`app/models/handoff.py`，皆繼承 `db.Model, TimestampMixin`），`app/repositories/*.py` 裡的 `SqlAlchemy*Repository` 實作對應 Protocol，`ServiceContainer.build()` 組裝時直接用這些實作 —— Service / API 層完全不知道底層是 DB 還是記憶體。`InMemory*Repository` 仍保留在同一檔案內（未被使用），方便之後需要繞過 DB 的情境。`OAuthStateStore`（`app/auth/state_store.py`）刻意維持 in-memory，正式多 worker 部署時需換 Redis 實作。

Enum 欄位（`Role`/`BindingStatus`）一律用 `sa.Enum(..., native_enum=False)` 存成 VARCHAR，不用原生 Postgres enum type —— 這樣新增列舉值（例如未來新角色）只要改 Python enum + migration 加欄位資料，不必下 `ALTER TYPE`。除了 `Role`（會持續變動，刻意不加 CHECK constraint）之外都保留 CHECK constraint。之後若有 JSON 欄位，用 `JSONB().with_variant(JSON(), "sqlite")` 讓測試環境的 SQLite 也能跑。`hospital_id` 目前在 `User`/`HandoffTicket` 兩張表上都只是沒有外鍵的字串欄位 —— 專案裡還沒有 `Hospital` 實體，這是「急診動態分流」功能規劃時才會補上的擴充點（見下方規劃中功能）。

改動模型後：`flask --app wsgi db migrate -m "..."` 產生 migration（需要連得到 `DATABASE_URL` 指到的資料庫，本機用 `./scripts/setup_db.sh` 建立）→ **人工檢查**產生的檔案（autogenerate 對 Enum/JSONB 這類型別不一定抓得對）→ `flask --app wsgi db upgrade`。`pytest` 全程走 in-memory SQLite（`TestingConfig`，`StaticPool` 讓整個 engine 共用一條連線），不需要真的 PostgreSQL，`tests/conftest.py` 的 `app`/`client` fixture 會在每次測試維持一個 app context（供測試直接呼叫 repository）但在每次送出 HTTP 請求時暫時 pop/push，避免 Flask 沿用同一個 app context 造成 `flask.g`（含快取的 `Principal`）跨請求殘留。

### 綁定流程（B1–B9）尚未設計

`users.binding_status` 只是狀態欄位，沒有對應的流程程式碼。曾經實作過一版「LIFF 多步驟表單 + 院方識別名單比對」的狀態機（含 `binding_sessions`、`roster_entries` 兩張表與 `BindingService`），因為流程方向未定已整個移除；重新設計時可從 git 歷史找回參考，但不要假設既有結構。

### LINE 層慣例

- Postback data 統一用 querystring `intent=<Intent>&key=value`，以 `postback_data()` 產生、`IntentRequest.from_postback()` 解析。文字關鍵字對照在 `app/line/intents.py` 的 `TEXT_KEYWORDS`，紅旗關鍵字 `EMERGENCY_KEYWORDS` 命中直接導向緊急畫面。
- 新增意圖：加 `Intent` 列舉值 → 繼承 `IntentHandler`（設 `intent`、`required_permission`、`requires_verified`）→ 在 `IntentRouter.default()` 註冊。新增事件類型：繼承 `EventHandler` 設 `event_type` → 在 `EventDispatcher.default()` 註冊。
- 訊息文案集中在 `app/line/messages/`，透過 `MessageBuilder` 組 Flex / QuickReply；`BotContext.reply()` 自動截到 LINE 上限 5 則。
- SDK 事件模型是 pydantic v1：`FollowEvent` 需要 `follow.isUnblocked`，測試 payload 缺欄位會被解析成 `UnknownEvent` 而靜默略過。

### 錯誤與回應

業務錯誤一律 raise `app/core/exceptions.py` 的 `AppError` 子類（`ValidationError` 422、`AuthenticationError` 401、`AuthorizationError` 403、`ConflictError` 409、`ExternalServiceError` 502），由 `register_error_handlers` 統一處理：`/api/`、`/line/` 路徑或 Accept 偏好 JSON 的請求回 `{"success": false, "error": {...}}`，其他瀏覽器請求 render `app/templates/errors/error.html`（文案依狀態碼查 `ERROR_PAGE_TEXT`，500 不外露例外內容）；成功用 `app/core/responses.py` 的 `ok()`。

## 規劃中功能（Roadmap）— 技術設計

以下 6 項功能是下一階段要擴充的範圍，**目前都尚未實作**，先寫成技術設計文件讓使用者確認方向是否正確，確認後再逐項排入開發。每項的格式一致：功能敘述 → 現況對應 → 資料模型設計 → 服務/API 設計 → 與既有架構整合點 → 外部依賴 → 待確認問題。

### 1. 急診動態分流

**功能敘述**：當偵測到某區一家醫院陷入壅塞狀態，自動生成分流建議報告，透過 API（或 LINE）發送給該區 119 勤務指揮中心或基層診所醫師，建議暫時將病患轉往仍有餘裕的中大型醫院。

**現況對應**：全新領域。目前整個專案沒有「急診」「分流」相關程式碼，也沒有 `Hospital` 實體 —— `hospital_id` 在所有既有的表（`User`/`HandoffTicket`）都只是不驗證的字串。這是唯一需要先補上 `Hospital` 實體的功能。

**資料模型設計**：
- `Hospital`（`id`、`name`、`region`、`capacity_tier`、`contact_phone`、`lat`/`lng`）— 其餘表的 `hospital_id` 之後可考慮補上外鍵指向這張表（現有資料需先清洗確保值域一致）。
- `HospitalCongestionSnapshot`（`hospital_id` FK、`reported_at`、`occupancy_level`：normal/busy/critical、`source`：his_api/manual、`note`）— 壅塞狀況的時序紀錄。

**服務/API 設計**：
- `TriageAdvisoryService`：定期（cron 或排程 job）評估某區域內醫院的最新 `HospitalCongestionSnapshot`，若超過閾值，比對同區其他有餘裕的醫院，產生分流建議。
- 送達管道（a）LINE push 給訂閱的帳號（需要新角色，例如 `DISPATCH_OPERATOR`，訂閱名單需要驗證身份，不能讓一般使用者訂閱）；（b）`GET /api/v1/admin/triage/advisories` 給外部系統（119 調度系統）主動 pull。
- 壅塞資料寫入：`POST /api/v1/admin/hospitals/{id}/congestion`（`HOSPITAL_ADMIN`/`MEDICAL_STAFF` 權限）供人工回報，或未來串 HIS API。

**與既有架構整合點**：沿用 `app/domain/rbac.py` 的 `Permission`/`Role` 模式新增權限；`AccessPolicy.can_access_hospital()` 的院所隔離邏輯可直接套用在壅塞回報 API 上。

**外部依賴**：無（除非要串接真實 HIS 系統 API）。

**待確認問題**：壅塞資料來源（HIS API？人工回報？兩者都要？）；119/診所帳號的訂閱名單怎麼建立與驗證身份；觸發分流建議的閾值怎麼定義（單一醫院層級？區域層級？）。

### 2. 病況諮詢

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

### 3. 衛教諮詢

**功能敘述**：針對不同醫院及不同病因出院的患者，提供 Agentic AI 的衛教資訊，讓病人可以即時知道自己需要做什麼樣的動作。

**現況對應**：延伸既有 `Intent.DISCHARGE` / `app/line/handlers/intents/discharge.py`（stub 已經依 `Principal.is_verified` 區分「已驗證→個人化」vs「未驗證→通用版」，TODO 明確寫著「個人化衛教 + 每日任務推播」尚未實作）。病人的 `discharge_code` 來源依賴綁定流程的設計（目前未定）。

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

**功能敘述**：根據醫生開的處方簽給予夠買藥物的地點資訊。

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

### 5. 服藥小幫手

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

**現況對應**：`app/api/v1/liff.py` 目前只有登入與 `/me`、`/profile`，沒有任何表單端點；綁定流程尚未設計。

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

---

以上皆為規劃階段的技術設計文件，**尚未實作**；待使用者逐項確認方向後，再個別排入實作。

## 文件

- `README.md`：端點表、角色摘要、資料庫現況。
- `docs/local-operations.md`：本機安裝（pgAdmin 為主）、LINE Developers Console 設定、ngrok、curl 手動測試、常見問題。
- `docs/architecture.md`：服務地圖節點與程式位置對照、狀態機圖、擴充點。
