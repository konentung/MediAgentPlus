# 藥物諮詢 + 服藥小幫手設計（Roadmap 第 4、5 項）

> 狀態：**已實作（2026-09-26）**。本文件把兩項功能合併設計，因為兩者共用同一份「處方箋」資料：
> 拍照辨識出來的處方 → 一邊查「去哪拿藥」（藥局），一邊產生「何時吃藥」（提醒）。
> §0 是使用者確認後的實作決策（與下方原始設計不同處以 §0 為準）；程式位置見 CLAUDE.md「藥物諮詢 / 服藥小幫手」。

使用者需求（原文）：

| 功能 | 敘述 |
|---|---|
| 藥物諮詢 | 根據醫生開的處方箋給予購買藥物的地點資訊 |
| 服藥小幫手 | 根據病人的處方箋或者是症狀，依據藥物的種類提醒該病人需要何時服用藥物 |

具體要做到：用 LINE 內建拍照把處方箋傳給機器人 → 後端辨識處方內容 → 告訴病人哪間藥局可以領 → 依藥物種類建立服藥提醒並定時推播。

## 0. 實作決策（使用者確認後，2026-09-26）

| 議題 | 決策 | 落地方式 |
|---|---|---|
| 辨識模型 / 費用 | **OpenAI API**（`OPENAI_API_KEY`，`OCR_MODEL` 預設 `gpt-4.1`）；原先的 Claude 訂閱 / Anthropic SDK 後端已於 2026-09-27 移除 | `app/integrations/llm.py` 的 `OpenAiClient` + `vision.py` 的提示詞與輸出模型 |
| 地理編碼 | **不用地圖服務** | LINE `LocationAction` 的位置訊息自帶地址，解析縣市 / 鄉鎮市區比對 `pharmacies.city / district`；藥局表不存經緯度。地址抓不到縣市（英文地址 / 沒有地址）時用經緯度找最近的鄉鎮市區中心點（`app/resources/tw_districts.csv`，368 筆離線資料） |
| 藥局資料 | 存資料表 | `pharmacies`（健保署特約 + 衛福部非特約），後台「參考資料」CSV 匯入、可重跑 |
| 藥品資料 | 匯入一次即可，不排定期更新 | `drugs`（健保用藥 E41001，同代號只留最新），後台 CSV 匯入 |
| 提醒對象 | **一般人也可用**，不需先驗證基本資料 | `REMINDER_MANAGE_OWN` / `PRESCRIPTION_MANAGE_OWN` 放進 `_PUBLIC`，`ReminderHandler.requires_verified=False` |
| 辨識中的提示 | 不發「辨識中」訊息，用 **LINE loading animation** | `line_client.show_loading_animation(chat_id, 60)`，辨識完直接 reply；reply token 過期改 push |
| 照片保留 | **完整保留**，不自動清除 | `prescriptions.image_path` 指向 `MEDIA_ROOT/<user>/<id>.bin`（Fernet 加密），LIFF 可看原圖 |
| 慢箋提醒 | 要做 | 確認處方時算 `refill_reminder_on = 開立日 + 給藥天數 × 第 n 次 − 3 天`，`run-due` 一併推播、只提醒一次 |
| 資料模型 | **最簡化** | 5 張表：`prescriptions`（items 為 JSON，照片欄位直接掛上）、`pharmacies`、`drugs`、`medication_schedules`、`reminder_deliveries` + `users.push_enabled`；不做 `prescription_items` / `patient_media` / `drug_licenses` / `reminder_preferences` |

其餘（對話狀態、cron `run-due`、合併推播、封鎖略過、免責文字、不抄病人姓名）皆依原設計實作。

## 1. 設計原則

1. **處方箋是共用核心**：`prescriptions` + `prescription_items` 由「拍照辨識」或「手動輸入」產生，藥局推薦與服藥提醒都從它衍生，之後串 HIS 只是多一種 `source`。
2. **辨識結果一定要病人確認**：AI 辨識（OCR）不可直接拿來排提醒。流程固定為「辨識 → 顯示結果 → 病人確認或修改 → 才建立提醒」，避免辨識錯誤造成錯誤用藥提醒。
3. **不做醫療判斷**：機器人只做「處方上寫什麼、幾點該吃、哪裡能領」，不做劑量建議、交互作用判讀；異常一律導向原院所藥局（可用意見回饋反映問題）。
4. **沿用既有架構**：`Intent.MEDICATION` / `Intent.REMINDER` 兩個 stub 直接擴充；排程走 Flask CLI + cron，不引入 Celery；外部呼叫（OpenAI、LINE）全部包成容器內的 client，測試以假物件替換。
5. **資料最少化**：處方箋照片含個資，預設保留 30 天供院方核對後自動清除；GUEST（未驗證）使用者的處方 7 天後清除。

## 2. 使用者流程（LINE 端）

### 2.1 藥物諮詢：拍處方箋 → 找藥局

```
Rich Menu「去哪拿藥」
  └─ Quick Reply：[📷 拍處方箋] [🖼 從相簿選] [🔍 輸入藥名] [📍 找附近藥局] [意見回饋]
       │  （camera / cameraRoll / location 都是 LINE 內建 Quick Reply action）
       ▼
  病人送出圖片訊息
       │  後端：下載圖片 → 存 patient_media → 多模態模型辨識（結構化輸出）
       ▼
  回覆 Flex「辨識結果」：院所、開立日、每項藥名 / 劑量 / 頻率 / 天數、慢箋第 n/m 次
     按鈕：[✅ 正確，找藥局] [✏️ 有誤，修改]（開 LIFF /liff/prescriptions/<id>） [⏰ 建立服藥提醒]
       │
       ▼ 按「找藥局」
  Quick Reply：[📍 分享目前位置] [🏠 用我填的住址]（已驗證且有住址才顯示）
       │
       ▼ 位置訊息 / 住址
  回覆 Flex Carousel（最多 5 間）：藥局名、距離、地址、電話、今天營業時段、是否健保特約
     按鈕：[導航]（Google Maps 連結） [撥打電話]（tel:） ＋ 最近一間附上 LINE Location 訊息
  附註：處方含管制藥品 → 額外提醒「管制藥品請回原院所藥局或持處方向健保特約藥局確認」
```

未拍處方箋、直接按「找附近藥局」也可以（只依位置找健保特約藥局，不帶處方）。
「輸入藥名」→ 查 `drugs` 資料表回覆藥品基本資料（中英文名、劑型、成分、仿單連結），查無 → 提示改用英文學名。

### 2.2 服藥小幫手：處方 → 提醒 → 推播 → 回報

```
處方確認後按「建立服藥提醒」（或 Rich Menu「服藥提醒」→「建立提醒」）
  │  需 binding_status=VERIFIED（現有 ReminderHandler.requires_verified 不變）
  ▼
後端依每項藥的頻率 + 飯前 / 飯後 / 睡前 + 病人偏好用餐時間，算出預設時間點
  例：bid + pc → 08:30、18:30；tid → 08:00、12:00、18:00；hs → 22:00；prn → 不排提醒
  ▼
開 LIFF /liff/reminders：列出每項藥的時間、起訖日（起 = 今天，訖 = 今天 + 處方天數），病人可調整後儲存
  ▼
APScheduler 每 5 分鐘 `ReminderService.run_due()`（`app/scheduler.py`）：
  找出「時間到、尚未送過」的提醒 → 同一人同一時段的多顆藥合併成 1 則 Flex 推播（省推播額度）
     Flex：藥名 / 劑量清單 + [✅ 已服用] [⏰ 30 分鐘後再提醒] [⏭ 這次跳過]
  ▼
病人按鈕 → postback 寫回 reminder_deliveries.status；未回應不再追發（v1）
Rich Menu「服藥提醒」→ 查看提醒（今日清單 + 狀態）/ 暫停 / 恢復 / 開 LIFF 編輯
```

「根據症狀」的提醒：v1 只做「處方箋」與「手動輸入」兩種來源；依症狀推論該吃什麼藥屬於醫療判斷，且依賴 Roadmap 第 2 項的病況諮詢，這裡不做（見 §10）。

## 3. 資料來源

`data/pharmacy/` 已經有 7 份政府開放資料，這次用得到的對應如下：

| 檔案 | 內容 | 用途 | 匯入到 |
|---|---|---|---|
| `A21030000I-D21005-001.csv` | 健保署特約醫事機構（含藥局，有 `固定看診時段`、電話、地址、`終止合約日期`） | 藥局主檔（健保特約 = 可持處方領藥） | `pharmacies` |
| `35_2.csv` | 衛福部藥局基本資料（機構狀態、是否健保特約） | 補「已歇業」狀態、非特約藥局（找 OTC 用） | `pharmacies`（同名同址合併） |
| `A21030000I-E41001-001.csv` | 健保用藥品項（藥品代號、中英文名、成分、劑型、**ATC 代碼**、給付規定） | 藥名正規化、依 ATC 判斷藥物種類 | `drugs` |
| `36_2.csv` | 藥品許可證（**管制藥品分類級別**、用法用量、適應症） | 管制藥品警示、用法用量顯示 | `drug_licenses` |
| `39_2.csv` | 仿單 / 外盒圖檔連結 | 藥品查詢回覆附仿單連結 | `drug_licenses.insert_url` |
| `42_2.csv` | 藥品外觀（形狀、顏色、刻痕、圖檔） | 之後做「藥袋 / 藥錠辨識」核對用，v1 不匯入 | （暫不） |
| `41_2.csv` | 許可證 ↔ ATC 對照 | 補 `drug_licenses.atc_code` | `drug_licenses` |

兩份藥局資料都**沒有經緯度**，匯入時需要地理編碼（見 §6.4）。這些 CSV 在 git 裡有 444k 行，建議之後移到 Git LFS 或改為下載腳本，不在本次範圍。

## 4. 資料模型

新增 8 張表 + 既有表加欄位。全部沿用 `db.Model, TimestampMixin`、`JSONB().with_variant(JSON(), "sqlite")`、`sa.Enum(native_enum=False)`。

### 4.1 處方（跟著 user 走）

**`prescriptions`**

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | String(32) PK | uuid hex |
| user_id | FK users.id CASCADE | |
| hospital_id | FK hospitals.id SET NULL, nullable | 辨識出的院所若能對到 `hospitals` 就填 |
| hospital_name_raw | String(100) | 辨識出的院所名稱原文 |
| source | Enum `ocr` / `manual` / `his_sync` | |
| status | Enum `draft` / `confirmed` / `discarded` | `draft` = 辨識完成待病人確認 |
| issued_on | Date | 開立日 |
| days_supply | Integer | 給藥天數 |
| is_chronic_refill | Boolean | 慢性病連續處方箋 |
| refill_seq / refill_total | Integer | 第 n 次 / 共 m 次 |
| media_id | FK patient_media.id SET NULL | 原始照片 |
| ocr_model | String(50) | 用哪個模型辨識 |
| ocr_raw | JSON | 模型結構化輸出原文（除錯 / 稽核） |
| ocr_warnings | JSON | 模型回報的不確定處（例如「劑量模糊」） |
| confirmed_at | DateTime(tz) | |

**`prescription_items`**

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | String(32) PK | |
| prescription_id | FK CASCADE | |
| position | Integer | |
| drug_name_raw | String(150) | 辨識原文 |
| drug_name | String(150) | 正規化後（對到 `drugs` 就用其英文名） |
| nhi_code | FK drugs.nhi_code SET NULL, nullable | |
| dose_text | String(50) | 「1 顆」「5 mg」 |
| frequency_code | String(8) | 沿用 `intake_schema.MED_FREQUENCY` 的 `qd/bid/tid/qid/hs/prn`，再加 `q4h/q6h/q8h/q12h/qw` |
| timing | String(8) nullable | `ac`（飯前）/ `pc`（飯後）/ `hs`（睡前）/ `stat` |
| route | String(16) | po / topical / … |
| duration_days | Integer | |
| quantity | String(30) | 總量原文 |
| instructions | String(200) | 其他醫囑原文 |
| controlled_level | Integer nullable | 由 `drug_licenses` 帶入，1–4 級 |
| confidence | Numeric(3,2) | 模型對此項的信心 |

**`patient_media`**（CLAUDE.md 已列為「尚未做」，這次補上）

| 欄位 | 說明 |
|---|---|
| id, user_id FK | |
| kind | `prescription` / `drug_bag` |
| line_message_id | LINE 訊息 id（同一張圖不重複下載） |
| content_type, size_bytes, sha256 | |
| storage_key | 檔案位置（v1：`MEDIA_ROOT/<user_id>/<id>.bin`，以 `FieldCipher` 加密後落地；之後可換 S3） |
| expires_at | 到期由 `flask media purge` 刪除 |

### 4.2 藥局與藥品（全系統共用，唯讀參考資料）

**`pharmacies`**

| 欄位 | 說明 |
|---|---|
| id | String(20) PK：健保醫事機構代碼；衛福部資料沒有代碼者用 `MOHW-<sha1(name+address)[:12]>` |
| name, phone, address, city, district | |
| lat, lng | Numeric(9,6)，地理編碼結果；NULL = 尚未編碼，不會出現在搜尋結果 |
| nhi_contracted | Boolean，健保特約（可持處方領藥） |
| kind | `藥師自營` / `藥劑生自營` / 其他 |
| opening_hours | JSON：`{"mon": ["am","pm","night"], ...}`，由「固定看診時段」文字解析 |
| contract_end | Date nullable，健保合約終止日 |
| hospital_id | FK hospitals.id nullable，院內藥局才填（v1 不會有資料，先留欄位） |
| active | Boolean（歇業 / 合約已終止 = False） |
| source, imported_at | |

索引：`(city, district)`、`(lat, lng)`；距離查詢用 bounding box 先篩再用 Haversine 排序，**不需要 PostGIS**。

**`drugs`**（來源：健保用藥品項）

| 欄位 | 說明 |
|---|---|
| nhi_code | String(12) PK，藥品代號 |
| name_en, name_zh, ingredient, dosage_form, category | |
| atc_code | String(8)，index |
| valid_from, valid_to | 只保留最新有效列（CSV 同代號多列是歷次支付價） |
| license_no | 對到 `drug_licenses`（透過超連結 licId 或英文名比對，v1 允許 NULL） |

**`drug_licenses`**（來源：36_2 + 41_2 + 39_2）

| 欄位 | 說明 |
|---|---|
| license_no | String(40) PK，許可證字號 |
| name_zh, name_en, indications, dosage_form, usage_text | |
| controlled_level | Integer nullable，管制藥品分類級別 |
| atc_code | |
| insert_url | 仿單連結 |
| cancelled | Boolean，已註銷 |

藥名模糊比對：PostgreSQL 開 `pg_trgm` 擴充套件，在 `drugs.name_en`、`drugs.name_zh` 建 GIN index，`similarity() >= 0.35` 取最佳。測試環境 SQLite 沒有 pg_trgm，`DrugCatalogService` 對 SQLite 退化成 `LIKE` 前綴比對（只為了測試能跑）。

### 4.3 提醒（跟著 user 走）

**`medication_schedules`**

| 欄位 | 說明 |
|---|---|
| id, user_id FK | |
| prescription_item_id | FK SET NULL nullable，手動建立為 NULL |
| drug_name, dose_text | 快照（處方刪除後提醒仍可讀） |
| frequency_code, timing | 同 `prescription_items` |
| times | JSON `["08:30","18:30"]`，台灣時區的當日時間點 |
| start_date, end_date | Date；`end_date` NULL = 長期 |
| active | Boolean |
| paused_until | Date nullable |
| origin | `prescription` / `manual` |

**`reminder_deliveries`**

| 欄位 | 說明 |
|---|---|
| id | |
| schedule_id FK CASCADE | |
| scheduled_for | DateTime(tz)，這次提醒的名目時間 |
| sent_at | |
| status | `pending` / `sent` / `taken` / `snoozed` / `skipped` / `failed` |
| snooze_until | DateTime(tz) nullable |
| responded_at | |
| error | String(200)，推播失敗原因 |

`UNIQUE(schedule_id, scheduled_for)` 是防重複推播的關鍵；cron 重跑或多台機器同時跑都不會送兩次。

**`reminder_preferences`**

| 欄位 | 說明 |
|---|---|
| user_id PK FK | |
| breakfast_at, lunch_at, dinner_at, bedtime_at | Time，預設 08:00 / 12:00 / 18:00 / 22:00 |
| ac_offset_min, pc_offset_min | 飯前提前 / 飯後延後分鐘數，預設 30 / 30 |

### 4.4 既有表加欄位

- `users.push_enabled`（Boolean, default True）：`UnfollowHandler` 設 False、`FollowHandler` 設 True；`run-due` 與所有主動推播都要檢查。這就是 `follow.py` 那個 TODO。
- `patient_profiles.address_lat / address_lng`：「用我填的住址」找藥局時做一次地理編碼並快取；住址欄位變更時清空。

## 5. 對話狀態（兩步式互動）

「按了拍照 → 才傳圖片」「按了找藥局 → 才分享位置」中間隔了一則訊息，後端要記得使用者正在做什麼。新增 `ConversationStateStore`（`app/line/state_store.py`），介面比照 `OAuthStateStore`：

```python
class ConversationStateStore(Protocol):
    def set(
        self, line_user_id: str, action: str, payload: dict, ttl_seconds: int = 600
    ) -> None: ...
    def pop(self, line_user_id: str) -> tuple[str, dict] | None: ...
```

- 有 `REDIS_URL` 用 Redis（key `conv:<user_id>`，TTL 10 分鐘），否則單程序記憶體版；跟現有 state store 一樣的取捨。
- 狀態值：`await_prescription_photo`、`await_location:pharmacy`（payload 帶 `prescription_id`）、`await_location:hospital`、`await_drug_name`。
- 圖片或位置訊息**沒有**對應狀態時不猜：圖片 → Quick Reply「這是處方箋，幫我辨識」（postback 帶 `message_id`，按下再去 LINE 下載內容）；位置 → Quick Reply「找藥局」「找醫院」（postback 帶 `lat`、`lng`）。這樣不會對每張隨手傳的圖都花一次模型呼叫。

## 6. 服務與外部整合

### 6.1 `PrescriptionOcrService`（`app/services/prescription_ocr_service.py`）

輸入圖片 bytes → 回 `PrescriptionExtraction`（pydantic v2 model，專案已裝 pydantic 2.13，line-bot-sdk 走 `pydantic.v1` 相容層互不影響）：

```python
class ExtractedItem(BaseModel):
    drug_name_raw: str
    drug_name_en: str | None
    dose_text: str | None
    frequency_code: Literal[
        "qd", "bid", "tid", "qid", "hs", "prn", "q4h", "q6h", "q8h", "q12h", "qw", "other"
    ]
    timing: Literal["ac", "pc", "hs", "stat"] | None
    route: str | None
    duration_days: int | None
    quantity: str | None
    instructions: str | None
    confidence: float  # 0–1


class PrescriptionExtraction(BaseModel):
    is_prescription: bool  # False → 回覆「這張看起來不是處方箋」
    hospital_name: str | None
    issued_on: date | None
    days_supply: int | None
    is_chronic_refill: bool
    refill_seq: int | None
    refill_total: int | None
    items: list[ExtractedItem]
    warnings: list[str]  # 模型自己標出的模糊處
```

- 呼叫走 `app/integrations/llm.py` 的 `OpenAiClient`（Chat Completions structured outputs，圖片以 base64 data URL 傳入），模型由 `OCR_MODEL` 設定；系統提示固定：只抄寫處方上的內容、不補腦、不確定就降低 confidence 並寫進 warnings、**不輸出病人姓名 / 身分證**。
- `output_config={"effort": "low"}`，`max_tokens` 4096，逾時 `OCR_TIMEOUT_SECONDS=25`（gunicorn worker 預設 30 秒）。逾時或 API 錯誤 → `ExternalServiceError`，LINE 端回「辨識失敗，請重拍或改用手動輸入」。
- 辨識完成後由 `DrugCatalogService.match()` 把每項 `drug_name_raw` 對到 `drugs`，帶回 `nhi_code`、ATC、`controlled_level`。
- 同步回覆 vs 推播：v1 **同步**在 webhook 內完成（一般 5–15 秒，reply token 有效期內），不消耗推播額度；若實測常逾時，改成先 reply「辨識中」再 push 結果。
- 測試：`tests/conftest.py` 加 `FakeVisionClient`，回固定 `PrescriptionExtraction`；任何測試都不打真 API。
- 成本參考：一張處方照片約 1.5k 輸入 + 0.5k 輸出 token，gpt-4.1 約 $0.01 / 張。

### 6.2 `DrugCatalogService`

- `match(name_raw) -> DrugMatch | None`：先精確比對英文名 / 中文名，再 `pg_trgm` 相似度；回 `nhi_code`、`atc_code`、`controlled_level`、`dosage_form`。
- `lookup(query) -> list[DrugSummary]`：「輸入藥名」用，回最多 5 筆 + 仿單連結。
- `category_hint(atc_code) -> str`：ATC 第一層 → 中文類別（A 消化代謝、C 心血管、N 神經…），提醒訊息裡顯示「（降血糖藥）」讓病人知道是哪一類。**不**做用藥建議。

### 6.3 `PrescriptionService`

- `create_from_image(user, media) -> Prescription(draft)`；`update_items()`、`confirm()`、`discard()`、`list_for_user()`。
- `purge_expired()`：GUEST 的 draft / confirmed 超過 7 天、任何人的 draft 超過 3 天 → 連同 media 刪除。
- GUEST 也能用（對應 `CONSULT_MEDICATION_ONCE` 的「單次查詢不需基本資料」），但只有 `VERIFIED` 才能從處方建提醒。

### 6.4 `PharmacyLocatorService` + `GeocodingClient`

- `nearby(lat, lng, *, radius_km=3, limit=5, nhi_only=True, prescription=None)`：bounding box 篩選 → Haversine 距離 → 排序（距離 → 現在營業中優先）；`prescription` 含管制藥品時在回覆加註警語。半徑內沒有 → 擴到 10 km 再試一次。
- `by_profile_address(user)`：讀 `patient_profiles` 住址 → 地理編碼（快取到 `address_lat/lng`）→ `nearby()`。
- `GeocodingClient`（`app/integrations/geocoding.py`）：v1 用 Google Geocoding API（`GOOGLE_MAPS_API_KEY`），介面抽象好之後可換 TGOS（政府免費，需申請）。只在 CLI 匯入與住址查詢時呼叫，主要查詢路徑不打外部 API。
- 藥局匯入 `flask pharmacy import <csv>`：自動辨認健保署 / 衛福部欄位（比照 `HospitalService.IMPORT_COLUMN_ALIASES`）；`flask pharmacy geocode --limit 500`：分批補經緯度（Google 免費額度每月 1 萬次，全台 8k 家藥局一次跑完即可）。

### 6.5 `MedicationScheduleService`

- `propose_from_prescription(user, prescription) -> list[ScheduleDraft]`：頻率 × timing × `reminder_preferences` → 時間點；`prn` 不排；`other` 要病人自己填。對照表放 `app/domain/reminder_rules.py`（單一來源，LIFF 也用它顯示預設值）：

| frequency | timing | 時間點（以偏好 08:00 / 12:00 / 18:00 / 22:00、飯前 −30、飯後 +30 為例） |
|---|---|---|
| qd | – / pc | 08:30 |
| qd | ac | 07:30 |
| qd | hs | 22:00 |
| bid | pc | 08:30、18:30 |
| tid | pc | 08:30、12:30、18:30 |
| qid | – | 08:00、12:00、18:00、22:00 |
| q12h | – | 08:00、20:00 |
| q8h / q6h | – | 從早餐時間起等距 |
| hs | – | 22:00 |
| prn | – | 不建立提醒，回覆說明 |

- CRUD、`pause(until)`、`resume()`；`end_date` 到了自動不再產生 deliveries。

### 6.6 `ReminderDispatchService`

- `run_due(now)`：
  1. 找 `active` 且日期在區間內、`paused_until` 未生效的 schedules，對每個 `times` 算出「今天的名目時間」落在 `[now − REMINDER_WINDOW_MINUTES, now]` 的項目（預設 15 分鐘，容許 cron 延遲）。
  2. `INSERT ... ON CONFLICT DO NOTHING` 進 `reminder_deliveries`（status `pending`）；只處理這次成功插入的列 → 天然去重。
  3. 加上 `snooze_until <= now` 的 `snoozed` 列。
  4. 依 `user_id` 分組，**同一人同一批合併成一則 Flex**（3 顆藥 = 1 則推播，而非 3 則）；`users.push_enabled=False` 或 `line_user_id` 為空 → 標 `skipped`。
  5. `container.line_client.push()`；成功 → `sent`，失敗 → `failed` + error，**不**丟例外（比照 `_notify` 慣例）。
- `respond(delivery_id, action)`：`taken` / `snoozed`（`snooze_until = now + 30 min`，最多 3 次）/ `skipped`；由 `ReminderHandler` 的 postback 呼叫。
- CLI：`flask reminder run-due [--dry-run] [--now 2026-09-26T08:30+08:00]`，外部 cron `*/5 * * * *`。第 3 項「衛教每日任務」之後共用這個 CLI 群組。
- 時間一律以 `app/utils/clock.py` 為準，補一個 `now_tw()`。

### 6.7 推播額度（重要）

LINE 官方帳號免費方案每月只有 200 則推播。一位病人 tid × 30 天 = 90 則，10 位病人就爆量。上線前要升級 Light / Standard 方案，或把提醒改成「每天早上一則當日總表」的省額度模式（可做成 `reminder_preferences.digest_mode`）。這是產品決策，v1 先做逐時段推播、合併同時段藥品。

## 7. API 與頁面

### 7.1 LIFF（病人端）

頁面（Jinja + Bootstrap，`app/templates/liff/`）：

| 路徑 | 說明 |
|---|---|
| `/liff/prescriptions/<id>` | 辨識結果檢視 / 修改（每項藥名、劑量、頻率、天數），底部「確認」「刪除」「建立提醒」 |
| `/liff/prescriptions` | 我的處方列表（已驗證才顯示歷史，GUEST 只看得到 7 天內的） |
| `/liff/reminders` | 提醒總覽：今日清單、每個排程的時間 / 起訖 / 暫停；「新增提醒」手動表單；用餐時間偏好 |

API（`app/api/v1/liff.py` 拆出 `liff_medication.py`，避免單檔過長）：

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/api/v1/liff/prescriptions` | `CONSULT_MEDICATION_ONCE` | 自己的處方列表 |
| GET | `/api/v1/liff/prescriptions/{id}` | 同上（僅本人） | 處方 + items + warnings |
| PATCH | `/api/v1/liff/prescriptions/{id}` | 同上 | 修改 items（`draft` 或 `confirmed` 都可改，改了提醒不自動跟著變） |
| POST | `/api/v1/liff/prescriptions/{id}/confirm` | 同上 | `draft` → `confirmed` |
| DELETE | `/api/v1/liff/prescriptions/{id}` | 同上 | 標 `discarded`，刪照片 |
| GET | `/api/v1/liff/pharmacies?address=` 或 `?lat=&lng=` | `CONSULT_MEDICATION_ONCE` | 同鄉鎮市區的健保特約藥局（座標只用來反推鄉鎮市區，不算距離） |
| GET | `/api/v1/liff/drugs?q=` | 同上 | 藥品查詢 |
| POST | `/api/v1/liff/prescriptions/{id}/schedules/propose` | `require_verified` + `REMINDER_MANAGE_OWN` | 回預設時間點（不寫入） |
| GET / POST | `/api/v1/liff/reminders` | 同上 | 列表 / 建立（可一次多筆） |
| PATCH / DELETE | `/api/v1/liff/reminders/{id}` | 同上 | 修改時間、暫停 / 恢復、刪除 |
| GET / PUT | `/api/v1/liff/reminders/preferences` | 同上 | 用餐時間偏好 |
| GET | `/api/v1/liff/reminders/history?days=7` | 同上 | `reminder_deliveries` 歷史（服藥紀錄） |

### 7.2 院方後台

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/api/v1/admin/patients/{user_id}/prescriptions` | `PATIENT_READ` + 院所範圍 | 病人處方列表（含照片連結，讀照片寫 `audit_logs`） |
| GET | `/api/v1/admin/patients/{user_id}/reminders` | `PATIENT_READ` | 提醒排程 + 近 30 天服藥回報率 |
| GET | `/api/v1/admin/pharmacies?q=&city=` | 院方角色 | 查藥局（維護用） |

後台病人詳情頁（`app/templates/admin/patients/detail.html`）加「處方與服藥」分頁；資料匯入只提供 CLI，不做 UI。

### 7.3 CLI（`app/cli.py` 新增三個 AppGroup）

```bash
flask --app wsgi pharmacy import data/pharmacy/A21030000I-D21005-001.csv   # 健保特約藥局
flask --app wsgi pharmacy import data/pharmacy/35_2.csv                    # 衛福部藥局（補狀態）
flask --app wsgi pharmacy geocode --limit 500                              # 補經緯度
flask --app wsgi drug import data/pharmacy/A21030000I-E41001-001.csv       # 健保用藥
flask --app wsgi drug import-licenses data/pharmacy/36_2.csv --atc data/pharmacy/41_2.csv --inserts data/pharmacy/39_2.csv
flask --app wsgi reminder run-due [--dry-run]                              # cron 每 5 分鐘
flask --app wsgi media purge                                               # cron 每天
```

## 8. 與既有架構整合點

| 位置 | 改動 |
|---|---|
| `app/domain/enums.py` | `Permission.PRESCRIPTION_MANAGE_OWN`（放 `_PUBLIC`，GUEST 也可確認 / 修改自己的辨識結果）；`Intent` 不新增，用 `action=` 參數 |
| `app/domain/rbac.py` + `tests/test_rbac.py` | 對照表與集合斷言 |
| `app/line/client.py` | 新增 `get_message_content(message_id) -> bytes`（`MessagingApiBlob.get_message_content`）；`FakeLineClient` 補同名方法回固定 bytes |
| `app/line/handlers/message.py` | 依 `ImageMessageContent` / `LocationMessageContent` 分派：查 `ConversationStateStore` 決定交給哪個 intent；無狀態時用 Quick Reply 詢問（§5）；`await_drug_name` 狀態下自由文字視為藥名 |
| `app/line/intents.py` | `TEXT_KEYWORDS` 加「找藥局」→ `MEDICATION` `action=locate` |
| `app/line/handlers/intents/medication.py` | actions：`menu` / `photo`（設狀態）/ `image`（下載 → OCR → Flex）/ `confirm` / `locate` / `lookup` / `not_prescription` |
| `app/line/handlers/intents/reminder.py` | actions：`menu` / `list` / `create`（→ LIFF）/ `taken` / `snooze` / `skip` / `pause` / `resume` |
| `app/line/handlers/follow.py` | `push_enabled` 切換（關掉 TODO） |
| `app/line/messages/medication.py`、`reminder.py` | 辨識結果 Flex、藥局 Carousel、提醒 Flex；Quick Reply 用 `CameraAction` / `CameraRollAction` / `LocationAction`（SDK 都有），`MessageBuilder` 補 `camera_item()` / `location_item()` / `location_message()` |
| `app/core/container.py` | 新增 `prescription_repo`、`pharmacy_repo`、`drug_repo`、`schedule_repo`、`delivery_repo`、`conversation_state`、`vision_client`、`geocoder` 與 5 個 Service；Service 一樣用 `lambda: container.line_client` |
| `config/settings.py` / `.env.example` | `OPENAI_API_KEY`、`OCR_MODEL`、`OCR_TIMEOUT_SECONDS`、`MEDIA_ROOT` |
| `requirements.txt` | `openai`（pydantic 2 已有） |
| `app/utils/clock.py` | 加 `now_tw()` |
| migration | `pg_trgm` 擴充套件（`op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")`，SQLite 跳過）+ 8 張表 + 2 個既有表欄位 |
| `README.md` / `docs/architecture.md` / `CLAUDE.md` | 端點表、服務地圖節點對照、Roadmap 第 4、5 項改為「已實作」 |

## 9. 安全與個資

- 處方照片：落地前用 `FieldCipher` 加密（同 `PATIENT_DATA_KEY`），API 回照片走專用端點並寫 `audit_logs`；到期自動清除。
- 模型提示明確要求**不抄病人姓名 / 身分證 / 病歷號**；`ocr_raw` 存進 DB 前再用正則把身分證格式（`[A-Z][12]\d{8}`）遮罩一次當第二道保險。
- 送去 OpenAI API 的圖片依 OpenAI 資料保留政策處理，這一點要寫進 `patient_consents` 的告知事項（新增 `consent_type=ai_processing`，第一次拍處方前要同意）。
- `run-due` 推播失敗只記 log；LINE 端的 postback（taken / snooze）要驗證 `delivery` 屬於該使用者。
- 藥局 / 藥品資料是公開資料，無個資；但 `patient_profiles.address_lat/lng` 是個資，比照住址處理。
- 免責文字：所有藥物相關回覆結尾加一行「以上依處方箋內容整理，用藥疑問請洽藥師或原院所」。

## 10. 待確認問題

1. **辨識模型**：預設 `gpt-4.1`；若量大想省成本可改 `gpt-4.1-mini`，需先用真實處方照片做小規模比對。
2. **地理編碼供應商**：Google（簡單、每月 1 萬次免費，需綁信用卡）vs TGOS（政府免費、要申請帳號）。設計上抽成 `GeocodingClient` 兩者都可。
3. **推播額度**：接受升級 LINE 方案，還是要做「每日一則總表」模式？（§6.7）
4. **提醒是否要求已驗證**：目前沿用 `requires_verified=True`。若希望未填基本資料的人也能用提醒，要把 `REMINDER_MANAGE_OWN` 移到 `_PUBLIC`，但代價是未驗證帳號也會持續收到推播。
5. **「根據症狀」提醒**：v1 不做。若要做，屬於 Roadmap 第 2 項（病況諮詢）的延伸，且需醫療內容審核。
6. **同步辨識逾時**：若實測常超過 25 秒，改為「先回覆辨識中、完成後推播」（多耗 1 則推播額度）。
7. **照片保留期**：30 天是否合適？院方是否需要在後台看原始照片來核對處方？
8. **開放資料更新頻率**：藥局 / 藥品 CSV 多久重新匯入一次（建議每月 cron，`import` 已設計成可重跑 upsert）。
9. **慢箋提醒**：慢性病連續處方箋第 n/m 次，是否要在下次可領藥日前 3 天推播「該去領藥了」？資料模型已有欄位，實作成本低，可列 v1.1。

## 11. 實作順序（建議）

1. **資料層**：`pharmacies` / `drugs` / `drug_licenses` 模型 + migration + `flask pharmacy import` / `flask drug import` + `pg_trgm`。先把公開資料灌進去，可獨立驗證。
2. **地理編碼 + `PharmacyLocatorService`**：`GeocodingClient`、`flask pharmacy geocode`、`nearby()`；LINE 端「找附近藥局」（位置訊息 → Carousel）。此時 Rich Menu「去哪拿藥」已經有可用功能。
3. **`ConversationStateStore` + 圖片 / 位置訊息分派**：`message.py` 改寫、`LineMessagingClient.get_message_content`、`patient_media`。
4. **處方辨識**：`LlmVisionClient`、`PrescriptionService`、`prescriptions` 模型、辨識結果 Flex、LIFF `/liff/prescriptions/<id>` 確認頁、`DrugCatalogService.match()`。
5. **服藥提醒**：`medication_schedules` / `reminder_deliveries` / `reminder_preferences`、`MedicationScheduleService`、LIFF `/liff/reminders`、`ReminderHandler` 全部 actions、`follow.py` 的 `push_enabled`。
6. **推播排程**：`ReminderDispatchService.run_due()`、`flask reminder run-due`、合併推播、postback 回報；`flask media purge`。
7. **後台與文件**：admin API + 病人詳情分頁；README / architecture / CLAUDE.md 更新。

每一步都附 `tests/test_pharmacy.py`、`tests/test_prescription.py`、`tests/test_reminder.py`，用 `FakeVisionClient` / `FakeGeocoder` / `FakeLineClient`，全程 SQLite。
