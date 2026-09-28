# 急診動態分流設計（Roadmap 第 1 項）

> 狀態：**已實作（2026-09-26）**。程式位置見 CLAUDE.md「急診動態分流」。

使用者需求（原文）：

> 當它發現某區的一家醫院陷入壅塞狀態時，它會自動生成一份分流建議報告，透過 API（或 LINE）發送給該區的 119 勤務指揮中心或基層診所醫師，建議他們暫時將病患轉往哪些仍有餘裕的中大型醫院。
> 要設計成可以定時抓取健保署急診即時 API 更新的系統，並且要可以讓使用者根據現在的狀況去決定要去哪家醫院或是診所。

## 0. 實作決策

| 議題 | 決策 | 落地方式 |
|---|---|---|
| 壅塞資料來源 | **健保署急診即時訊息 API**（公開、免金鑰、每 15 分鐘更新、全國 59 家重度級急救責任醫院），不做人工回報 | `app/integrations/nhi_er.py`（`NhiErClient`），參考 [KJT125/Nhi-er-open-data](https://github.com/KJT125/Nhi-er-open-data) 整理的欄位說明 |
| 定時抓取 | 沿用專案慣例：**Flask CLI + cron**，不引入 Celery / APScheduler | `flask triage poll` 每 15 分鐘；沒有 cron 的環境可用 `--loop --interval 900` 常駐 |
| 哪些欄位可信 | 等看診 / 等住院 / 等 ICU / 119 滿床通報；**推床數不列入判斷**（各院填報標準不一，原作者向 EOC 執行長確認過） | `app/domain/triage_rules.py` 只用前四個欄位算燈號，`wait_bed` 照存但不使用 |
| 壅塞門檻 | 依醫院層級不同（醫學中心 / 區域 / 地區），119 滿床通報一律壅塞 | `WAIT_ADMIT_THRESHOLDS`（偏忙 / 壅塞）：醫中 35/70、區域 10/20、地區 5/10；等 ICU 3/5；等看診 20/40（對照原專案觀察：大型醫中 ≥70–100 才算爆、區域 ≥20、ICU ≥5 警報） |
| 分流建議粒度 | **以縣市為單位**：該縣市任一家醫院達「壅塞」就開立一份建議，列出壅塞醫院與有餘裕的替代醫院（同縣市 → 鄰近縣市） | `TriageService.evaluate()`，鄰近縣市對照 `ADJACENT_AREAS` |
| 接收者是誰、如何驗證身份 | 不新增角色。院所管理員 / 系統管理員在後台建立「接收者（訂閱）」，**身份線下核對**；三種管道：LINE 推播、Webhook 推送（HMAC 簽章）、API key 主動拉取 | `triage_subscriptions`，權限 `TRIAGE_SUBSCRIPTION_MANAGE`；HOSPITAL_ADMIN 只看得到自己院所建立的 |
| 通知頻率 | 開立時通知；持續壅塞時只有「新增壅塞醫院」或超過 `TRIAGE_RENOTIFY_MINUTES`（預設 120 分）才再通知；解除時通知 | 避免每 15 分鐘轟炸 119 |
| 一般使用者（病人）怎麼用 | Rich Menu「找醫院」→ **分享位置（LINE `LocationAction`，自帶經緯度）** → 以使用者位置為準，列出最近 8 家重度級醫院（距離 + 燈號）、**推薦最近且未壅塞的一家**並附上位置訊息可直接導航 →「非急症：找附近診所」依距離找 10 km 內診所；選縣市 / 用住址（無座標）時退回縣市看板；紅旗命中時同一流程加撥打 119 | `FindHospitalHandler`、`TriageService.board_for_location()`、LIFF `/liff/er`（「用我的位置」用瀏覽器定位） |
| 院所座標來源 | 不用付費地圖服務。59 家重度級醫院的座標做成種子檔 `data/er_hospitals.csv`（OpenStreetMap Nominatim 一次性查詢 + 人工校對，`coord_source` 欄位註明），`flask hospital import data/er_hospitals.csv` 匯入 `hospitals.lat / lng`；其他院所可在匯入 CSV 加 `lat,lng` 欄或後台 PATCH | `HospitalService.IMPORT_COLUMN_ALIASES`、`app/utils/geo.py`（Haversine） |
| 分流建議的替代醫院 | 壅塞醫院有座標時，依「離壅塞醫院最近」找 50 km 內未壅塞的醫院（報告標示距離）；沒座標退回縣市 / 鄰近縣市規則 | `TriageService._alternatives()` |
| 快照過舊 | 查詢時最新快照超過 `TRIAGE_STALE_MINUTES`（30 分）就即時抓一次，但**不**在請求中做建議評估與通知 | `TriageService.board(refresh_if_stale=True)` |
| 保留期限 | 快照保留 `TRIAGE_RETENTION_DAYS`（30 天），poll 時自動清除；建議（advisories）不清除，作為歷史 | 24 小時平均 / 壅塞倍數用得到時序資料 |
| 與院所主檔的關係 | API 的 `hosP_ID` 就是健保「醫事機構代碼」；若 `hospitals.id` 用同一代碼匯入，看板會帶出地址 / 電話 | `HospitalRepository.get_many()`；沒有對應也能運作 |

## 1. 資料流

```
cron 每 15 分鐘 ──▶ flask triage poll
                      │ 1. NhiErClient.fetch()   健保署 API → ErFeed（pydantic）
                      │ 2. ingest()              er_snapshots（UNIQUE(醫院, 回報時間)，重跑不重複）
                      │ 3. purge_before()        清掉超過保留天數的快照
                      │ 4. evaluate()            逐縣市：壅塞醫院？→ 開立 / 更新 / 解除 triage_advisories
                      └ 5. _notify()             LINE push（訂閱者）、Webhook POST（HMAC）；api 類型不推送

LINE「找醫院」 ──▶ FindHospitalHandler ──▶ TriageService.board(area) ──▶ Flex 看板
                                        └▶ nearby_clinics(city, district) ──▶ hospitals 主檔（診所 / 地區 / 區域）
LIFF /liff/er ──▶ GET /api/v1/triage/board（公開、rate limit）
119 系統      ──▶ GET /api/v1/triage/advisories + X-Api-Key（拉取）或接收 Webhook（推送）
院方後台      ──▶ /admin/triage：看板、建議、接收者管理、立即抓取
```

## 2. 資料模型（`app/models/triage.py`）

- `er_snapshots`：`nhi_hospital_id`、`hospital_name`、`area_code`、`hosp_type`、`full_119`、`wait_see`、`wait_bed`、`wait_admit`、`wait_icu`、`reported_at`（API 的 txT_DATE，台灣時間轉 UTC）、`fetched_at`。UNIQUE(`nhi_hospital_id`, `reported_at`)。
- `triage_advisories`：`area_code`、`status`（open / resolved）、`congested` JSON、`alternatives` JSON、`report`（純文字報告）、`snapshot_reported_at`、`opened_at`、`resolved_at`、`last_notified_at`、`notify_count`。同一縣市同時只有一筆 open。
- `triage_subscriptions`：`kind`（line / webhook / api）、`label`（接收單位）、`area_codes` JSON（空 = 全國）、`user_id`（line）、`webhook_url`、`secret_encrypted`（Fernet，webhook 簽章 / API key）、`secret_fingerprint`（HMAC，供 API key 查找）、`hospital_id`（建立者院所，決定可見範圍）、`active`、`last_notified_at`、`last_error`、`notify_count`。

## 3. 燈號與建議規則（`app/domain/triage_rules.py`）

- `congestion_level()`：119 滿床 → 壅塞；否則依層級比對等住院門檻，等 ICU / 等看診任一達門檻也升級。
- `congestion_score()` = 等住院 + 3 × 等 ICU + 0.5 × 等看診，同燈號內越低越前面。
- `ratio_24h` = 目前等住院 ÷ 24 小時平均（平均 ≥ 5 才算），≥ 1.5 標示「較 24h 平均惡化」，只做提示不影響燈號。
- 替代醫院：壅塞醫院有座標 → 全國未壅塞且有座標的醫院依「離壅塞醫院最近」排序（50 km 內、多家壅塞時取最近距離）；否則同縣市「尚有餘裕」→ 同縣市「偏忙」→ 鄰近縣市「尚有餘裕」。最多 5 家。
- 使用者位置：`board_for_location(lat, lng)` 純依距離列最近 8 家（含壅塞者，讓使用者看到等候人數），`recommended` = 最近且燈號非「壅塞」的一家；全部壅塞則提示撥 119。
- 找診所：有使用者座標 → 10 km 內的診所 / 地區 / 區域醫院依距離；沒有（或範圍內無座標院所）→ 鄉鎮市區 → 縣市比對。
- 某醫院從 API 消失（停止回報）超過 2 × `TRIAGE_STALE_MINUTES`，其舊快照不再當成現況。

## 4. Webhook 與 API 拉取格式

Webhook `POST <webhook_url>`，header `X-MediAgent-Event: opened|updated|resolved|test`、
`X-MediAgent-Signature: sha256=<HMAC-SHA256(secret, body)>`：

```json
{
  "event": "opened",
  "generated_at": "2026-09-26T06:45:00+00:00",
  "advisory": {
    "id": "…", "area_code": "01", "area_name": "台北市", "status": "open",
    "congested": [{"nhi_hospital_id": "0401180014", "hospital_name": "臺大", "tier_label": "醫學中心",
                   "level": "critical", "full_119": true, "wait_see": 5, "wait_admit": 80, "wait_icu": 6}],
    "alternatives": [{"hospital_name": "聯醫仁愛", "area_name": "台北市", "level": "normal", "wait_admit": 1,
                      "distance_km": 2.7, "distance_from": "臺大", "lat": 25.0372, "lng": 121.5452, "...": "..."}],
    "report": "【急診分流建議】台北市　09/26 14:45\n壅塞醫院：\n・臺大（醫學中心）等住院 80、等 ICU 6、等看診 5，119 滿床通報\n建議轉往（依餘裕排序）：\n・聯醫仁愛（區域醫院）…",
    "opened_at": "…", "resolved_at": null, "notify_count": 1
  }
}
```

拉取：`GET /api/v1/triage/advisories`，header `X-Api-Key: <secret>`，回傳該訂閱縣市範圍內 `status=open` 的建議陣列。

## 5. 尚未做 / 可延伸

- 人工回報壅塞（非重度級醫院、診所）：原設計的 `POST /admin/hospitals/{id}/congestion`，目前資料源已足夠先不做。
- 院所座標目前只有 59 家重度級醫院有種子資料；診所要依距離推薦需在匯入 CSV 補 `lat,lng`（健保開放資料沒有座標）。
- 趨勢圖：後台目前以文字列出 24 小時歷史；要圖表可從 `GET /admin/triage/hospitals/<id>/history` 取資料。
- LINE 推播額度：官方帳號免費方案每月 200 則，訂閱者數量 × 通知次數要留意。
