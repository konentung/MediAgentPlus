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
步驟見 [docs/local-operations.md](docs/local-operations.md) 第 3 節。

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                # 填入 LINE / DB / JWT 設定
```

### 3. 執行

```bash
flask --app wsgi db upgrade         # 建表
flask --app wsgi run --debug        # 開發模式，http://localhost:5000
gunicorn -w 1 -b 0.0.0.0:5000 wsgi:app   # 正式模式（FLASK_ENV=production；多 worker 請設 REDIS_URL）
pytest                              # 測試（不需要 DB）
ruff check . && ruff format .
```

Webhook URL 設定為 `https://<your-domain>/line/webhook`（本機開發可用 ngrok 對外）。
Rich Menu 在 LINE Official Account Manager 建立（2500×1686 六格），每格動作設「文字」：我不舒服 / 找醫院 / 我剛出院 / 去哪拿藥 / 服藥提醒 / 意見回饋（口語化；正式名稱也可當文字關鍵字）。
第一位系統管理員：把自己的 LINE userId 填進 `.env` 的 `SUPER_ADMIN_LINE_USER_IDS`，登入後台後在「人員與角色」指派其他人。
服藥提醒推播與急診資料抓取由程序內排程（APScheduler，`app/scheduler.py`）執行，不需要 cron。

更完整的本機安裝、LINE 後台設定、ngrok、手動測試與常見問題請看 [docs/local-operations.md](docs/local-operations.md)。

## 目錄結構

```
config/settings.py         環境設定（Development / Testing / Production）
wsgi.py                    gunicorn / flask 進入點
app/__init__.py            create_app 應用程式工廠
app/extensions.py          SQLAlchemy / Migrate
app/scheduler.py           APScheduler 排程：服藥提醒推播（5 分鐘）、急診資料抓取（15 分鐘）
app/core/                  例外、統一回應、錯誤處理、DI 容器
app/domain/                Role / Permission / BindingStatus / Intent、Principal、AccessPolicy、
                           intake_schema（病人自填表欄位定義與驗證）、reminder_rules（頻率 → 提醒時間）、
                           triage_rules（急診壅塞燈號門檻、地區代碼）、retrieval（jieba + BM25）、
                           assistant_schema（AI 決策格式）
app/auth/                  JWT（只有 access token，到期重登）、LINE Login OAuth、LIFF 驗證、授權裝飾器、OAuth state
app/repositories/          Repository Protocol，User / Feedback / Hospital / Patient / PatientRecord /
                           Audit / Prescription / Pharmacy / Drug / Schedule / Delivery 皆為 SQLAlchemy 實作
app/services/              UserService / FeedbackService / HospitalService / IntakeService / PatientRecordService /
                           PharmacyService / PrescriptionService / ReminderService / TriageService /
                           KnowledgeService（RAG 知識庫）/ KnowledgeJobService（後台背景匯入 / 算向量）/
                           ConversationService（AI 脈絡與決策）
app/integrations/vision.py 處方箋照片辨識：提示詞與輸出模型，辨識交給 llm.py 的後端
app/integrations/nhi_er.py 健保署急診即時訊息 API 客戶端（找醫院依使用者位置推薦用）
app/integrations/llm.py    OpenAI 結構化輸出客戶端，對話助理與處方辨識共用
data/knowledge/            通用知識種子 Markdown（服務說明等），在後台「衛教資訊 → 新增文件」貼上
data/                      開放資料與知識庫原始檔（不進版控，本機自行放置）：pharmacy/ 藥局藥品 CSV、knowledge/ 種子 Markdown、
                           衛教資料以 PDF 為主，由各院所管理員在後台「資料匯入」上傳
app/utils/crypto.py        FieldCipher：身分證指紋雜湊（HMAC）、處方箋照片加密（Fernet）
app/line/                  Messaging client、webhook、EventDispatcher、IntentRouter、handlers、messages、Rich Menu、
                           state_store（對話狀態：等處方箋照片 / 等位置 / 等藥名）
app/api/v1/                REST API：health / auth / liff / admin / triage（急診看板、分流建議、接收者）/ knowledge（衛教資訊）
app/pages.py               一般網頁（首頁 `/`）
app/liff_pages.py          LIFF 頁面：/liff/intake（病人自填表）、/liff/settings、/liff/prescriptions/<id>、/liff/reminders、
                           /liff/er（急診即時看板，不需登入）
app/admin_pages.py         院方後台頁面：/admin/*（儀表板、病人、工單、人員、衛教資訊、設定）
app/models/                SQLAlchemy 模型：Hospital / User / Feedback / Patient*（自填資料 7 張）/
                           PatientRecord*（院方紀錄 6 張）/ medication（處方、藥局、藥品、提醒排程、推播紀錄）/
                           triage（急診即時快照）/ knowledge（衛教資訊文件與片段、背景工作），
                           migrations/ 由 Flask-Migrate 管理
app/templates/             Jinja 頁面（無獨立前端專案）：base.html、liff/、admin/、auth/、errors/
tests/                     pytest（RBAC、JWT、OAuth、LIFF、webhook、intake、後台、藥局、處方、提醒、急診分流、AI 助理）
```

## 請求生命週期

**LINE Webhook**：`POST /line/webhook` → `WebhookParser` 驗簽 → `EventDispatcher` 依事件類型找 `EventHandler`
→ 建立 `BotContext`（含 `Principal`）→ postback、完全等於功能關鍵字的文字（主選單、找醫院…）直接交給 `IntentRouter`（不經 AI）；**其餘自由文字才交給 AI 助理**
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
| GET | /api/v1/auth/me | 登入 | 身分 + 權限清單 |
| GET | /api/v1/liff/config | 公開 | 回傳 LIFF ID |
| POST | /api/v1/liff/auth | 公開 | id_token / access_token → JWT |
| GET | /api/v1/liff/me | 登入 | 身分 |
| GET | /api/v1/liff/hospitals | 公開 | 啟用中的院所清單（表單「常去的醫院 / 診所」選單） |
| GET | /api/v1/liff/intake | intake:write_own | 表單 schema + 已填資料（身分證遮罩）+ 進度 |
| PUT | /api/v1/liff/intake/steps/{n} | intake:write_own | 存單一步驟；body `partial: true` 為草稿 |
| POST | /api/v1/liff/intake/submit | intake:write_own | 全表驗證 → 存原始資料 → `binding_status=PENDING` |
| GET | /api/v1/liff/profile | 已驗證 + profile:read_own | 病人自己的基本資料 |
| GET | /api/v1/liff/prescriptions[/{id}] | prescription:manage_own | 自己的處方列表 / 單張（含辨識出的藥品清單、warnings；不含 ocr_raw） |
| GET | /api/v1/liff/prescriptions/{id}/image | prescription:manage_own | 原始處方箋照片（解密後回傳） |
| PATCH | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 修改院所 / 日期 / 天數 / 慢箋次數 / 藥品清單 |
| POST | /api/v1/liff/prescriptions/{id}/confirm | prescription:manage_own | `draft` → `confirmed`，並算出慢箋領藥提醒日 |
| DELETE | /api/v1/liff/prescriptions/{id} | prescription:manage_own | 標記 `discarded` |
| POST | /api/v1/liff/prescriptions/{id}/schedules/propose | reminder:manage_own | 依處方各項頻率算出預設提醒時間（不寫入） |
| GET | /api/v1/liff/pharmacies?address= 或 ?lat=&lng= | consult:medication_once | 依縣市 / 鄉鎮市區列健保特約藥局（地址抓不到縣市時用座標反推鄉鎮市區；含今日營業時段） |
| GET | /api/v1/liff/drugs?q= | consult:medication_once | 健保用藥查詢（中英文名 / 成分） |
| GET / POST | /api/v1/liff/reminders | reminder:manage_own | 我的提醒排程（含今日推播狀態）/ 建立多筆 |
| PATCH / DELETE | /api/v1/liff/reminders/{id} | reminder:manage_own | 改時間 / 結束日 / 暫停 / 刪除 |
| GET | /api/v1/liff/reminders/history?days=7 | reminder:manage_own | 推播與回報紀錄 |
| GET | /api/v1/triage/board?lat=&lng=（或 ?area=01） | 公開（rate limit） | 依使用者位置列最近 8 家急救責任醫院 + 燈號，`recommended` = 最近且未壅塞者；`?area=` 為 LIFF 縣市切換用 |
| GET | /api/v1/triage/areas | 公開 | 地區代碼與燈號門檻說明 |
| GET | /api/v1/admin/knowledge[/{id}]、/search?q= | education:content_manage | 衛教資訊文件（以院所為單位：院所管理員只看得到本院的，SUPER_ADMIN 看全部）、搜尋（所有院所共用、本院優先） |
| POST / PATCH / DELETE | /api/v1/admin/knowledge[/{id}] | education:content_manage | 新增 / 修改 / 刪除文件（院所綁自己；通用與別家只有 SUPER_ADMIN） |
| GET | /api/v1/admin/knowledge/hospitals | education:content_manage | 各院所的文件 / 片段 / 已有向量數（SUPER_ADMIN 看全部，院方帳號只有本院） |
| GET | /api/v1/admin/knowledge/jobs[/{id}] | education:content_manage | 統計 + 進行中 / 最近的背景工作 + 片段大小選項與用過的科別（頁面輪詢）；院所管理員只看得到本院的 |
| POST | /api/v1/admin/knowledge/jobs/pdf-import | education:content_manage | 上傳衛教 PDF（multipart `files` 多檔，`department`、`chunk_size` = small / medium / large），背景擷取文字、切段、自動算向量，回 202；一次上傳上限 `KNOWLEDGE_UPLOAD_MAX_MB`（預設 1 GB、最多 500 個檔）。院所管理員一律歸本院；SUPER_ADMIN 用 `hospital_id` 指定院所或留空（通用） |
| POST | /api/v1/admin/knowledge/jobs/rechunk | education:content_manage | 重建片段（body 可帶 `chunk_size` 換大小）：院所管理員重建本院文件並自動重算向量；SUPER_ADMIN 重建全部（向量清空），回 202 |
| POST | /api/v1/admin/knowledge/jobs/embed | reference_data:manage | 替還沒有向量的片段計算 embedding（背景、可重跑），同一時間只跑一個工作 |
| GET | /api/v1/admin/permissions | staff:manage | 權限表 |
| GET | /api/v1/admin/dashboard | patient:read | 待核對數、到期追蹤、開放工單 |
| GET | /api/v1/admin/hospitals?q=&region=&tier=&page=&all= | 院方角色 | 院所表格分頁（每頁 15 筆；all=1 含停用，僅 SUPER_ADMIN） |
| GET | /api/v1/admin/hospitals/options | 院方角色 | 下拉選單用的院所（只有醫院層級，不含診所） |
| POST | /api/v1/admin/hospitals | hospital:manage | 新增院所（`id` 即院所代碼，建立後不可改） |
| POST | /api/v1/admin/hospitals/import | hospital:manage | CSV 批次匯入（multipart `file` 或 JSON `rows`；支援自家範本、健保署特約機構、衛福部醫療機構基本資料三種欄名，後者提供 `departments` 科別），同代碼更新、否則新增；`?only_hospitals=1` 略過診所 |
| GET | /api/v1/admin/hospitals/import/template | hospital:manage | 下載匯入範本 CSV |
| PATCH | /api/v1/admin/hospitals/{id} | hospital:settings / hospital:manage | 修改院所資料；`active` 只有 SUPER_ADMIN 能改 |
| GET | /api/v1/admin/pharmacies?city=&district=&q=&page= | medication:manage | 藥局表格分頁（每頁 15 筆） |
| POST | /api/v1/admin/pharmacies/import | reference_data:manage | 藥局 CSV 匯入（健保署特約醫事機構或衛福部藥局格式，multipart `file` 或 JSON `rows`） |
| GET | /api/v1/admin/drugs?q=&page= | 院方角色 | 藥品主檔表格分頁（每頁 15 筆） |
| POST | /api/v1/admin/drugs/import | reference_data:manage | 健保用藥品項 CSV 匯入（同代號只留最新） |
| GET | /api/v1/admin/users | staff:manage | 人員列表（同院所） |
| PUT | /api/v1/admin/users/{id}/role | staff:manage | 指派角色（同院所限制） |
| GET | /api/v1/liff/feedback | feedback:submit | 意見回饋類別與自己送過的回饋 |
| POST | /api/v1/liff/feedback | feedback:submit | 送出意見回饋（不需填過病人表單） |
| GET | /api/v1/admin/feedback | feedback:manage | 意見回饋列表（`?status=open|resolved`） |
| PATCH | /api/v1/admin/feedback/{id} | feedback:manage | 標記已處理 / 重新開啟 |
| GET | /api/v1/admin/patients | patient:read | 病人列表 `?status=pending/active/locked/all&q=`，只含本院新增 / 建檔的病人（SUPER_ADMIN 全部） |
| GET | /api/v1/admin/patients/{user_id} | patient:read | 自填資料 + 本院紀錄 + 就診 / 風險 / 追蹤 / 備註 / 歷次送出資料 |
| POST | /api/v1/admin/patients/{user_id}/lock、/unlock | patient:verify | 加入 / 移出黑名單（鎖定 / 解除鎖定帳號，寫入稽核） |
| GET | /api/v1/admin/audit | audit:read | 稽核紀錄（同院所） |
| GET | /api/v1/admin/patients/unclaimed?phone=&name=&last4=（至少一個） | patient:verify | 搜尋病人：精準找還沒被任何院所加入的病人（每個欄位完全相等、不做模糊搜尋） |
| POST | /api/v1/admin/patients/{user_id}/request-hospital-confirmation | patient:verify | 把病人加入本院並推播 LINE 請病人自己按「是，我在這裡就醫」；別家新增的病人回 409 |
| POST | /api/v1/admin/patients/{user_id}/verify | patient:verify | 核對身份 → 建檔 → VERIFIED + 角色（病人尚未確認在本院就醫時回 409） |
| POST | /api/v1/admin/patients/{user_id}/reject | patient:verify | 退回修正（推播原因） |
| PATCH | /api/v1/admin/patients/{user_id}/profile | patient:write | 院方修正自填資料（留 corrections） |
| POST / PATCH | /api/v1/admin/patients/{user_id}/encounters[/{id}] | encounter:manage | 就診 / 住院紀錄、出院代碼 |
| POST / PATCH | /api/v1/admin/patients/{user_id}/flags[/{id}] | patient:write | 風險警示 |
| POST / PATCH | /api/v1/admin/patients/{user_id}/follow-ups[/{id}] | patient:write | 追蹤事項 |
| POST | /api/v1/admin/patients/{user_id}/notes | patient:read | 內部備註 |

### 網頁

| Path | 說明 |
|---|---|
| / | 首頁：LINE 登入、進入基本資料 / 後台 |
| /liff/intake | 病人自填表（6 步、可續填），不問院所。**只在 LINE 內透過 LIFF 開啟**：`https://liff.line.me/<LIFF_ID>/intake`（加好友後由歡迎訊息 / Rich Menu 帶入） |
| /liff/settings | 病人設定頁（Rich Menu「設定」→ LIFF）：綁定狀態、資料摘要、進入表單 |
| /liff/prescriptions/{id} | 處方辨識結果：看原始照片、修改藥品清單、確認、進入建立提醒 |
| /liff/er?area=01 或 ?lat=&lng= | 急診即時看板（公開資料，不需登入，LINE 內外都能開）：「用我的位置」依距離推薦，或看各縣市燈號與等候人數 |
| /admin/knowledge | 院方後台：衛教資訊文件維護、搜尋（以院所為單位，院所管理員只看得到本院的文件，列表顯示每篇切成幾個片段）；系統管理員另有「各院所的衛教資訊」統計，可點進去看該院所的文件；匯入在「資料匯入」頁 |
| /liff/reminders?prescription={id} | 服藥提醒總覽 / 新增 / 編輯；帶 `prescription` 會先載入該處方的建議排程 |
| /admin | 院方後台儀表板；未登入 / 過期 / 非院方帳號一律導向 `/admin/login` |
| /admin/login | 後台登入頁（「使用 LINE 登入」按鈕），`?next=` 登入後回原頁、`?error=` 顯示原因。LIFF 端不走這頁 |
| /admin/patients、/admin/patients/{user_id} | 病人列表 / 詳情（核對、修正、就診、風險、追蹤、備註） |
| /admin/feedback、/admin/staff | 意見回饋、人員角色 |
| /admin/pharmacies、/admin/drugs | 藥局 / 藥品參考資料查詢 |
| /admin/imports | 資料匯入：院所 / 藥局 / 藥品 CSV、衛教 PDF（選科別與片段大小，院所管理員匯入的歸本院；上傳顯示進度），以及衛教資訊背景工作（匯入進度、換片段大小重建片段；全庫計算向量只有系統管理員） |
| /admin/settings、/admin/permissions、/admin/audit | 院所設定（本院資料、所有院所管理與匯入）、權限表查閱、稽核紀錄 |

後台頁面骨架不需登入即可開啟，頁內 JS 用 `sessionStorage` 的 JWT 打 API，真正的授權在 API 端。

## 安全機制摘要

- **身分證字號**：**不保存明碼也不保存密文**，只存末四碼與 HMAC 指紋（`PATIENT_DATA_KEY`，唯一索引，供重複檢查）；任何 API、任何後台角色都只看得到遮罩 `******1234`。院方核對身份以末四碼 + 姓名 + 生日對照 HIS。
- **院所隔離**：`AccessPolicy.hospital_scope()` 統一判斷，院方帳號沒有 `hospital_id` 直接拒絕（不會退化成看全部）。草稿對院所不可見，送出後才進待核對清單。
- **狀態機**：送出後病人端唯讀；退回清除送出時間，重新送出前院方不能核對；鎖定 / 解鎖只能由院方操作並留稽核。
- **登入效期**：只有 access token、沒有 refresh token 與伺服器端 session。一般使用者 `JWT_ACCESS_EXPIRES_SECONDS`（預設 1 小時，LIFF 到期自動用 `liff.init()` 換新）、院方角色 `JWT_STAFF_ACCESS_EXPIRES_SECONDS`（預設 8 小時，到期導回後台登入頁）。每個請求都重新從資料庫載入角色與綁定狀態，帳號鎖定或降權立即生效。
- **Rate limit / OAuth state**：`app/core/ratelimit.py`、`app/auth/state_store.py`。未設 `REDIS_URL` 用單程序記憶體，設定後改用 Redis（多 worker / 多機共用）。
- **設定保護**：`FLASK_ENV` 未設定視為 production，缺必要環境變數或使用範例金鑰會啟動失敗；`TRUSTED_PROXY_COUNT` 設定信任的 proxy 層數才會採用 `X-Forwarded-*`。
- **回應標頭**：`X-Content-Type-Options: nosniff`、`Referrer-Policy`，後台加 `X-Frame-Options: DENY`。
- **錯誤處理**：500 不外露例外內容，log 只記型別與 traceback（避免 SQL 參數夾帶個資）。

## 角色與權限

後台「權限表」頁（`GET /api/v1/admin/permissions`）可看完整矩陣。摘要：

| 角色 | 說明 | 重點權限 |
|---|---|---|
| guest | 加好友但未驗證 | 病況諮詢、找醫院、藥物單次查詢、通用衛教、意見回饋 |
| patient / family | 驗證通過（本人 / 家屬） | guest 全部 + 個人化衛教、服藥提醒、個資讀寫 |
| hospital_staff | 院內人員（管病人） | 意見回饋處理、對話讀取、病患讀取 / 修正 / 核對建檔、就診紀錄 |
| medical_staff | 醫護（管藥） | 病患讀取（遮罩）、對話讀取、處方 / 服藥紀錄管理（`medication:manage`，後台頁面待做）、藥局查詢 |
| hospital_admin | 院所管理員 | hospital_staff + medical_staff + 衛教資訊管理、權限表、人員管理、院所設定、Rich Menu、稽核 |
| super_admin | 系統管理員（開發 / 維運方，不屬於任何院所） | 全部 + 院所管理、參考資料匯入、系統管理 |

角色分三方：用戶端（guest / patient / family）、院方（hospital_staff / medical_staff / hospital_admin，必須有所屬院所）、
系統方（super_admin）。`Role.is_end_user / is_hospital_staff / is_system` 對應三組，`is_staff` = 院方 + 系統方（可登入後台）。

院所資料隔離由 `AccessPolicy.can_access_hospital()` 統一判斷；角色指派範圍由 `ROLE_ASSIGNABLE_BY` 限制。

## 資料庫

`Hospital`（`hospitals`，`id` 即院所代碼，其他表的 `hospital_id` 都是指向它的外鍵）、`User`、
`Feedback`、`AuditLog`、病人自填資料（`patient_profiles` 等 7 張）、院方紀錄（`patient_records` 等 6 張）、
藥物諮詢 / 服藥小幫手（`prescriptions`、`pharmacies`、`drugs`、`medication_schedules`、`reminder_deliveries`）都是 SQLAlchemy 模型（`app/models/`），`ServiceContainer.build()` 用對應的
`SqlAlchemy*Repository` 讀寫 PostgreSQL，`migrations/` 由 Flask-Migrate 管理。身分證字號只存
末四碼與 HMAC 指紋（金鑰 `PATIENT_DATA_KEY`）供搜尋與重複檢查，沒有明碼或密文欄位。**每張表、每個欄位的說明見 `docs/database-models.md`**；設計背景見
`docs/patient-form-design.md`、`docs/medication-design.md`。`OAuthStateStore`、rate limit 與 LINE 對話狀態在未設 `REDIS_URL` 時用單程序記憶體，設定後改用 Redis
（`app/auth/state_store.py`、`app/core/ratelimit.py`、`app/line/state_store.py`）。

### 藥物諮詢 / 服藥小幫手

- 藥局與藥品是唯讀參考資料，系統管理員在後台「藥局」/「藥品」頁上傳 CSV（可重跑，upsert）：
  `data/pharmacy/A21030000I-D21005-001.csv`（健保署特約藥局）、`35_2.csv`（衛福部藥局，只補非特約）、
  `A21030000I-E41001-001.csv`（健保用藥）。
- 找藥局不用地圖服務：LINE 位置訊息自帶地址，解析縣市 / 鄉鎮市區後比對 `pharmacies.city / district`。
- 處方箋辨識與 AI 對話都走 **OpenAI API**（`OPENAI_API_KEY`；對話 `gpt-4.1-mini`、看圖 `gpt-4.1`，可用 `AI_MODEL` / `OCR_MODEL` 覆寫），透過 `app/integrations/llm.py` 的結構化輸出直接對到 pydantic 模型。
- 衛教資訊由各院所各自管理、檢索時不分院所：每份文件歸屬一家院所（或通用），所有病人都檢索得到，病人自己院所的內容優先、本院沒有相關內容時由其他院所補上。資料來源只有 PDF 與後台手動新增：院所管理員在後台 `/admin/imports` 上傳衛教 PDF（`pypdf` 擷取文字，選科別與片段大小，歸本院），匯入後自動切段並計算向量；想換片段大小可直接「重建片段」（背景執行、頁面輪詢進度，狀態存 `knowledge_jobs`）。掃描影像型 PDF 沒有文字層，無法匯入。大量 PDF 不想經瀏覽器上傳時可用 `python scripts/import_pdfs.py data`（同一套匯入邏輯，可重跑）。
- 衛教資訊檢索 = PostgreSQL 全文檢索（jieba 斷詞 tsvector）+ pgvector 語意檢索（`EMBEDDING_MODEL` 預設 `text-embedding-3-small`）以 RRF 合併；migration 會 `CREATE EXTENSION vector`，本機 PostgreSQL 需已安裝 pgvector。
- 服藥提醒由 `app/scheduler.py`（APScheduler）每 5 分鐘執行 `ReminderService.run_due()`；同一排程同一時段只推播一次，
  同一人同一批多顆藥合併成一則，封鎖官方帳號（`users.push_enabled=false`）的人自動略過。

新增欄位或模型時：改 `app/models/`、跑 `flask --app wsgi db migrate -m "..."` 產生 migration、
人工檢查產生的檔案（尤其是 Enum / JSONB 型別是否正確）、再 `flask --app wsgi db upgrade`。
`pytest` 不需要真的 PostgreSQL，測試設定用 in-memory SQLite（`config/settings.py` 的 `TestingConfig`）。
