# 架構說明

## 服務地圖 → 程式對照

| 服務地圖節點 | 程式位置 |
|---|---|
| 加入好友 → 歡迎訊息 → 安全聲明 → 資料使用說明 Flex | `app/line/handlers/follow.py`、`app/line/messages/onboarding.py` |
| 主選單 Rich Menu 六格 | `app/line/richmenu.py`（`DEFAULT_CELLS`） |
| 我不舒服 → ① 病況諮詢 / 紅旗 → 緊急畫面 | `intents/symptom.py`、`EMERGENCY_KEYWORDS` in `app/line/intents.py` |
| 找醫院 → 院所查詢 + 壅塞燈號 | `intents/hospital.py` |
| 我剛出院 → 已驗證？→ ② 個人化 / 通用版 | `intents/discharge.py` |
| 去哪拿藥 → ③ 藥物諮詢 / 一鍵建立提醒 | `intents/medication.py` |
| 服藥提醒 → 已驗證？→ ④ 服藥小幫手 | `intents/reminder.py`（`requires_verified = True`） |
| 找真人 / 轉真人客服 | `intents/human.py`、`services/handoff_service.py` |
| 設定頁（檢視 / 修改基本資料） | `intents/settings.py` |
| B1–B9 綁定流程 | **尚未設計**，待流程定案後實作（LIFF 頁面 + `api/v1/liff.py` 端點） |
| 基本資料已填且驗證通過？ | `Principal.is_verified`（`BindingStatus.VERIFIED`） |

## 綁定狀態

`users.binding_status` 只保留狀態欄位（`UNBOUND / PENDING / VERIFIED / LOCKED`）供「已驗證？」
分流使用；狀態轉換的流程（B1–B9）尚未設計，目前沒有任何程式碼會改動這個欄位。

## 擴充點

- 新增意圖：繼承 `IntentHandler`，在 `IntentRouter.default()` 註冊，並在 `Intent` 列舉加值。
- 新增 Webhook 事件類型：繼承 `EventHandler`，設定 `event_type`，在 `EventDispatcher.default()` 註冊。
- OAuth state 改用 Redis：實作 `OAuthStateStore`。
- AI 對話引擎：在 `MessageHandler.handle()` 未命中關鍵字處接入。
