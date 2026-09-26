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
app/cli.py                 flask richmenu / rbac / user 指令
app/core/                  例外、統一回應、錯誤處理、DI 容器
app/domain/                Role / Permission / BindingStatus / Intent、Principal、AccessPolicy、
                           intake_schema（病人自填表欄位定義與驗證）
app/auth/                  JWT、LINE Login OAuth、LIFF 驗證、授權裝飾器、OAuth state
app/repositories/          Repository Protocol，User / Handoff / Hospital / Patient / PatientRecord /
                           AuthSession / Audit 皆為 SQLAlchemy 實作
app/services/              UserService / HandoffService / HospitalService / IntakeService / PatientRecordService
app/utils/crypto.py        FieldCipher：身分證字號欄位級加密（Fernet）與指紋雜湊
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages、Rich Menu
app/api/v1/                REST API：health / auth / liff / admin
app/pages.py               一般網頁（首頁 `/`）
app/liff_pages.py          LIFF 頁面：/liff/intake（病人自填表）、/liff/settings
app/admin_pages.py         院方後台頁面：/admin/*（儀表板、病人、工單、人員、設定）
app/models/                SQLAlchemy 模型：Hospital / User / HandoffTicket / Patient*（自填資料 7 張）/
                           PatientRecord*（院方紀錄 6 張），migrations/ 由 Flask-Migrate 管理
app/templates/             Jinja 頁面（無獨立前端專案）：base.html、liff/、admin/、auth/、errors/
tests/                     pytest（RBAC、JWT、OAuth、LIFF、webhook、intake、後台）
```

## 請求生命週期

**LINE Webhook**：`POST /line/webhook` → `WebhookParser` 驗簽 → `EventDispatcher` 依事件類型找 `EventHandler`
→ 建立 `BotContext`（含 `Principal`）→ 文字 / postback 交給 `IntentRouter` → `IntentHandler.execute()`
先檢查鎖定 / 綁定 / 權限，再呼叫 `handle()`。

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
| GET | /api/v1/admin/permissions | 院方角色 | 權限矩陣 |
| GET | /api/v1/admin/dashboard | patient:read | 待核對數、到期追蹤、開放工單 |
| GET | /api/v1/admin/hospitals | 院方角色 | 院所清單（SUPER_ADMIN 帶 `?all=1` 含停用） |
| POST | /api/v1/admin/hospitals | hospital:manage | 新增院所（`id` 即院所代碼，建立後不可改） |
| POST | /api/v1/admin/hospitals/import | hospital:manage | CSV 批次匯入（multipart `file` 或 JSON `rows`），同代碼更新、否則新增；`?only_hospitals=1` 略過診所 |
| GET | /api/v1/admin/hospitals/import/template | hospital:manage | 下載匯入範本 CSV |
| PATCH | /api/v1/admin/hospitals/{id} | hospital:settings / hospital:manage | 修改院所資料；`active` 只有 SUPER_ADMIN 能改 |
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
| /admin | 院方後台儀表板；未登入 / 過期 / 非院方帳號一律導向 `/admin/login` |
| /admin/login | 後台登入頁（「使用 LINE 登入」按鈕），`?next=` 登入後回原頁、`?error=` 顯示原因。LIFF 端不走這頁 |
| /admin/patients、/admin/patients/{user_id} | 病人列表 / 詳情（核對、修正、就診、風險、追蹤、備註） |
| /admin/handoffs、/admin/staff | 工單、人員角色 |
| /admin/settings、/admin/permissions、/admin/audit | 院所設定（本院資料、填表連結、所有院所管理與匯入）、權限矩陣查閱、稽核紀錄 |

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
`HandoffTicket`、`AuthSession`、`AuditLog`、病人自填資料（`patient_profiles` 等 7 張）、院方紀錄（`patient_records` 等 6 張）都是 SQLAlchemy 模型（`app/models/`），`ServiceContainer.build()` 用對應的
`SqlAlchemy*Repository` 讀寫 PostgreSQL，`migrations/` 由 Flask-Migrate 管理。身分證字號以
`PATIENT_DATA_KEY`（Fernet）加密存放，另存末四碼與 HMAC 指紋供搜尋與重複檢查。資料表設計見
`docs/patient-form-design.md`。`OAuthStateStore` 與 rate limit 在未設 `REDIS_URL` 時用單程序記憶體，設定後改用 Redis
（`app/auth/state_store.py`、`app/core/ratelimit.py`）。

新增欄位或模型時：改 `app/models/`、跑 `flask --app wsgi db migrate -m "..."` 產生 migration、
人工檢查產生的檔案（尤其是 Enum / JSONB 型別是否正確）、再 `flask --app wsgi db upgrade`。
`pytest` 不需要真的 PostgreSQL，測試設定用 in-memory SQLite（`config/settings.py` 的 `TestingConfig`）。
