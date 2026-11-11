# MediAgent+ 後端

醫療照護 LINE 機器人後端。病人加 LINE 官方帳號好友後，透過 Rich Menu 六格功能（病況諮詢 / 找醫院 / 衛教諮詢 /
找藥局 / 服藥小幫手 / 問題回報）取得服務；院方人員用瀏覽器登入後台核對病人、維護衛教資料與參考資料。

技術組合：Python 3.11、Flask 3、line-bot-sdk 3.25.0（`linebot.v3`）、PostgreSQL 16 + pgvector、OpenAI API。
沒有獨立前端專案，所有網頁（LIFF 頁面、院方後台）都由 Flask 用 Jinja2 直接渲染。

## 目錄

1. [系統架構](#系統架構)
2. [安裝與執行步驟](#安裝與執行步驟)
3. [相依套件](#相依套件)
4. [模型與版本](#模型與版本)
5. [環境變數](#環境變數)
6. [API](#api)
7. [資料流程](#資料流程)
8. [角色與權限](#角色與權限)

---

## 系統架構

### 架構總覽

```
 LINE 使用者                         院方人員（瀏覽器）
   │  訊息 / postback / 圖片 / 位置     │  LINE Login → JWT
   ▼                                   ▼
 POST /line/webhook              /admin/*（Jinja 頁面）→ /api/v1/admin/*
   │ 驗簽後立刻回 200                   │
   ▼                                   ▼
 EventDispatcher → IntentRouter → IntentHandler ─┐
   （關鍵字 / postback 直接進功能，                │   Service 層（ServiceContainer 注入）
     自由文字交給 AI 助理判斷事件）                 ├──► UserService / IntakeService / PatientRecordService
                                                  │    PrescriptionService / ReminderService / TriageService
 LIFF 頁面（/liff/*）→ /api/v1/liff/* ────────────┘    KnowledgeService / ConversationService …
                                                  │
              ┌───────────────────────────────────┼──────────────────────────────┐
              ▼                                   ▼                              ▼
     PostgreSQL + pgvector                 外部服務                         APScheduler（程序內排程）
     （SQLAlchemy / Flask-Migrate）         LINE Messaging / LINE Login       每 5 分鐘：服藥提醒推播
                                           OpenAI（對話、處方辨識、向量）     每 15 分鐘：抓健保署急診即時資料
                                           健保署急診即時訊息 API
```

分層原則：

- **LINE 層**（`app/line/`）只負責解析事件、組訊息，不寫商業邏輯。
- **Service 層**（`app/services/`）放商業邏輯，透過 `ServiceContainer`（`app/core/container.py`）統一組裝與注入，
  所有對外呼叫（LINE、OpenAI、健保署 API）都經由容器內的物件，方便測試時替換成假物件。
- **Repository 層**（`app/repositories/`）包住 SQLAlchemy，Service 不知道底層是 PostgreSQL 還是測試用的 SQLite。
- **授權**集中在 `app/domain/rbac.py` 的 `AccessPolicy`，LINE 與 REST API 兩條入口共用同一套 `Principal` 與權限判斷。

### 目錄結構

```
config/settings.py         環境設定（Development / Testing / Production），啟動時讀取 .env
wsgi.py                    gunicorn / flask 進入點
app/__init__.py            create_app 應用程式工廠
app/extensions.py          SQLAlchemy / Migrate
app/scheduler.py           APScheduler 排程：服藥提醒推播（5 分鐘）、急診資料抓取（15 分鐘）
app/core/                  例外、統一回應、錯誤處理、分頁、rate limit、DI 容器
app/domain/                Role / Permission / BindingStatus / Intent、Principal、AccessPolicy、
                           intake_schema（病人自填表欄位定義與驗證）、reminder_rules（頻率 → 提醒時間）、
                           triage_rules（急診壅塞燈號門檻、地區代碼）、retrieval（切段、jieba 斷詞、BM25）、
                           assistant_schema（AI 決策格式）
app/auth/                  JWT（只有 access token）、LINE Login OAuth、LIFF 驗證、授權裝飾器、OAuth state
app/repositories/          Repository Protocol 與 SQLAlchemy 實作
app/services/              商業邏輯：使用者、問題回報、院所、病人自填表、院方紀錄、藥局、處方、提醒、
                           急診分流、衛教知識庫（含背景工作）、AI 對話脈絡與決策
app/integrations/llm.py    OpenAI 結構化輸出客戶端，對話助理、處方辨識、向量計算共用
app/integrations/vision.py 處方箋照片辨識的提示詞與輸出模型
app/integrations/nhi_er.py 健保署急診即時訊息 API 客戶端
app/utils/                 crypto（身分證指紋、照片加密）、clock（台灣時區）、geo（距離、鄉鎮市區中心點）、pdf（文字擷取）
app/resources/             離線資料：tw_districts.csv（368 個鄉鎮市區中心點）
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages（Flex 樣式）、
                           state_store（對話狀態）
app/api/v1/                REST API：health / auth / liff / liff_medication / admin / knowledge / triage
app/pages.py               首頁 /
app/liff_pages.py          LIFF 頁面 /liff/*
app/admin_pages.py         院方後台頁面 /admin/*
app/models/                SQLAlchemy 模型與 migrations/（Flask-Migrate）
app/templates/             Jinja 頁面：base.html、liff/、admin/、auth/、errors/
scripts/import_pdfs.py     不經瀏覽器、直接把資料夾裡的衛教 PDF 匯入知識庫
data/                      開放資料與衛教 PDF 原始檔（不進版控，本機自行放置）
tests/                     pytest，不需要資料庫與外部服務
```

### 請求生命週期

**LINE Webhook**：`POST /line/webhook` → `WebhookParser` 驗簽 → **立刻回 200，事件在背景執行緒處理**
（`WEBHOOK_ASYNC`；AI / 辨識流程常要 10 到 60 秒，同步處理會讓 LINE 判定失敗並重送）→ `EventDispatcher`
用 webhook event id 去重 → 依事件類型找 `EventHandler` → 建立 `BotContext`（含 `Principal`，首次接觸自動建立 GUEST 使用者）
→ postback、完全等於功能關鍵字的文字直接交給 `IntentRouter`（不經 AI）；
**其餘自由文字才交給 AI 助理**（混合檢索知識庫 → OpenAI 依片段生成回答並判斷事件 → 套用對應模組）
→ `IntentHandler.execute()` 先檢查鎖定 / 綁定 / 權限，再呼叫 `handle()`。

**REST API**：`Authorization: Bearer <JWT>` → `current_principal()` **每次都從資料庫重新載入**角色與綁定狀態
（token 內的角色不拿來判斷）→ `@require_auth / @require_permission / @require_role / @require_verified` → view。

---

## 安裝與執行步驟

### 1. 安裝 Python 3.11 與 PostgreSQL（含 pgvector）

```bash
# macOS
brew install python@3.11 postgresql@16 pgvector
brew install --cask pgadmin4
brew services start postgresql@16

# Ubuntu / Debian
sudo apt install python3.11 python3.11-venv postgresql postgresql-16-pgvector
sudo systemctl start postgresql
```

PostgreSQL 伺服器**必須先安裝 pgvector 擴充套件**，第一份 migration 會執行 `CREATE EXTENSION vector`。
pip 安裝的 `pgvector` 只是 Python 端的型別轉換，不能取代伺服器端的擴充套件。

### 2. 建立資料庫與虛擬環境

用 pgAdmin 4 連到 `localhost:5432`，建立一個登入角色與一個資料庫（名稱自訂，密碼請自行設定，不要用範例值），
再把連線字串填進 `.env` 的 `DATABASE_URL`。

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                # 依下方「環境變數」填入 LINE / 資料庫 / JWT / OpenAI 設定
```

`.env` 務必設 `FLASK_ENV=development`，否則會被視為正式環境而拒絕範例金鑰。

### 3. 建表與啟動

```bash
flask --app wsgi db upgrade                 # 套用 migration 建表
flask --app wsgi run --debug                # 開發模式，http://localhost:5000
gunicorn -w 1 -b 0.0.0.0:5000 wsgi:app      # 正式模式（FLASK_ENV=production；只支援單一 worker）
```

### 4. 串接 LINE

1. LINE Developers Console 的 Messaging API channel：Webhook URL 設為 `https://<你的網域>/line/webhook`
   （本機開發可用 ngrok 對外），並取得 channel secret / access token。
2. LINE Login channel：設定 callback `https://<你的網域>/api/v1/auth/line/callback`，底下建立 LIFF app，
   Endpoint URL 指向 `https://<你的網域>/liff`。
3. Rich Menu 在 LINE Official Account Manager 建立（2500×1686 六格），每格動作設「文字」，文字分別為：
   病況諮詢 / 找醫院 / 衛教諮詢 / 找藥局 / 服藥小幫手 / 問題回報。
4. 第一位系統管理員：把自己的 LINE userId 填進 `.env` 的 `SUPER_ADMIN_LINE_USER_IDS`，加好友或登入後台即自動升級，
   其他人員在後台「人員與角色」指派。

### 5. 匯入參考資料（後台「資料匯入」頁）

依序匯入：院所（健保署特約醫事機構 CSV → 衛福部醫療機構基本資料 CSV 補科別 → `er_hospitals.csv` 補急救責任醫院座標）、
藥局、藥品，再由各院所管理員上傳衛教 PDF。大量 PDF 也可用指令直接匯入：

```bash
python scripts/import_pdfs.py data --hospital <院所代碼> --department 心臟內科 --chunk-size medium
python scripts/import_pdfs.py data --no-embed    # 只切段不算向量（不呼叫 OpenAI）
```

### 6. 測試與格式檢查

```bash
pytest                                   # 全部測試，不需要資料庫、不會呼叫外部服務
pytest tests/test_webhook.py -v          # 單一檔案
pytest -p no:warnings                    # 隱藏測試用短金鑰的 PyJWT 警告
FREEZE_AT="2030-01-01 08:00:00" pytest   # 把「現在」凍結在未來，檢查有沒有會過期的測試
ruff check . && ruff format .            # lint 與格式化（行長 100）
flask --app wsgi routes                  # 列出所有路由
```

---

## 相依套件

執行環境：Python 3.11（`pyproject.toml` 的 `requires-python`），版本範圍以 `requirements.txt` 為準。

| 套件 | 版本範圍 | 用途 |
|---|---|---|
| Flask | >=3.1,<4 | Web 框架、Jinja2 模板 |
| line-bot-sdk | ==3.25.0 | LINE Messaging API（`linebot.v3`）：webhook 驗簽、reply / push、Flex、loading animation |
| Flask-SQLAlchemy | >=3.1,<4 | ORM 整合 |
| Flask-Migrate | >=4.1,<5 | 資料庫 migration（Alembic） |
| psycopg[binary] | >=3.2,<4 | PostgreSQL 驅動 |
| pgvector | >=0.3,<1 | SQLAlchemy 的 `vector` 型別（語意檢索） |
| PyJWT | >=2.9,<3 | 自家 API 的 access token、LIFF id_token 驗證 |
| cryptography | >=43,<46 | Fernet 加密處方箋照片、HMAC 身分證指紋 |
| requests | >=2.32,<3 | LINE Login token 交換、健保署急診 API |
| python-dotenv | >=1.0,<2 | 讀取 `.env` |
| gunicorn | >=23,<27 | 正式環境 WSGI 伺服器 |
| pydantic | >=2.6,<3 | AI 決策格式、處方辨識結果的結構化輸出 |
| openai | >=1.50,<4 | 對話、處方箋辨識、embedding |
| jieba | >=0.42,<1 | 中文斷詞（全文檢索 token 化、藥名比對） |
| APScheduler | >=3.10,<4 | 程序內排程（服藥提醒、急診資料抓取） |
| pypdf | >=6,<7 | 衛教 PDF 文字擷取 |

開發用（`requirements-dev.txt`）：pytest >=8、ruff >=0.6、mypy >=1.11、freezegun >=1.5。

外部服務：

| 服務 | 用途 | 需要金鑰 |
|---|---|---|
| LINE Messaging API | 接收訊息、回覆、推播 | 是（channel secret / access token） |
| LINE Login + LIFF | 後台登入、LIFF 頁面身分驗證 | 是（channel id / secret、LIFF ID） |
| OpenAI API | 對話助理、處方箋辨識、知識庫向量 | 是 |
| 健保署急診即時訊息 API | 59 家重度級急救責任醫院的急診壅塞資料 | 否（公開） |
| 政府開放資料 CSV | 院所、藥局、藥品主檔（後台匯入） | 否 |

---

## 模型與版本

| 用途 | 設定變數 | 預設模型 | 說明 |
|---|---|---|---|
| AI 對話助理（事件判斷 + 依知識片段回答） | `AI_MODEL` | `gpt-4.1-mini` | Chat Completions structured outputs，回 `AssistantDecision`；逾時 `AI_TIMEOUT_SECONDS`（60 秒） |
| 處方箋照片辨識 | `OCR_MODEL` | `gpt-4.1` | 看圖抄寫藥品清單，不推測、不輸出姓名與身分證；逾時 `OCR_TIMEOUT_SECONDS`（90 秒） |
| 知識庫語意檢索向量 | `EMBEDDING_MODEL` | `text-embedding-3-small` | 1536 維，存 pgvector `vector(1536)` + HNSW 索引；留空則只用關鍵字檢索 |
| 依症狀挑診所 | 同 `AI_MODEL` | `gpt-4.1-mini` | 把同鄉鎮市區的院所清單交給模型，只回編號 |

- 所有模型呼叫都走 `app/integrations/llm.py` 的 `OpenAiClient`，提示詞與輸出格式分別在
  `app/domain/assistant_schema.py`（對話）與 `app/integrations/vision.py`（辨識）。模型只回結構化資料，**不決定訊息長相**。
- 開發環境 `OPENAI_API_KEY` 留空時容器注入 `DisabledLlmClient`：AI 對話退回關鍵字比對、處方辨識回失敗訊息、
  語意檢索停用，程式仍可啟動。正式環境缺少金鑰會啟動失敗。
- 檢索為混合式：PostgreSQL 全文檢索（jieba 斷詞 + bigram → tsvector，GIN 索引）與 pgvector 語意檢索各取 10 個候選，
  再以 RRF 合併；病人所屬院所的內容與就診科別會加權但不硬過濾。
- 測試一律用 `tests/conftest.py` 的 `FakeLlmClient` / `FakeVisionClient`，`pytest` 不會呼叫 OpenAI。

執行環境版本：Python 3.11、PostgreSQL 16 + pgvector。

---

## 環境變數

複製 `.env.example` 為 `.env` 後填寫。`config/settings.py` 在啟動時以 `load_dotenv(override=True)` 讀取，
**`.env` 的值會蓋掉系統環境變數**，改完要重啟。正式環境（`FLASK_ENV=production` 或未設定）缺少「必填」項目或
`SECRET_KEY` / `JWT_SECRET_KEY` 仍是範例值會直接啟動失敗。

### Flask 與資料庫

| 變數 | 預設 | 正式必填 | 說明 |
|---|---|---|---|
| `FLASK_ENV` | production | | `development` / `testing` / `production`；未設定視為 production |
| `SECRET_KEY` | | 是 | Flask session 與衍生金鑰，用 `secrets.token_urlsafe(48)` 產生 |
| `LOG_LEVEL` | INFO | | 日誌等級 |
| `TRUSTED_PROXY_COUNT` | 0 | | 位於 ngrok / nginx 等 reverse proxy 後面時設信任層數（通常 1），才會採用 `X-Forwarded-*` |
| `DATABASE_URL` | | 是 | `postgresql+psycopg://<帳號>:<密碼>@<主機>:5432/<資料庫>` |

### LINE

| 變數 | 說明 |
|---|---|
| `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN` | Messaging API channel（必填） |
| `LINE_BASIC_ID` | 官方帳號基本 ID（`@` 可省略），首頁「加入 LINE 好友」按鈕用；留空只顯示 QR code |
| `LINE_LOGIN_CHANNEL_ID`、`LINE_LOGIN_CHANNEL_SECRET` | LINE Login channel（必填） |
| `LINE_LOGIN_REDIRECT_URI` | 預設 `http://localhost:5000/api/v1/auth/line/callback`，要與 Console 設定一致 |
| `LINE_LOGIN_SCOPE` | 預設 `profile openid` |
| `OAUTH_STATE_TTL_SECONDS` | OAuth state 有效秒數，預設 600 |
| `LIFF_ID` | LIFF app ID（必填） |
| `LIFF_CHANNEL_ID` | LIFF id_token 的 aud，留空沿用 `LINE_LOGIN_CHANNEL_ID` |

### JWT 與個資保護

| 變數 | 預設 | 說明 |
|---|---|---|
| `JWT_SECRET_KEY` | 同 `SECRET_KEY` | 自家 API access token 簽章金鑰（正式必填、不可用範例值） |
| `JWT_ACCESS_EXPIRES_SECONDS` | 3600 | 一般使用者 token 效期；LIFF 到期自動用 `liff.init()` 換新 |
| `JWT_STAFF_ACCESS_EXPIRES_SECONDS` | 28800 | 院方帳號效期（8 小時），到期導回後台登入頁 |
| `PATIENT_DATA_KEY` | 空 | Fernet 金鑰：身分證 HMAC 指紋與處方箋照片加密。留空時由 `SECRET_KEY` 衍生（僅開發用），正式必填 |
| `MEDIA_ROOT` | var/media | 本機：加密後的處方箋照片與 PDF 匯入暫存目錄 |
| `AZURE_STORAGE_CONNECTION_STRING` | 空 | 雲端：填了就把加密後的處方箋照片改存 Azure Blob Storage（留空存 `MEDIA_ROOT`） |
| `AZURE_STORAGE_CONTAINER` | prescriptions | 存放照片的 container（私有；不存在時第一次上傳自動建立） |

### OpenAI 與 AI 助理

| 變數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_API_KEY` | 空 | 正式必填；開發環境留空則 AI 功能停用 |
| `AI_MODEL` / `OCR_MODEL` / `EMBEDDING_MODEL` | 見「模型與版本」 | 留空用預設 |
| `AI_TIMEOUT_SECONDS` / `OCR_TIMEOUT_SECONDS` | 60 / 90 | 模型呼叫逾時 |
| `AI_ENABLED` | true | 關閉時自由文字只做關鍵字比對 |
| `AI_HISTORY_TURNS` / `AI_HISTORY_TTL_SECONDS` | 6 / 1800 | 帶進 prompt 的最近對話輪數與保留秒數 |
| `KNOWLEDGE_TOP_K` | 4 | 每次回答帶幾個知識片段 |
| `KNOWLEDGE_UPLOAD_MAX_MB` | 1024 | 衛教 PDF 一次上傳的總大小上限（只對該端點放寬，其餘端點 128 MB） |

### 排程、提醒、急診資料

| 變數 | 預設 | 說明 |
|---|---|---|
| `SCHEDULER_ENABLED` | true | 關閉程序內排程（測試環境預設關） |
| `WEBHOOK_ASYNC` | true | webhook 立刻回 200、背景處理事件（測試環境預設關） |
| `SUPER_ADMIN_LINE_USER_IDS` | 空 | 逗號分隔的 LINE userId，這些人自動成為 SUPER_ADMIN |
| `REMINDER_WINDOW_MINUTES` | 15 | 排程延遲容許值 |
| `REFILL_REMINDER_DAYS_BEFORE` | 3 | 慢性處方箋提前幾天提醒領藥 |
| `NHI_ER_API_URL` | 健保署公開端點 | 急診即時訊息 API |
| `NHI_ER_TIMEOUT_SECONDS` | 20 | 抓取逾時 |
| `TRIAGE_STALE_MINUTES` | 30 | 快照超過幾分鐘視為過舊，查詢時即時補抓 |
| `TRIAGE_RETENTION_DAYS` | 30 | 快照保留天數 |

---

## API

所有回應格式統一：成功 `{"success": true, "data": ...}`，失敗 `{"success": false, "error": {"code", "message", "details"}}`。
業務錯誤對應 HTTP 狀態：驗證失敗 422、未登入 401、沒有權限 403、狀態衝突 409、外部服務失敗 502。
除標示「公開」的端點外都需要 `Authorization: Bearer <JWT>`，JWT 由 LINE Login callback 或 `POST /api/v1/liff/auth` 取得。

### 公開與登入

| Method | Path | 權限 | 說明 |
|---|---|---|---|
| POST | /line/webhook | LINE 簽章 | LINE webhook 入口 |
| GET | /api/v1/health | 公開 | 健康檢查 |
| GET | /api/v1/auth/line/login | 公開 | 302 → LINE 授權頁 |
| GET | /api/v1/auth/line/callback | 公開 | 換發 JWT，渲染登入成功頁 |
| GET | /api/v1/auth/me | 登入 | 身分、權限清單、`is_staff`、可指派角色 |
| GET | /api/v1/liff/config | 公開 | 回傳 LIFF ID |
| POST | /api/v1/liff/auth | 公開 | LIFF id_token / access_token → JWT |
| GET | /api/v1/liff/me | 登入 | 身分 |

### 病人端（LIFF）

| Method | Path | 權限 | 說明 |
|---|---|---|---|
| GET | /api/v1/liff/hospitals[?q=] | 公開 | 帶 `q`：「我的就醫院所」加入院所的關鍵字搜尋（含診所，至少 2 個字、最多 20 筆）；不帶：醫院層級的院所清單 |
| GET / POST | /api/v1/liff/hospitals/mine | intake:write_own | 我加入的就醫院所（含是否已確認 / 已建檔）/ 加入一家（`hospital_id`；自己加的即視為確認） |
| POST | /api/v1/liff/hospitals/mine/{hospital_id}/confirm | intake:write_own | 回答院方的「是否在本院就醫」（`accepted` true / false） |
| DELETE | /api/v1/liff/hospitals/mine/{hospital_id} | intake:write_own | 移除一家院所（已建檔的回 409） |
| GET | /api/v1/liff/intake | intake:write_own | 表單 schema + 已填資料（身分證遮罩）+ 進度；送出後回 `read_only` |
| PUT | /api/v1/liff/intake/steps/{n} | intake:write_own | 存單一步驟；body `partial: true` 為草稿 |
| POST | /api/v1/liff/intake/submit | intake:write_own | 全表驗證 → 存原始資料 → `binding_status=PENDING` |
| GET | /api/v1/liff/profile | 已驗證 + profile:read_own | 病人自己的基本資料 |
| GET / POST | /api/v1/liff/feedback | feedback:submit | 問題回報類別與自己送過的回報 / 送出（不需填過病人表單） |
| GET | /api/v1/liff/prescriptions[/{id}] | prescription:manage_own | 自己的處方列表 / 單張（含辨識藥品清單，不含原始辨識文字） |
| GET | /api/v1/liff/prescriptions/{id}/image | prescription:manage_own | 原始處方箋照片（解密後回傳） |
| PATCH | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 修改院所 / 日期 / 天數 / 慢箋次數 / 藥品清單 |
| POST | /api/v1/liff/prescriptions/{id}/confirm | prescription:manage_own | `draft` → `confirmed`，算出慢箋領藥提醒日 |
| DELETE | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 標記 `discarded` |
| POST | /api/v1/liff/prescriptions/{id}/schedules/propose | reminder:manage_own | 依各藥頻率算出預設提醒時間（不寫入） |
| GET | /api/v1/liff/pharmacies?address= 或 ?lat=&lng= | consult:medication_once | 依縣市 / 鄉鎮市區列健保特約藥局（含今日營業時段） |
| GET | /api/v1/liff/drugs?q= | consult:medication_once | 健保用藥查詢（中英文名 / 成分） |
| GET / POST | /api/v1/liff/reminders | reminder:manage_own | 我的提醒排程（含今日推播狀態）/ 建立多筆 |
| PATCH / DELETE | /api/v1/liff/reminders/{id} | reminder:manage_own | 改時間 / 結束日 / 暫停 / 刪除 |
| GET | /api/v1/liff/reminders/history?days=7 | reminder:manage_own | 推播與回報紀錄 |
| GET | /api/v1/triage/board?lat=&lng=（或 ?area=01） | 公開（rate limit） | 依位置列最近 8 家急救責任醫院 + 燈號，`recommended` = 最近且未壅塞者 |
| GET | /api/v1/triage/areas | 公開 | 地區代碼與燈號門檻說明 |

### 院方後台

| Method | Path | 權限 | 說明 |
|---|---|---|---|
| GET | /api/v1/admin/permissions | permission:read | 完整角色權限表（只有系統方） |
| GET | /api/v1/admin/dashboard | patient:read | 待核對數、到期追蹤、未處理的問題回報 |
| GET | /api/v1/admin/districts | 院方角色 | 縣市 / 鄉鎮市區清單（篩選下拉用） |
| GET | /api/v1/admin/hospitals?q=&region=&tier=&page=&all= | 院方角色 | 院所分頁（每頁 15 筆；`all=1` 含停用，僅 SUPER_ADMIN） |
| GET | /api/v1/admin/hospitals/options | 院方角色 | 下拉選單用的院所（醫院層級，不含診所） |
| POST | /api/v1/admin/hospitals | hospital:manage | 新增院所（`id` 即院所代碼，建立後不可改） |
| POST | /api/v1/admin/hospitals/import | hospital:manage | CSV 批次匯入（自家範本 / 健保署特約機構 / 衛福部醫療機構基本資料），同代碼更新 |
| GET | /api/v1/admin/hospitals/import/template | hospital:manage | 下載匯入範本 CSV |
| PATCH | /api/v1/admin/hospitals/{id} | hospital:settings / hospital:manage | 修改院所資料；`active` 只有 SUPER_ADMIN 能改 |
| GET | /api/v1/admin/pharmacies?city=&district=&q=&page= | medication:manage | 藥局分頁 |
| GET | /api/v1/admin/pharmacy | pharmacy:settings | 登入者所屬藥局的資料（SUPER_ADMIN 用 `?pharmacy_id=`） |
| PATCH | /api/v1/admin/pharmacies/{id} | pharmacy:settings | 改電話 / 地址 / 營業時段（只能改自己的藥局；名稱與啟用只有 SUPER_ADMIN） |
| GET | /api/v1/admin/pickups?status=&page= | pickup:manage | 病人指定到本藥局的領藥需求（分頁；SUPER_ADMIN 看全部藥局） |
| PATCH | /api/v1/admin/pickups/{id} | pickup:manage | 改狀態 `ready` / `picked_up` / `cancelled`（備好與無法調劑會推播病人） |
| POST | /api/v1/admin/pharmacies/import | reference_data:manage | 藥局 CSV 匯入（可重跑） |
| GET | /api/v1/admin/drugs?q=&page= | 院方角色 | 藥品主檔分頁 |
| POST | /api/v1/admin/drugs/import | reference_data:manage | 健保用藥品項 CSV 匯入 |
| GET | /api/v1/admin/pushes/overview | push:manage | 推播管理：今日送出 / 已服用 / 失敗 / 略過、有效排程數、已封鎖人數（只有 SUPER_ADMIN） |
| GET | /api/v1/admin/pushes/deliveries?status=&page= | push:manage | 服藥提醒的推播紀錄（所有使用者，分頁） |
| GET | /api/v1/admin/pushes/schedules?active=&page= | push:manage | 提醒排程（所有使用者，分頁） |
| PATCH | /api/v1/admin/pushes/schedules/{id} | push:manage | 停用 / 啟用提醒排程（body `active`），寫稽核紀錄 |
| GET | /api/v1/admin/users | staff:manage | 人員列表（同院所或同藥局） |
| PUT | /api/v1/admin/users/{id}/role | staff:manage | 指派角色（body `role` + `hospital_id` 或 `pharmacy_id`；受 `rbac.assignable_roles()` 限制，不能跨單位） |
| GET / PATCH | /api/v1/admin/feedback[/{id}] | feedback:manage | 問題回報列表（`?status=open|resolved`）/ 標記已處理 |
| GET | /api/v1/admin/patients?status=&q= | patient:read | 病人列表，只含本院新增 / 建檔的病人（SUPER_ADMIN 全部） |
| GET | /api/v1/admin/patients/unclaimed?phone=&name=&last4= | patient:verify | 精準搜尋還沒被任何院所加入的病人（至少一個欄位、完全相等） |
| GET | /api/v1/admin/patients/{user_id} | patient:read | 自填資料 + 本院紀錄 + 就診 / 風險 / 追蹤 / 備註 / 歷次送出資料 |
| POST | /api/v1/admin/patients/{user_id}/request-hospital-confirmation | patient:verify | 加入本院並推播請病人確認；別家新增的回 409 |
| POST | /api/v1/admin/patients/{user_id}/verify | patient:verify | 核對身份 → 建檔 → VERIFIED + 角色（病人未確認回 409） |
| POST | /api/v1/admin/patients/{user_id}/reject | patient:verify | 退回修正（推播原因） |
| POST | /api/v1/admin/patients/{user_id}/lock、/unlock | patient:verify | 加入 / 移出黑名單（寫入稽核） |
| PATCH | /api/v1/admin/patients/{user_id}/profile | patient:write | 院方修正自填資料（保留 corrections） |
| POST / PATCH | /api/v1/admin/patients/{user_id}/encounters[/{id}] | encounter:manage | 就診 / 住院紀錄、出院代碼（未建檔時自動開暫存紀錄） |
| POST / PATCH | /api/v1/admin/patients/{user_id}/flags[/{id}] | patient:write | 風險警示 |
| POST / PATCH | /api/v1/admin/patients/{user_id}/follow-ups[/{id}] | patient:write | 追蹤事項 |
| POST | /api/v1/admin/patients/{user_id}/notes | patient:read | 內部備註 |
| GET | /api/v1/admin/audit | audit:read | 稽核紀錄（同院所） |

### 衛教資訊（知識庫）

| Method | Path | 權限 | 說明 |
|---|---|---|---|
| GET | /api/v1/admin/knowledge[/{id}]、/search?q= | education:content_manage | 文件列表 / 單篇（院所管理員只看本院）、搜尋（所有院所共用、本院優先） |
| POST / PATCH / DELETE | /api/v1/admin/knowledge[/{id}] | education:content_manage | 新增 / 修改 / 刪除（院所綁自己；通用與別家只有 SUPER_ADMIN） |
| GET | /api/v1/admin/knowledge/hospitals | education:content_manage | 各院所文件 / 片段 / 已有向量數 |
| GET | /api/v1/admin/knowledge/jobs[/{id}] | education:content_manage | 統計 + 背景工作進度 + 片段大小選項與用過的科別（頁面每 3 秒輪詢） |
| POST | /api/v1/admin/knowledge/jobs/pdf-import | education:content_manage | 上傳衛教 PDF（multipart `files` 多檔 + `department` + `chunk_size`），背景擷取、切段，回 202；單位管理員上傳的會接著算向量 |
| POST | /api/v1/admin/knowledge/jobs/rechunk | education:embed | 重建片段（可換 `chunk_size`）；院所範圍自動重算向量 |
| POST | /api/v1/admin/knowledge/jobs/embed | education:embed | 替還沒有向量的片段計算 embedding（單位管理員只算本院，SUPER_ADMIN 全庫） |

### 網頁

| Path | 說明 |
|---|---|
| / | 首頁：功能介紹 + 加 LINE 好友 QR code（不提供登入、也沒有後台連結；院方直接開 /admin/login） |
| /liff、/liff/settings | 病人設定頁：綁定狀態、資料摘要、進入表單 |
| /liff/intake | 病人自填表（6 步、可續填）。**只在 LINE 內透過 LIFF 開啟**：`https://liff.line.me/<LIFF_ID>/intake` |
| /liff/hospitals | 我的就醫院所：加入 / 移除就醫的醫院與診所（可多家） |
| /liff/feedback | 問題回報表單 |
| /liff/prescriptions/{id} | 處方辨識結果：看原始照片、修改藥品清單、確認、建立提醒 |
| /liff/reminders?prescription={id} | 服藥提醒總覽 / 新增 / 編輯 |
| /liff/er?area=01 或 ?lat=&lng= | 急診即時看板（公開資料，不需登入） |
| /admin、/admin/login | 後台儀表板；未登入 / 過期 / 非院方帳號導向登入頁（`?next=` 只接受 `/admin` 開頭） |
| /admin/patients[/{user_id}] | 病人列表 / 詳情（核對、修正、就診、風險、追蹤、備註） |
| /admin/feedback、/admin/staff | 問題回報、人員角色 |
| /admin/knowledge | 衛教資訊文件維護與搜尋；SUPER_ADMIN 另有各院所統計 |
| /admin/pharmacies、/admin/drugs | 藥局 / 藥品參考資料查詢 |
| /admin/pickups、/admin/pharmacy | 藥局人員：領藥需求、我的藥局（電話 / 地址 / 營業時段）；SUPER_ADMIN 看全部藥局的需求，從藥局清單的「編輯」改任一家藥局 |
| /admin/pushes | 推播管理（只有 SUPER_ADMIN）：服藥提醒的今日數字、推播紀錄、提醒排程停用 / 啟用 |
| /admin/imports | 資料匯入：院所 / 藥局 / 藥品 CSV、衛教 PDF，以及知識庫背景工作進度 |
| /admin/settings、/admin/permissions、/admin/audit | 院所設定、權限表、稽核紀錄 |

後台頁面骨架不需登入即可開啟，頁內 JS 用 `sessionStorage` 的 JWT 打 API，真正的授權在 API 端。

---

## 資料流程

### 1. 病人加好友 → 填表 → 院方核對

```
加好友 → 建立 GUEST 使用者（binding_status=UNBOUND）→ 歡迎訊息「填寫基本資料」
  → LIFF /liff/intake 逐步存檔（patient_* 7 張表，身分證只存末四碼 + HMAC 指紋）
  → 送出 → PENDING，病人端轉唯讀，原始 JSON 存 patient_form_submissions
  → 就醫院所是另一張表單 LIFF /liff/hospitals（patient_hospitals，每家院所一筆、可多家）：
     病人自己加入 → 當下即視為確認，該院所的待核對清單直接看到、可直接核對建檔
     院方後台「搜尋病人」精準比對（手機 / 姓名 / 末四碼，找「還沒加入本院」的病人）
       →「加入本院並請病人確認」→ LINE 推播「是 / 不是」→ 病人按「是」才算確認
       （按「不是」→ 刪掉本院這一筆，本院不再看得到；其他院所不受影響）
  → 院方「核對建檔」→ patient_records（status=active）、binding_status=VERIFIED、角色改 PATIENT / FAMILY
  → 之後去別家院所：病人再加一家、或該院所搜尋加入，各自建檔
```

自填資料跟著使用者走、跨院所共用一份，院方紀錄跟著院所走；院所只看得到在本院有就醫院所那一筆或已在本院建檔的病人。病人只能移除還沒建檔的院所。

### 2. 自由文字 → AI 助理

```
文字訊息 → 完全等於功能關鍵字？→ 是：直接進功能（不經 AI）
        → 否：ConversationService.build_context()
              使用者狀態 + 最近對話（只供理解上下文）+ 知識片段（關鍵字 + 語意混合檢索，本院與就診科別加權）
              + 即時資料（提到縣市 → 急診看板摘要；提到藥名 → 藥品主檔）
        → OpenAI 回 AssistantDecision（event / risk / answer / slots / actions / citations）
        → AssistantHandler 依 event 套用模組：hospital / clinic / medication / pharmacy /
          reminder / discharge / intake / feedback，或 answer + Quick Reply（附「參考來源」按鈕）
        → AI 失敗 → 關鍵字退路 → 失敗訊息 + 主選單
```

### 3. 處方箋拍照 → 找藥局 / 建立提醒

```
「去哪拿藥」→ Quick Reply 拍照 / 選相簿 → 圖片訊息（對話狀態 await_prescription_photo）
  → 顯示 loading animation → 下載圖片 → OpenAI 看圖辨識（只抄寫、不推測、不輸出姓名與身分證）
  → prescriptions（items JSON；照片 Fernet 加密後存 Azure Blob / MEDIA_ROOT）→ 回辨識結果 Flex
  → 找藥局：分享位置 → 地址解析縣市 / 鄉鎮市區（抓不到用座標反推）→ 比對 pharmacies → 列出營業中藥局
  → 建立提醒：LIFF /liff/reminders 依頻率代碼（qd / bid / tid…）× 飯前後算預設時間 → medication_schedules
  → APScheduler 每 5 分鐘 run_due() → 同一人同一批合併成一則 Flex 推播 → reminder_deliveries（同一時段只送一次）
  → 已服用 / 30 分後 / 跳過 postback 回寫狀態
```

### 4. 找醫院（急診分流）

```
APScheduler 每 15 分鐘 → 健保署急診即時訊息 API → er_snapshots（UNIQUE(醫院, 回報時間)）→ 清除超過保留天數的快照
「找醫院」→ 請分享位置 → 位置訊息 → board_for_location(lat, lng[, department])
  → 依距離列最近 8 家急救責任醫院 + 壅塞燈號（triage_rules 門檻）→ recommended = 最近且未壅塞（有科別優先）
  → Flex + 該醫院 LocationMessage（可導航）
非急症 → 附近診所：hospitals 主檔同鄉鎮市區 → 有症狀 / 科別時交給模型依名稱與登記科別挑選 → 列出並提醒先電話確認
```

### 5. 衛教 PDF → 知識庫 → 檢索

```
後台「資料匯入」上傳 PDF（或 scripts/import_pdfs.py）→ knowledge_jobs（背景執行緒）
  → pypdf 擷取文字 → 正規化（康熙部首 / 相容字轉回常用字、去亂碼行、去頁首頁尾、接回折行）
  → chunk_document() 結構感知切段（標題 / 條列 / 表格 / 問答，small 300 / medium 400 / large 600 字）
  → knowledge_documents（一個 PDF 一篇，同院所同檔名重傳 = 更新）+ knowledge_chunks（tokens、tsvector、embedding）
  → 單位管理員上傳的自動呼叫 OpenAI 算向量（算不了只記錯誤，關鍵字檢索照常可用）；其他人上傳的等單位管理員按「計算向量」
  → 原始 PDF 刪除，只留文字
檢索時：所有院所 + 通用內容共用一池，病人所屬院所的片段夠相關時優先，相同內文去重
```

---

## 角色與權限

角色分三方（`app/domain/enums.py`）：

| 角色 | 歸屬 | 說明 | 重點權限 |
|---|---|---|---|
| guest | 用戶端 | 加好友但未驗證 | 病況諮詢、找醫院、藥物單次查詢、處方辨識、服藥提醒、填表、問題回報 |
| patient / family | 用戶端 | 驗證通過（本人 / 家屬） | guest 全部 + 個人化衛教、查看自己的基本資料 |
| clinic_staff | 院方（診所） | 診所人員（助理 / 護理師），不分工 | hospital_staff + medical_staff 的全部 |
| hospital_staff | 院方（醫院） | 行政人員 | 病人讀取 / 修正 / 核對建檔、問題回報處理（不能寫就診紀錄） |
| medical_staff | 院方（醫院） | 醫護人員 | 病人讀取 / 修正、就診紀錄、衛教文件上傳、藥局查詢（不能核對建檔） |
| pharmacy_staff | 院方（藥局） | 藥局人員 | 處理病人的領藥需求、維護自己藥局的資料 |
| hospital_admin | 院方（醫院 / 診所 / 藥局共用） | 單位管理員 | 掛在院所：clinic_staff + 計算衛教向量、人員管理、院所設定、稽核；掛在藥局：pharmacy_staff + 人員管理 |
| super_admin | 系統方 | 開發 / 維運方，不屬於任何單位 | 擁有全部權限、看得到所有介面；獨有院所管理、參考資料匯入與推播管理 |

院方帳號一定屬於一個單位：醫院 / 診所掛 `users.hospital_id`，藥局掛 `users.pharmacy_id`，二選一。
單位管理員是三種單位共用的角色，實際權限由 `rbac.permissions_of(principal)` 依所屬單位決定。
診所用 clinic_staff 一個角色就夠；大醫院要分工時用 hospital_staff + medical_staff。

- `ROLE_PERMISSIONS`（`app/domain/rbac.py`）是明確列舉，不是階層繼承；新增權限要同時改 `Permission` 列舉與對照表，
  `tests/test_rbac.py` 有角色集合關係的斷言。完整矩陣可在後台「權限表」頁或 `GET /api/v1/admin/permissions` 查看。
- **院所隔離**：院方查詢一律經過 `AccessPolicy.hospital_scope(actor)`，沒有 `hospital_id` 的院方帳號直接 403，
  不會退化成看全部；SUPER_ADMIN 不受限。衛教資訊「管理以院所為單位、檢索不分院所」。
- **角色指派**：`rbac.assignable_roles(actor)` 限制誰能指派什麼角色（院所管理員只能指派院所角色、藥局管理員只能指派藥局人員，
  都不能跨單位）；升為 SUPER_ADMIN 時清掉 `hospital_id` / `pharmacy_id`。
- **衛教向量**：上傳衛教 PDF 是 `education:content_manage`（診所人員 / 醫護 / 單位管理員），
  把片段丟進向量資料庫（「計算向量」、重建片段）是 `education:embed`，只有單位管理員與 SUPER_ADMIN。
- **驗證狀態**：`Principal.is_verified`（`BindingStatus.VERIFIED`）是服務地圖上「基本資料已填且驗證通過？」的判斷，
  `IntentHandler.requires_verified` 與 `@require_verified()` 都以此為準。
- **後台選單**依 `/api/v1/auth/me` 回傳的權限隱藏，前端不硬編碼角色清單；真正授權在 API decorator。
