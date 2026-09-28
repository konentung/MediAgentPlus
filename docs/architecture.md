# 架構說明

## 服務地圖 → 程式對照

| 服務地圖節點 | 程式位置 |
|---|---|
| 加入好友 → 歡迎訊息 → 安全聲明 → 資料使用說明 Flex | `app/line/handlers/follow.py`、`app/line/messages/onboarding.py` |
| 主選單 Rich Menu 六格 | LINE Official Account Manager 建立（文字動作 = `TEXT_KEYWORDS`）；六格定義 `app/line/handlers/intents/menu.py`（`DEFAULT_CELLS`） |
| 自由文字 → AI 助理（RAG 檢索 → 判斷事件 → 套用模組） | `intents/assistant.py`、`services/conversation_service.py`、`services/knowledge_service.py`、`domain/retrieval.py`、`docs/ai-assistant-design.md` |
| 我不舒服 → ① 病況諮詢 / 紅旗 → 緊急畫面 | `intents/symptom.py`（入口提示，後續文字走 AI 助理）、`EMERGENCY_KEYWORDS` in `app/line/intents.py` |
| 找醫院 → 急診即時看板（燈號）+ 附近診所 | `intents/hospital.py`、`services/triage_service.py`、`domain/triage_rules.py`、LIFF `/liff/er` |
| 急診動態分流（壅塞 → 分流建議 → 通知 119 / 診所） | `app/scheduler.py` 每 15 分鐘 `TriageService.poll()`、`FindHospitalHandler`、`docs/triage-design.md` |
| 我剛出院 → 已驗證？→ ② 個人化 / 通用版 | `intents/discharge.py` |
| 去哪拿藥 → ③ 藥物諮詢 / 一鍵建立提醒 | `intents/medication.py` |
| 服藥提醒 → 已驗證？→ ④ 服藥小幫手 | `intents/reminder.py`（`requires_verified = True`） |
| 意見回饋（取代真人客服） | `intents/feedback.py`、`services/feedback_service.py`、LIFF `/liff/feedback`、後台 `/admin/feedback` |
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
- 登入效期：只有 access token（`JwtService.issue()`，一般 1 小時、院方 8 小時），到期重新登入；沒有 refresh token 與 session 表。
- AI 助理：新增事件 / 動作改 `app/domain/assistant_schema.py` 與 `AssistantHandler._compose()`；知識內容在後台 `/admin/knowledge` 上傳。
- 新增院所：後台「院所設定」；其他表寫入 `hospital_id` 前呼叫 `HospitalService.require_active()`。
- 病人表單加欄位：只改 `app/domain/intake_schema.py`（必要時加 model 欄位 + migration），LIFF 頁面、驗證、後台標籤自動跟上。
- 衛教個人化：`PatientRecordRepository.latest_discharge_code(user_id)` 取最近一次出院代碼。
- 急診壅塞門檻 / 地區對照：只改 `app/domain/triage_rules.py`；新增通知管道：在 `TriageService._deliver()` 加分支並擴充 `SUBSCRIPTION_KINDS`。
