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
app/domain/                Role / Permission / BindingStatus / Intent、Principal、AccessPolicy
app/auth/                  JWT、LINE Login OAuth、LIFF 驗證、授權裝飾器、OAuth state
app/repositories/          Repository Protocol，User/Handoff 為 SQLAlchemy 實作，
                           OAuthStateStore 仍是 in-memory（待換 Redis）
app/services/              UserService / HandoffService
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages、Rich Menu
app/api/v1/                REST API：health / auth / liff / admin
app/pages.py               一般網頁（首頁 `/`）
app/models/                SQLAlchemy 模型：UserModel / HandoffTicketModel + TimestampMixin，
                           migrations/ 由 Flask-Migrate 管理
app/templates/             前端頁面用 Jinja render（無獨立前端專案），base.html 共用版型
tests/                     pytest（RBAC、JWT、OAuth、LIFF、webhook）
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
| POST | /api/v1/auth/refresh | 公開 | refresh_token → 新 token pair |
| GET | /api/v1/auth/me | 登入 | 身分 + 權限清單 |
| GET | /api/v1/liff/config | 公開 | 回傳 LIFF ID |
| POST | /api/v1/liff/auth | 公開 | id_token / access_token → JWT |
| GET | /api/v1/liff/me | 登入 | 身分 |
| GET | /api/v1/liff/profile | 已驗證 + profile:read_own | 基本資料（占位，綁定流程定案後實作） |
| GET | /api/v1/admin/permissions | 院方角色 | 權限矩陣 |
| PUT | /api/v1/admin/users/{id}/role | staff:manage | 指派角色（同院所限制） |
| GET | /api/v1/admin/handoffs | handoff:accept | 待處理工單 |
| POST | /api/v1/admin/handoffs/{id}/accept | handoff:accept | 接單 |

## 角色與權限

`flask --app wsgi rbac show` 可輸出完整矩陣。摘要：

| 角色 | 說明 | 重點權限 |
|---|---|---|
| guest | 加好友但未驗證 | 病況諮詢、找醫院、藥物單次查詢、通用衛教、找真人 |
| patient / family | 驗證通過（本人 / 家屬） | guest 全部 + 個人化衛教、服藥提醒、個資讀寫 |
| cs_agent | 真人客服 | 接單回覆、對話讀取、病患讀取 |
| medical_staff | 醫護 / 衛教 | 病患 / 對話讀取、衛教內容管理 |
| hospital_admin | 院所管理員 | cs_agent + medical_staff + 人員管理、院所設定、Rich Menu、稽核 |
| super_admin | 系統管理員 | 全部 + 院所管理、系統管理 |

院所資料隔離由 `AccessPolicy.can_access_hospital()` 統一判斷；角色指派範圍由 `ROLE_ASSIGNABLE_BY` 限制。

## 資料庫

`User`、`HandoffTicket` 已經是 SQLAlchemy 模型（`app/models/`），
`ServiceContainer.build()` 用對應的 `SqlAlchemy*Repository` 讀寫 PostgreSQL，`migrations/` 由
Flask-Migrate 管理。`OAuthStateStore` 仍是 in-memory（正式多 worker 部署需換 Redis，見
`app/auth/state_store.py`）。

新增欄位或模型時：改 `app/models/`、跑 `flask --app wsgi db migrate -m "..."` 產生 migration、
人工檢查產生的檔案（尤其是 Enum / JSONB 型別是否正確）、再 `flask --app wsgi db upgrade`。
`pytest` 不需要真的 PostgreSQL，測試設定用 in-memory SQLite（`config/settings.py` 的 `TestingConfig`）。
