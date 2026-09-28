# MediAgent+ 後端

醫療照護 LINE 機器人後端：Flask + line-bot-sdk 3.25.0 + PostgreSQL，Python 3.11。

## 快速開始

### 1. 安裝 Python 3.11 與 PostgreSQL

```bash
# macOS
brew install python@3.11 postgresql@16
brew install --cask pgadmin4
brew services start postgresql@16

# Ubuntu / Debian
sudo apt install python3.11 python3.11-venv postgresql
sudo systemctl start postgresql
```

### 2. 建立資料庫與虛擬環境

用 pgAdmin 4 連到 `localhost:5432`，建立登入角色 `mediagent`（密碼 `mediagent`）與資料庫 `mediagent`，
步驟見 [docs/local-operations.md](docs/local-operations.md) 第 3 節。不想開 GUI 可改用 `./scripts/setup_db.sh`。

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                # 填入 LINE / DB / JWT 設定
```

### 3. 執行

```bash
./scripts/run_dev.sh                # 開發模式，http://localhost:5000
./scripts/run_prod.sh               # 正式模式（gunicorn，需 FLASK_ENV=production 的必要環境變數）
pytest                              # 測試（不需要 DB）
ruff check . && ruff format .
```

Webhook URL 設定為 `https://<your-domain>/line/webhook`（本機開發可用 ngrok 對外）。
Rich Menu 六格：`flask --app wsgi richmenu setup --image ./assets/richmenu.png`（2500×1686）。

更完整的本機安裝、LINE 後台設定、ngrok、手動測試與常見問題請看 [docs/local-operations.md](docs/local-operations.md)。

## 目錄結構

```
config/settings.py         環境設定（Development / Testing / Production）
wsgi.py                    gunicorn / flask CLI 進入點
scripts/                   setup_db.sh / run_dev.sh / run_prod.sh
app/__init__.py            create_app 應用程式工廠
app/extensions.py          SQLAlchemy / Migrate
app/cli.py                 flask richmenu / rbac / user / hospital / pharmacy / drug / reminder / triage / knowledge / assistant 指令
app/core/                  例外、統一回應、錯誤處理、DI 容器
app/domain/                Role / Permission / BindingStatus / Intent、Principal、AccessPolicy、
                           intake_schema（病人自填表欄位定義與驗證）、reminder_rules（頻率 → 提醒時間）、
                           triage_rules（急診壅塞燈號門檻、地區代碼、鄰近縣市）、retrieval（jieba + BM25）、
                           assistant_schema（AI 決策格式）
app/auth/                  JWT、LINE Login OAuth、LIFF 驗證、授權裝飾器、OAuth state
app/repositories/          Repository Protocol，User / Handoff / Hospital / Patient / PatientRecord /
                           AuthSession / Audit / Prescription / Pharmacy / Drug / Schedule / Delivery 皆為 SQLAlchemy 實作
app/services/              UserService / HandoffService / HospitalService / IntakeService / PatientRecordService /
                           PharmacyService / PrescriptionService / ReminderService / TriageService /
                           KnowledgeService（RAG 知識庫）/ ConversationService（AI 脈絡與決策）
app/integrations/vision.py 處方箋照片辨識：提示詞與輸出模型，辨識交給 llm.py 的後端
app/integrations/nhi_er.py 健保署急診即時訊息 API 客戶端；webhook.py 分流建議對外推送（HMAC 簽章）
app/integrations/llm.py    OpenAI 結構化輸出客戶端，對話助理與處方辨識共用
data/knowledge/            AI 知識庫種子 Markdown（通用衛教、服務說明），`flask knowledge import` 匯入
data/衛教內文資料/          全台 129 家醫院官網衛教內文（JSONL），`flask knowledge import-health-education` 匯入後 `knowledge embed` 算向量
app/utils/crypto.py        FieldCipher：身分證字號欄位級加密（Fernet）與指紋雜湊
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages、Rich Menu、
                           state_store（對話狀態：等處方箋照片 / 等位置 / 等藥名）
app/api/v1/                REST API：health / auth / liff / admin / triage（急診看板、分流建議、接收者）/ knowledge（AI 知識庫）
app/pages.py               一般網頁（首頁 `/`）
app/liff_pages.py          LIFF 頁面：/liff/intake（病人自填表）、/liff/settings、/liff/prescriptions/<id>、/liff/reminders、
                           /liff/er（急診即時看板，不需登入）
app/admin_pages.py         院方後台頁面：/admin/*（儀表板、病人、工單、人員、急診動態分流、AI 知識庫、設定）
app/models/                SQLAlchemy 模型：Hospital / User / HandoffTicket / Patient*（自填資料 7 張）/
                           PatientRecord*（院方紀錄 6 張）/ medication（處方、藥局、藥品、提醒排程、推播紀錄）/
                           triage（急診快照、分流建議、接收者訂閱）/ knowledge（AI 知識庫文件與片段），
                           migrations/ 由 Flask-Migrate 管理
app/templates/             Jinja 頁面（無獨立前端專案）：base.html、liff/、admin/、auth/、errors/
tests/                     pytest（RBAC、JWT、OAuth、LIFF、webhook、intake、後台、藥局、處方、提醒、急診分流、AI 助理）
```

## 請求生命週期

**LINE Webhook**：`POST /line/webhook` → `WebhookParser` 驗簽 → `EventDispatcher` 依事件類型找 `EventHandler`
→ 建立 `BotContext`（含 `Principal`）→ postback 交給 `IntentRouter`；**自由文字一律先交給 AI 助理**
（`AssistantHandler`：關鍵字 + 語意混合檢索知識庫 → OpenAI 依片段生成回答並判斷事件 → 套用對應模組，回答附原文來源），紅旗關鍵字例外直接進緊急畫面
→ `IntentHandler.execute()` 先檢查鎖定 / 綁定 / 權限，再呼叫 `handle()`。

**API**：`Authorization: Bearer <JWT>` → `current_principal()` 由 DB 重新載入角色與綁定狀態
→ `@require_auth / @require_permission / @require_role / @require_verified` 閘門 → view。

## 端點

| Method | Path | 權限 | 說明 |
|---|---|---|---|
| GET | / | 公開 | 首頁（`app/templates/home.html`），顯示 LINE 登入按鈕或登入者資訊 |
| GET | /api/v1/health | 公開 | 健康檢查 |
| GET | /api/v1/auth/line/login | 公開 | 302 → LINE 授權頁 |
| GET | /api/v1/auth/line/callback | 公開 | 換發 JWT，render 登入成功頁（`app/templates/auth/callback.html`） |
| POST | /api/v1/auth/refresh | 公開 | 輪替 refresh token（舊的作廢；網頁版登入會同步向 LINE 換新 token） |
| POST | /api/v1/auth/logout | 公開 | 撤銷本次登入的所有 token，並向 LINE 撤銷 access / refresh token |
| GET | /api/v1/auth/me | 登入 | 身分 + 權限清單 |
| GET | /api/v1/liff/config | 公開 | 回傳 LIFF ID |
| POST | /api/v1/liff/auth | 公開 | id_token / access_token → JWT |
| GET | /api/v1/liff/me | 登入 | 身分 |
| GET | /api/v1/liff/hospitals | 公開 | 啟用中的院所清單（表單「常去的醫院 / 診所」選單） |
| GET | /api/v1/liff/intake | intake:write_own | 表單 schema + 已填資料（身分證遮罩）+ 進度 |
| PUT | /api/v1/liff/intake/steps/{n} | intake:write_own | 存單一步驟；body `partial: true` 為草稿 |
| POST | /api/v1/liff/intake/submit | intake:write_own | 全表驗證 → 快照 → `binding_status=PENDING` |
| GET | /api/v1/liff/profile | 已驗證 + profile:read_own | 病人自己的基本資料 |
| GET | /api/v1/liff/prescriptions[/{id}] | prescription:manage_own | 自己的處方列表 / 單張（含辨識出的藥品清單、warnings；不含 ocr_raw） |
| GET | /api/v1/liff/prescriptions/{id}/image | prescription:manage_own | 原始處方箋照片（解密後回傳） |
| PATCH | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 修改院所 / 日期 / 天數 / 慢箋次數 / 藥品清單 |
| POST | /api/v1/liff/prescriptions/{id}/confirm | prescription:manage_own | `draft` → `confirmed`，並算出慢箋領藥提醒日 |
| DELETE | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 標記 `discarded` |
| POST | /api/v1/liff/prescriptions/{id}/schedules/propose | reminder:manage_own | 依處方各項頻率算出預設提醒時間（不寫入） |
| GET | /api/v1/liff/pharmacies?address= | consult:medication_once | 依地址的縣市 / 鄉鎮市區列健保特約藥局（含今日營業時段） |
| GET | /api/v1/liff/drugs?q= | consult:medication_once | 健保用藥查詢（中英文名 / 成分） |
| GET / POST | /api/v1/liff/reminders | reminder:manage_own | 我的提醒排程（含今日推播狀態）/ 建立多筆 |
| PATCH / DELETE | /api/v1/liff/reminders/{id} | reminder:manage_own | 改時間 / 結束日 / 暫停 / 刪除 |
| GET | /api/v1/liff/reminders/history?days=7 | reminder:manage_own | 推播與回報紀錄 |
| GET | /api/v1/triage/board?area=01 或 ?lat=&lng= | 公開（rate limit） | 急診即時狀況 + 燈號：帶經緯度時依距離列最近 8 家並推薦最近未壅塞者（`recommended`），否則某縣市 / 全國依餘裕排序 |
| GET | /api/v1/triage/areas | 公開 | 地區代碼與燈號門檻說明 |
| GET | /api/v1/triage/advisories | `X-Api-Key` | 外部系統（119）拉取進行中的分流建議（key = 後台建立 api / webhook 接收者時的 secret） |
| GET | /api/v1/admin/triage/board、/stats、/advisories?status=open/all、/hospitals/{nhi_id}/history?hours= | triage:read | 後台看板、統計、分流建議、單一醫院 24 小時歷史 |
| POST | /api/v1/admin/triage/poll | triage:subscriptions | 立即抓取健保署資料並評估（等同 cron 一次） |
| GET / POST | /api/v1/admin/triage/subscriptions | triage:subscriptions | 分流建議接收者（line / webhook / api），同院所隔離；webhook / api 的 secret 只在建立時回一次 |
| PATCH / DELETE | /api/v1/admin/triage/subscriptions/{id} | triage:subscriptions | 改名稱 / 縣市 / 啟用，刪除 |
| POST | /api/v1/admin/triage/subscriptions/{id}/test | triage:subscriptions | 送測試通知 |
| GET | /api/v1/admin/knowledge[/{id}]、/search?q= | 院方角色 | AI 知識庫文件（通用 + 本院）、試搜 |
| POST / PATCH / DELETE | /api/v1/admin/knowledge[/{id}] | education:content_manage | 新增 / 修改 / 刪除文件（院所綁自己；通用只有 SUPER_ADMIN） |
| POST | /api/v1/admin/knowledge/import | education:content_manage | 上傳多個 Markdown（multipart `files`），同檔名更新 |
| GET | /api/v1/admin/permissions | 院方角色 | 權限矩陣 |
| GET | /api/v1/admin/dashboard | patient:read | 待核對數、到期追蹤、開放工單 |
| GET | /api/v1/admin/hospitals | 院方角色 | 院所清單（SUPER_ADMIN 帶 `?all=1` 含停用） |
| POST | /api/v1/admin/hospitals | hospital:manage | 新增院所（`id` 即院所代碼，建立後不可改） |
| POST | /api/v1/admin/hospitals/import | hospital:manage | CSV 批次匯入（multipart `file` 或 JSON `rows`），同代碼更新、否則新增；`?only_hospitals=1` 略過診所 |
| GET | /api/v1/admin/hospitals/import/template | hospital:manage | 下載匯入範本 CSV |
| PATCH | /api/v1/admin/hospitals/{id} | hospital:settings / hospital:manage | 修改院所資料；`active` 只有 SUPER_ADMIN 能改 |
| GET | /api/v1/admin/reference-data | reference_data:manage | 藥局 / 藥品資料筆數 |
| POST | /api/v1/admin/pharmacies/import | reference_data:manage | 藥局 CSV 匯入（健保署特約醫事機構或衛福部藥局格式，multipart `file` 或 JSON `rows`） |
| POST | /api/v1/admin/drugs/import | reference_data:manage | 健保用藥品項 CSV 匯入（同代號只留最新） |
| GET | /api/v1/admin/users | staff:manage | 人員列表（同院所） |
| PUT | /api/v1/admin/users/{id}/role | staff:manage | 指派角色（同院所限制） |
| GET | /api/v1/admin/handoffs | handoff:accept | 待處理工單 |
| POST | /api/v1/admin/handoffs/{id}/accept | handoff:accept | 接單 |
| GET | /api/v1/admin/patients | patient:read | 病人列表 `?status=pending/active/locked/all&q=` |
| GET | /api/v1/admin/patients/{user_id} | patient:read | 自填資料 + 本院紀錄 + 就診 / 風險 / 追蹤 / 備註 / 快照 |
| GET | /api/v1/admin/patients/{user_id}/national-id | patient:identity_read | 身分證明碼（寫入稽核、每人 10 分鐘 30 次） |
| POST | /api/v1/admin/patients/{user_id}/lock、/unlock | patient:verify | 鎖定 / 解除鎖定帳號（寫入稽核） |
| GET | /api/v1/admin/audit | audit:read | 稽核紀錄（同院所） |
| POST | /api/v1/admin/patients/{user_id}/verify | patient:verify | 核對身份 → 建檔 → VERIFIED + 角色 |
| POST | /api/v1/admin/patients/{user_id}/reject | patient:verify | 退回補正（推播原因） |
| PATCH | /api/v1/admin/patients/{user_id}/profile | patient:write | 院方修正自填資料（留 corrections） |
| POST / PATCH | /api/v1/admin/patients/{user_id}/encounters[/{id}] | encounter:manage | 就診 / 住院紀錄、出院代碼 |
| POST / PATCH | /api/v1/admin/patients/{user_id}/flags[/{id}] | patient:write | 風險警示 |
| POST / PATCH | /api/v1/admin/patients/{user_id}/follow-ups[/{id}] | patient:write | 追蹤事項 |
| POST | /api/v1/admin/patients/{user_id}/notes | patient:read | 內部備註 |

### 網頁

| Path | 說明 |
|---|---|
| / | 首頁：LINE 登入、進入基本資料 / 後台 |
| /liff/intake?hospital=<院所代碼> | 病人自填表（6 步、可續填）。**只在 LINE 內透過 LIFF 開啟**：`https://liff.line.me/<LIFF_ID>/intake?hospital=H1`，院所把這個 LIFF 連結做成 QR |
| /liff/settings | 病人設定頁（Rich Menu「設定」→ LIFF）：綁定狀態、資料摘要、進入表單 |
| /liff/prescriptions/{id} | 處方辨識結果：看原始照片、修改藥品清單、確認、進入建立提醒 |
| /liff/er?area=01 或 ?lat=&lng= | 急診即時看板（公開資料，不需登入，LINE 內外都能開）：「用我的位置」依距離推薦，或看各縣市燈號與等候人數 |
| /admin/triage | 院方後台：急診看板、分流建議（進行中 / 歷史）、接收者管理、立即抓取 |
| /admin/knowledge | 院方後台：AI 知識庫文件維護、Markdown 匯入、試搜 |
| /liff/reminders?prescription={id} | 服藥提醒總覽 / 新增 / 編輯；帶 `prescription` 會先載入該處方的建議排程 |
| /admin | 院方後台儀表板；未登入 / 過期 / 非院方帳號一律導向 `/admin/login` |
| /admin/login | 後台登入頁（「使用 LINE 登入」按鈕），`?next=` 登入後回原頁、`?error=` 顯示原因。LIFF 端不走這頁 |
| /admin/patients、/admin/patients/{user_id} | 病人列表 / 詳情（核對、修正、就診、風險、追蹤、備註） |
| /admin/handoffs、/admin/staff | 工單、人員角色 |
| /admin/settings、/admin/permissions、/admin/audit | 院所設定（本院資料、填表連結、所有院所管理與匯入、藥局 / 藥品開放資料匯入）、權限矩陣查閱、稽核紀錄 |

後台頁面骨架不需登入即可開啟，頁內 JS 用 `sessionStorage` 的 JWT 打 API，真正的授權在 API 端。

## 安全機制摘要

- **身分證字號**：Fernet 加密儲存（`PATIENT_DATA_KEY`），另存末四碼與 HMAC 指紋（唯一索引）；API 一律回遮罩，明碼只有 `patient:identity_read` 可讀且寫入稽核。
- **院所隔離**：`AccessPolicy.hospital_scope()` 統一判斷，院方帳號沒有 `hospital_id` 直接拒絕（不會退化成看全部）。草稿對院所不可見，送出後才進待核對清單。
- **狀態機**：送出後病人端唯讀；退回清除送出時間，重新送出前院方不能核對；鎖定 / 解鎖只能由院方操作並留稽核。
- **Refresh token**：每個 refresh token 對應 `auth_sessions` 一列，每次 refresh 輪替（舊 token 作廢），舊 token 被重用時整個登入 family 撤銷；網頁版 LINE Login 會把 LINE 的 token 加密存起來，refresh 時向 LINE 換新、登出時向 LINE 撤銷、LINE 判定失效時自家 session 一併失效；帳號被鎖定時撤銷所有裝置。
- **Rate limit / OAuth state**：`app/core/ratelimit.py`、`app/auth/state_store.py`。未設 `REDIS_URL` 用單程序記憶體，設定後改用 Redis（多 worker / 多機共用）。
- **設定保護**：`FLASK_ENV` 未設定視為 production，缺必要環境變數或使用範例金鑰會啟動失敗；`TRUSTED_PROXY_COUNT` 設定信任的 proxy 層數才會採用 `X-Forwarded-*`。
- **回應標頭**：`X-Content-Type-Options: nosniff`、`Referrer-Policy`，後台加 `X-Frame-Options: DENY`。
- **錯誤處理**：500 不外露例外內容，log 只記型別與 traceback（避免 SQL 參數夾帶個資）。

## 角色與權限

`flask --app wsgi rbac show` 可輸出完整矩陣。摘要：

| 角色 | 說明 | 重點權限 |
|---|---|---|
| guest | 加好友但未驗證 | 病況諮詢、找醫院、藥物單次查詢、通用衛教、找真人 |
| patient / family | 驗證通過（本人 / 家屬） | guest 全部 + 個人化衛教、服藥提醒、個資讀寫 |
| cs_agent | 真人客服 | 接單回覆、對話讀取、病患讀取（身分證遮罩） |
| medical_staff | 醫護 / 衛教 | 病患讀取 / 修正 / 核對建檔、就診紀錄、衛教內容管理 |
| hospital_admin | 院所管理員 | cs_agent + medical_staff + 人員管理、院所設定、Rich Menu、稽核、身分證明碼 |
| super_admin | 系統管理員 | 全部 + 院所管理、系統管理 |

院所資料隔離由 `AccessPolicy.can_access_hospital()` 統一判斷；角色指派範圍由 `ROLE_ASSIGNABLE_BY` 限制。

## 資料庫

`Hospital`（`hospitals`，`id` 即院所代碼，其他表的 `hospital_id` 都是指向它的外鍵）、`User`、
`HandoffTicket`、`AuthSession`、`AuditLog`、病人自填資料（`patient_profiles` 等 7 張）、院方紀錄（`patient_records` 等 6 張）、
藥物諮詢 / 服藥小幫手（`prescriptions`、`pharmacies`、`drugs`、`medication_schedules`、`reminder_deliveries`）都是 SQLAlchemy 模型（`app/models/`），`ServiceContainer.build()` 用對應的
`SqlAlchemy*Repository` 讀寫 PostgreSQL，`migrations/` 由 Flask-Migrate 管理。身分證字號以
`PATIENT_DATA_KEY`（Fernet）加密存放，另存末四碼與 HMAC 指紋供搜尋與重複檢查。資料表設計見
`docs/patient-form-design.md`、`docs/medication-design.md`。`OAuthStateStore`、rate limit 與 LINE 對話狀態在未設 `REDIS_URL` 時用單程序記憶體，設定後改用 Redis
（`app/auth/state_store.py`、`app/core/ratelimit.py`、`app/line/state_store.py`）。

### 藥物諮詢 / 服藥小幫手

- 藥局與藥品是唯讀參考資料，系統管理員可在後台「院所設定 → 參考資料」上傳 CSV，或用 CLI 從 `data/pharmacy/` 匯入（可重跑，upsert）：
  `flask --app wsgi pharmacy import data/pharmacy/A21030000I-D21005-001.csv`（健保署特約藥局）、
  `flask --app wsgi pharmacy import data/pharmacy/35_2.csv`（衛福部藥局，只補非特約）、
  `flask --app wsgi drug import data/pharmacy/A21030000I-E41001-001.csv`（健保用藥）。
- 找藥局不用地圖服務：LINE 位置訊息自帶地址，解析縣市 / 鄉鎮市區後比對 `pharmacies.city / district`。
- 處方箋辨識與 AI 對話都走 **OpenAI API**（`OPENAI_API_KEY`；對話 `gpt-4.1-mini`、看圖 `gpt-4.1`，可用 `AI_MODEL` / `OCR_MODEL` 覆寫），透過 `app/integrations/llm.py` 的結構化輸出直接對到 pydantic 模型。
- AI 知識庫檢索 = PostgreSQL 全文檢索（jieba 斷詞 tsvector）+ pgvector 語意檢索（`EMBEDDING_MODEL` 預設 `text-embedding-3-small`）以 RRF 合併；migration 會 `CREATE EXTENSION vector`，本機 PostgreSQL 需已安裝 pgvector。
- 服藥提醒由外部 cron 每 5 分鐘執行 `flask --app wsgi reminder run-due`；同一排程同一時段只推播一次，
  同一人同一批多顆藥合併成一則，封鎖官方帳號（`users.push_enabled=false`）的人自動略過。

新增欄位或模型時：改 `app/models/`、跑 `flask --app wsgi db migrate -m "..."` 產生 migration、
人工檢查產生的檔案（尤其是 Enum / JSONB 型別是否正確）、再 `flask --app wsgi db upgrade`。
`pytest` 不需要真的 PostgreSQL，測試設定用 in-memory SQLite（`config/settings.py` 的 `TestingConfig`）。
