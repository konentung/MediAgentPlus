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
| 設定頁（檢視 / 修改基本資料） | `intents/settings.py` → LIFF `/liff/settings`（`app/liff_pages.py`） |
| B1–B6 填寫基本資料（LIFF 6 步表單） | `/liff/intake`（`app/templates/liff/intake.html`）、`app/domain/intake_schema.py`、`IntakeService`、`api/v1/liff.py` |
| B7 院方核對身份 → 建檔 | 後台 `/admin/patients/<user_id>`（`app/admin_pages.py`）、`PatientRecordService.verify()`、`api/v1/admin.py` |
| B8 退回補正 | `PatientRecordService.reject()`，推播原因給病人 |
| B9 完成通知 | `verify()` 內 `line_client.push()` |
| 基本資料已填且驗證通過？ | `Principal.is_verified`（`BindingStatus.VERIFIED`） |

## 綁定狀態

```
UNBOUND ──送出 intake（IntakeService.submit）──▶ PENDING ──院方 verify──▶ VERIFIED
   ▲                                              │
   └──────────── 院方 reject ─────────────────────┘
PENDING / VERIFIED：病人端唯讀（只能檢視），修正由院方後台進行
LOCKED：只由院方後台鎖定 / 解鎖（寫稽核）；解鎖回 UNBOUND
同一身分證出現在第二個帳號：只回 409，不自動鎖定
```

`VERIFIED` 時同時把 `role` 從 GUEST 改為 PATIENT（本人）或 FAMILY（家屬代填）。
資料模型與後台設計見 `docs/patient-form-design.md`。

## 擴充點

- 新增意圖：繼承 `IntentHandler`，在 `IntentRouter.default()` 註冊，並在 `Intent` 列舉加值。
- 新增 Webhook 事件類型：繼承 `EventHandler`，設定 `event_type`，在 `EventDispatcher.default()` 註冊。
- OAuth state / rate limit：設定 `REDIS_URL` 即改用 Redis 實作（`RedisOAuthStateStore`、`RedisRateLimiter`）。
- 登入工作階段：`AuthSessionService` 管理 refresh token 輪替、重用偵測、登出撤銷，並與 LINE Login 的 refresh / revoke 端點同步；`auth_sessions` 表。
- AI 對話引擎：在 `MessageHandler.handle()` 未命中關鍵字處接入。
- 新增院所：`flask hospital add` 或後台「院所設定」；其他表寫入 `hospital_id` 前呼叫 `HospitalService.require_active()`。
- 病人表單加欄位：只改 `app/domain/intake_schema.py`（必要時加 model 欄位 + migration），LIFF 頁面、驗證、後台標籤自動跟上。
- 衛教個人化：`PatientRecordRepository.latest_discharge_code(user_id)` 取最近一次出院代碼。
