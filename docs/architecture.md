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
| 設定頁（檢視 / 修改 / 解除綁定） | `intents/settings.py`、`DELETE /liff/binding` |
| B1–B9 綁定流程 | `services/binding_service.py`、`api/v1/liff.py` |
| 基本資料已填且驗證通過？ | `Principal.is_verified`（`BindingStatus.VERIFIED`） |

## 綁定狀態機

```
UNBOUND --start(B1)--> PENDING --identity(B2)--> --profile(B3)--> --verification(B4)--> --consent(B5)--> MATCHING
MATCHING --match & unbound--> VERIFIED (B7)  role: PATIENT / FAMILY
MATCHING --mismatch (<3)-----> 退回 PROFILE 重填 (B8 提示重填)
MATCHING --mismatch (=3)-----> escalated + HandoffTicket (B8 轉真人核對)
MATCHING --bound by other----> LOCKED + HandoffTicket (B9)
VERIFIED --revoke-----------> UNBOUND, role: GUEST
```

## 擴充點

- 新增意圖：繼承 `IntentHandler`，在 `IntentRouter.default()` 註冊，並在 `Intent` 列舉加值。
- 新增 Webhook 事件類型：繼承 `EventHandler`，設定 `event_type`，在 `EventDispatcher.default()` 註冊。
- 換掉驗證方式（簡訊供應商 / HIS）：實作 `VerificationGateway`。
- OAuth state 改用 Redis：實作 `OAuthStateStore`。
- AI 對話引擎：在 `MessageHandler.handle()` 未命中關鍵字處接入。
