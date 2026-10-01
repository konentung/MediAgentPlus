# 急診分流（找醫院）設計

> 2026-09-28 重新實作：**只做「依使用者位置推薦適合去的急診醫院」**。原本的分流建議、
> 119 / 診所接收者（LINE / Webhook / API 訂閱）、院方後台看板全部移除，資料表只剩 `er_snapshots`。

## 1. 使用者看到什麼

1. Rich Menu「找醫院」、AI 判斷 `hospital` 事件、或紅旗症狀的「最近急救責任醫院」按鈕 → 機器人請使用者**分享位置**
   （Quick Reply 只有「📍 分享位置」；紅旗時多「🚨 撥打 119」）。不再有「選縣市」「用基本資料住址」。
2. 使用者傳 LINE 位置訊息 → 回一張 Flex：離使用者最近的 8 家重度級急救責任醫院，每家標距離、燈號、
   等看診 / 等住院 / 等 ICU 人數、119 滿床通報；**推薦最近且未壅塞的一家**，並附上該醫院的 `LocationMessage`（可直接導航）。
   全部壅塞時提示「附近都壅塞，有生命危險請撥 119」。AI 症狀諮詢判斷出科別時（`slots.department`），
   「找醫院」按鈕也帶 `department`，`board_for_location(department=)` 每家標 `department_matched`
   （`hospitals.departments` 對 `department_terms()`），推薦優先給「最近、未壅塞、有該科」的一家，
   都沒有該科再退回最近的並說明（2026-10-01）。
3. 「非急症：找附近診所」按鈕 → 同鄉鎮市區（沒有退到同縣市）的診所 / 地區 / 區域醫院，診所優先、有座標的再依距離，
   並補上 10 km 內有座標的院所；AI 症狀諮詢建議的科別（`department`）會帶進按鈕，依 `hospitals.departments` 篩選
   （同義詞對應在 `triage_rules.department_terms()`），沒有該科再放寬。按鈕沒帶位置時先請分享位置。
4. LIFF `/liff/er`（不需登入）：「用我的位置」用瀏覽器定位打同一支 API；另保留縣市切換看全國燈號。

## 2. 資料流

```
APScheduler 每 15 分鐘：TriageService.poll()（app/scheduler.py）
  → NhiErClient.fetch()（健保署急診即時訊息，公開、免金鑰）
  → TriageService.ingest()：寫 er_snapshots（UNIQUE(醫院, 回報時間)，重跑不重複）
  → purge 超過 TRIAGE_RETENTION_DAYS 的舊快照

查詢（LINE 位置訊息 / GET /api/v1/triage/board?lat=&lng=）
  → TriageService.board()：最新一輪快照 + 燈號（快照超過 TRIAGE_STALE_MINUTES 會即時補抓一次，失敗用舊的）
  → board_for_location(lat, lng)：只取 hospitals 主檔有座標的醫院，Haversine 算距離，取最近 8 家，
    recommended = 最近且 level != critical 的一家
```

- 院所座標：`hospitals.lat / lng`，種子 `data/er_hospitals.csv`（59 家重度級醫院），後台「院所設定」CSV 匯入。
  主檔完全沒座標時 LINE 回「院所座標尚未建置」並給 119，不退回縣市看板。
- 距離用 `app/utils/geo.py` 的 Haversine，不用地圖服務。

## 3. 燈號規則（`app/domain/triage_rules.py`）

- 依醫院層級（醫學中心 / 區域 / 地區）的等住院門檻 + 等 ICU / 等看診門檻 → `normal` / `busy` / `critical`。
- 119 滿床通報一律 `critical`。**推床數 `wait_bed` 不列入判斷**（資料不可靠）。
- `worsening`：等住院人數高於過去 24 小時平均，看板上標示「↑ 上升中」。
- 醫院停止回報超過 2 × `TRIAGE_STALE_MINUTES` 的舊快照不當現況。
- `public_rules()` 提供 LIFF 顯示門檻與地區代碼。

## 4. 資料模型

只有 `er_snapshots`（見 `docs/database-models.md`）。`triage_advisories`、`triage_subscriptions` 已移除。

## 5. 設定

| 變數 | 預設 | 說明 |
|---|---|---|
| `TRIAGE_STALE_MINUTES` | 30 | 快照超過幾分鐘視為過舊，查詢時即時補抓 |
| `TRIAGE_RETENTION_DAYS` | 30 | 快照保留天數 |
| `NHI_ER_TIMEOUT_SECONDS` | 10 | 健保署 API 逾時 |
