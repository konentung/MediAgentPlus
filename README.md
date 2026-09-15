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
app/repositories/          Repository Protocol + in-memory 實作（待換 SQLAlchemy）
app/services/              UserService / BindingService(B1–B9) / HandoffService / 驗證閘道
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages、Rich Menu
app/api/v1/                REST API：health / auth / liff / admin
app/models/                SQLAlchemy 模型（尚未設計，僅 TimestampMixin）
tests/                     pytest（RBAC、JWT、OAuth、LIFF、綁定流程、webhook）
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
| GET | /api/v1/health | 公開 | 健康檢查 |
| GET | /api/v1/auth/line/login | 公開 | 302 → LINE 授權頁 |
| GET | /api/v1/auth/line/callback | 公開 | 換發 JWT（FRONTEND_URL 存在時 302 帶 token） |
| POST | /api/v1/auth/refresh | 公開 | refresh_token → 新 token pair |
| GET | /api/v1/auth/me | 登入 | 身分 + 權限清單 |
| GET | /api/v1/liff/config | 公開 | 回傳 LIFF ID |
| POST | /api/v1/liff/auth | 公開 | id_token / access_token → JWT |
| GET | /api/v1/liff/me | 登入 | 身分 + 綁定進度 |
| POST | /api/v1/liff/binding/start | binding:create | B1 |
| POST | /api/v1/liff/binding/identity | binding:create | B2 身分別 |
| POST | /api/v1/liff/binding/profile | binding:create | B3 基本欄位 |
| POST | /api/v1/liff/binding/verification | binding:create | B4 驗證方式 |
| POST | /api/v1/liff/binding/consent | binding:create | B5 同意書 |
| POST | /api/v1/liff/binding/verify | binding:create | 比對 → verified / mismatch / escalated / locked |
| DELETE | /api/v1/liff/binding | binding:revoke | 解除綁定 |
| GET | /api/v1/liff/profile | 已驗證 + profile:read_own | 基本資料 |
| GET | /api/v1/admin/permissions | 院方角色 | 權限矩陣 |
| PUT | /api/v1/admin/users/{id}/role | staff:manage | 指派角色（同院所限制） |
| GET/POST | /api/v1/admin/roster | roster:read / roster:manage | 院方識別名單 |
| GET | /api/v1/admin/handoffs | handoff:accept | 待處理工單 |
| POST | /api/v1/admin/handoffs/{id}/accept | handoff:accept | 接單 |

## 角色與權限

`flask --app wsgi rbac show` 可輸出完整矩陣。摘要：

| 角色 | 說明 | 重點權限 |
|---|---|---|
| guest | 加好友但未綁定 | 病況諮詢、找醫院、藥物單次查詢、通用衛教、找真人、開始綁定 |
| patient / family | 綁定完成（本人 / 家屬） | guest 全部 + 個人化衛教、服藥提醒、個資讀寫、解除綁定 |
| cs_agent | 真人客服 | 接單回覆、對話讀取、人工核對（B8）、解鎖（B9）、病患 / 名單讀取 |
| medical_staff | 醫護 / 衛教 | 病患 / 名單 / 對話讀取、衛教內容管理 |
| hospital_admin | 院所管理員 | cs_agent + medical_staff + 名單維護、人員管理、院所設定、Rich Menu、稽核 |
| super_admin | 系統管理員 | 全部 + 院所管理、系統管理 |

院所資料隔離由 `AccessPolicy.can_access_hospital()` 統一判斷；角色指派範圍由 `ROLE_ASSIGNABLE_BY` 限制。

## 接上資料庫時要做的事

1. 在 `app/models/` 設計模型（User、BindingSession、RosterEntry、HandoffTicket ...）。
2. 為每個 Repository Protocol 實作 SQLAlchemy 版本。
3. 在 `ServiceContainer.build()` 換掉 in-memory 實作，其餘程式碼不需變動。
4. `flask --app wsgi db init && flask --app wsgi db migrate && flask --app wsgi db upgrade`。
