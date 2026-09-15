# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概要

MediAgent+ 醫療照護 LINE 機器人後端。Python 3.11、Flask 3、line-bot-sdk 3.25.0（`linebot.v3`）、PostgreSQL（psycopg 3）。只有後端，無前端程式碼。
業務流程依據「服務地圖」設計：加好友 → Rich Menu 六格 → 依「基本資料已填且驗證通過？」分流 → LIFF 綁定流程 B1–B9。對照表在 `docs/architecture.md`。

## 常用指令

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                   # 全部測試，不需要資料庫
pytest tests/test_binding.py -v          # 單一檔案
pytest tests/test_rbac.py::test_hospital_scope   # 單一測試
pytest -p no:warnings                    # 隱藏 PyJWT 短金鑰警告（測試用金鑰刻意很短）

ruff check . && ruff format .            # lint + 格式化，行長 100，tests/ 不檢查 E501

./scripts/run_dev.sh                     # flask --app wsgi run --debug
flask --app wsgi routes                  # 列出路由
flask --app wsgi rbac show               # 角色權限矩陣
flask --app wsgi richmenu setup --image <png>   # 建立並設為預設 Rich Menu（2500×1686）
flask --app wsgi user set-role <user_id> <role> # 以 SUPER_ADMIN 身分指派角色
```

環境設定來自 `.env`（複製 `.env.example`），`config/settings.py` 在 import 時讀取；改 `.env` 需重啟。
`FLASK_ENV=testing` 使用 `TestingConfig`，內含假的 LINE / JWT 憑證，SQLite in-memory 佔位。

## 架構重點

### 請求生命週期有兩條，共用同一套 Principal / 權限

- **LINE webhook** `POST /line/webhook`（`app/line/webhook.py`）：`WebhookParser` 驗簽 → `EventDispatcher` 依事件類別找 `EventHandler` → 建立 `BotContext`（含 `Principal`，首次接觸即建立 GUEST 使用者）→ 文字 / postback 交給 `IntentRouter` → `IntentHandler.execute()` 先檢查鎖定、綁定、權限，再呼叫子類的 `handle()`。
- **REST API** `/api/v1/*`：`Authorization: Bearer <JWT>` → `app/auth/decorators.py` 的 `current_principal()` **每次都從 Repository 重新載入使用者**，token 內的 role 只是快照不被信任 → `@require_auth / @require_permission / @require_role / @require_verified`。

### 授權集中在 `app/domain/rbac.py`

`ROLE_PERMISSIONS` 是明確列舉（非階層繼承），`AccessPolicy` 是唯一的判斷入口，包含院所隔離 `can_access_hospital()`（SUPER_ADMIN 不受限）與角色指派範圍 `can_assign_role()`。新增權限時同時改 `Permission` 列舉與此對照表，`tests/test_rbac.py` 有對角色集合關係的斷言。

`Principal.is_verified`（`BindingStatus.VERIFIED`）就是服務地圖上「基本資料已填且驗證通過？」菱形的判斷；`IntentHandler.requires_verified = True` 與 `@require_verified()` 都以此為準。

### 依賴注入：`ServiceContainer`（`app/core/container.py`）

所有 Repository、Service、`JwtService`、`LineLoginService`、`LiffAuthService`、`LineMessagingClient` 在 `ServiceContainer.build(app)` 一次組裝，掛在 `app.extensions["container"]`，透過 `get_container()` 取用。測試以 `app.extensions["container"].line_client = FakeLineClient()` 替換元件（見 `tests/conftest.py`），任何對外呼叫都必須經過容器內的物件，不要直接 new SDK client。

### 資料層尚未接資料庫

`app/repositories/` 只有 Protocol + in-memory 實作（`UserRecord`、`BindingSession`、`RosterEntry`、`HandoffTicket` 是 dataclass 佔位）。`app/models/` 只有 `TimestampMixin`。接 SQLAlchemy 時：實作各 Protocol → 在 `ServiceContainer.build()` 換掉 → Service / API / 測試不需改動。`OAuthStateStore` 與 `VerificationGateway` 同樣是 Protocol，正式環境需換 Redis / 簡訊供應商實作。

### 綁定流程狀態機 `app/services/binding_service.py`

`start(B1) → submit_identity(B2) → submit_profile(B3) → request_verification(B4) → give_consent(B5) → verify()`。`verify()` 與院方識別名單比對後回傳 `BindingOutcome`：`VERIFIED`（B7，角色升為 PATIENT / FAMILY）、`MISMATCH`（退回 B3）、`ESCALATED`（累計 `BINDING_MAX_FAILURES` 次建立客服工單）、`LOCKED`（B9，名單已被其他 LINE 綁定）。步驟順序由 `_require_session()` 強制。

### LINE 層慣例

- Postback data 統一用 querystring `intent=<Intent>&key=value`，以 `postback_data()` 產生、`IntentRequest.from_postback()` 解析。文字關鍵字對照在 `app/line/intents.py` 的 `TEXT_KEYWORDS`，紅旗關鍵字 `EMERGENCY_KEYWORDS` 命中直接導向緊急畫面。
- 新增意圖：加 `Intent` 列舉值 → 繼承 `IntentHandler`（設 `intent`、`required_permission`、`requires_verified`）→ 在 `IntentRouter.default()` 註冊。新增事件類型：繼承 `EventHandler` 設 `event_type` → 在 `EventDispatcher.default()` 註冊。
- 訊息文案集中在 `app/line/messages/`，透過 `MessageBuilder` 組 Flex / QuickReply；`BotContext.reply()` 自動截到 LINE 上限 5 則。
- SDK 事件模型是 pydantic v1：`FollowEvent` 需要 `follow.isUnblocked`，測試 payload 缺欄位會被解析成 `UnknownEvent` 而靜默略過。

### 錯誤與回應

業務錯誤一律 raise `app/core/exceptions.py` 的 `AppError` 子類（`ValidationError` 422、`AuthenticationError` 401、`AuthorizationError` 403、`ConflictError` 409、`ExternalServiceError` 502），由 `register_error_handlers` 統一轉成 `{"success": false, "error": {...}}`；成功用 `app/core/responses.py` 的 `ok()`。

## 文件

- `README.md`：端點表、角色摘要、接資料庫步驟。
- `docs/local-operations.md`：本機安裝（pgAdmin 為主）、LINE Developers Console 設定、ngrok、curl 手動測試、常見問題。
- `docs/architecture.md`：服務地圖節點與程式位置對照、狀態機圖、擴充點。
