# 台灣醫院衛教內文資料

抓取日期：2026-09-25　來源：全台 449 家健保特約醫院官網的衛教專區（實際找到並抓到內文的有 133 家、224 個站台）

從衛教索引的 44,083 個網址逐筆抓回內文：HTML 抽正文、PDF 與 DOCX 抽文字，
再以「同站台重複出現的行＝版面而非內容」把選單與頁尾剔掉。

## 檔案

```
衛教內文資料/
  health_education_content.jsonl   # ★ 一行一篇的扁平檔，35,784 篇有內文的衛教（96MB）
  content/<host>.json              # 完整資料，每個站台一檔，共 224 檔（含抓失敗的紀錄，119MB）
  content_index.json               # 總覽：統計 + 每個網址一列的精簡欄位（不含正文，26MB）
```

**要用內容就讀 `health_education_content.jsonl`**，一行一個 JSON 物件，已濾掉抓不到內文的項目。
要追溯抓取細節（狀態、HTTP 回應、附件、PDF 頁數）再去 `content/<host>.json`；
`content_index.json` 適合先篩再取正文，每筆的 `file` 欄位就是它所在的檔名。

## health_education_content.jsonl

35,784 篇、3,673 萬字、129 家醫院，平均 1,026 字/篇。每行的欄位：

```jsonc
{
  "id": "f75f09c5488f18d8",       // 網址雜湊，重跑不會變
  "title": "了解肝癌",
  "text": "正文純文字，段落以換行分隔",
  "chars": 834,
  "hospitalName": "中國醫藥大學附設醫院",
  "hospitalCode": "0401200011",
  "level": "醫學中心",             // 醫學中心 / 區域醫院 / 地區醫院
  "topics": ["癌症/腫瘤", "腸胃/肝膽"],   // 21 類疾病／科別，可多類，也可能是空的
  "forms": ["疾病介紹"],           // 12 類內容型態，可多類，也可能是空的
  "category": "衛教單張",          // 來源站台自己的分類名稱
  "format": "html",               // html / pdf / docx / doc
  "url": "https://...",           // 原始出處
  "indexTitle": "…"               // 列表頁上的連結文字（與 title 不同時才有）
}
```

讀取：

```python
import json

docs = [json.loads(line) for line in open("health_education_content.jsonl", encoding="utf-8")]
print(len(docs))  # 35784

kidney = [d for d in docs if "腎臟/泌尿" in d["topics"]]  # 依主題
ntuh = [d for d in docs if d["hospitalCode"] == "0401180014"]  # 依醫院
long_ = [d for d in docs if d["chars"] >= 500]  # 依長度
```

重新匯出（可調條件）：

```bash
cd MediAgentPlus/data/hospital-health-education
python scripts/09_export_jsonl.py --out out.jsonl --min-chars 200 --classified-only
```

## 內容概況

內文合計 **3,684 萬字**。

| 狀態 | 筆數 | 佔比 | 說明 |
|------|-----:|-----:|------|
| `ok` | **35,784** | 81.2% | 抽到實質內文（≥60 字），可直接用 |
| `thin` | 5,482 | 12.4% | 抓到頁面但沒有實質內文（正文是 iframe／圖片，或整頁只有選單連結） |
| `no-text` | 1,665 | 3.8% | PDF／DOCX 抽不到文字（掃描檔，需要 OCR） |
| `skipped-video` | 562 | 1.3% | 影音連結，沒有文字可抽 |
| `http-404` / `403` / `400` | 204 | 0.5% | 來源已下架或擋爬蟲 |
| `host-unreachable` / `timeout` / `conn-error` | 232 | 0.5% | 來源站台連不上 |
| `robots-disallow` | 62 | 0.1% | robots.txt 不允許，未抓取 |
| 其他 | 91 | 0.2% | 舊版 `.doc`、非文字格式、檔案過大（>50MB）等 |

依格式：html 33,854　pdf 8,980　docx 118　doc 46
依醫院層級：區域醫院 1,819 萬字　醫學中心 1,296 萬字　地區醫院 570 萬字

## 欄位（`content/<host>.json` → `contents[]`）

```jsonc
{
  "url": "https://www.puzih.mohw.gov.tw/public/ufile/....pdf",
  "hospitalCode": "0140010028",       // 健保醫事機構代碼
  "hospitalName": "衛生福利部朴子醫院",
  "level": "地區醫院",                 // 醫學中心 / 區域醫院 / 地區醫院
  "indexTitle": "認識A型肝炎",         // 列表頁上的連結文字
  "category": "衛教單張",              // 來源站台的分類名稱
  "kind": "file",                     // article 文章頁 / file 檔案 / page 一般頁
  "indexType": "pdf",                 // 索引階段依副檔名推測的型態
  "listedOn": "https://...?aid=5",    // 從哪個列表頁連出來的
  "alsoFor": ["其他共用同一連結的院名"],
  "fetchedAt": "2026-09-25T20:11:11",
  "httpStatus": 200,
  "contentType": "application/pdf",
  "bytes": 51695,
  "format": "pdf",                    // 實際型態（依位元組嗅探，會修正 indexType 的誤判）
  "pdfPages": 1,
  "title": "…",                       // 頁面自己的標題（HTML 取 <title>／<h1>）
  "headings": ["…"],                  // HTML 的 h1–h4
  "attachments": [{"title": "…", "url": "…"}],   // 頁面內的 PDF／Word 附件連結
  "text": "正文純文字，段落以換行分隔",
  "chars": 1621,
  "removedLines": 12,                 // 從這筆剔掉幾行版面文字
  "truncated": true,                  // 正文超過 8 萬字被截斷
  "schemeUpgraded": true,             // 原網址是 http，該站只開 443 而改走 https
  "status": "ok"
}
```

站台檔的最外層另有 `host`、`hospitals`（這個站台涵蓋哪幾家醫院）、`items`、`ok`、`chars`、`statusCounts`。

`content_index.json` 的 `items[]` 是同一批資料去掉 `text`／`headings`／`attachments` 的版本，
另有 `totals`（全域統計）與 `hosts[]`（每站的檔名、涵蓋醫院、成功筆數、字數）。

## 怎麼用

```python
import json, glob

# 一次讀出所有可用的衛教內文
docs = []
for path in glob.glob("content/*.json"):
    with open(path, encoding="utf-8") as f:
        for r in json.load(f)["contents"]:
            if r.get("status") == "ok":
                docs.append(r)
print(len(docs), "篇")  # 35784

# 只要某個主題（索引階段就標好的分類，一筆可屬多類）
kidney = [d for d in docs if "腎臟/泌尿" in (d.get("topics") or [])]

# 只要某家醫院
ntuh = [d for d in docs if d["hospitalCode"] == "0401180014"]
```

分類用的欄位：`topics`（21 類疾病／科別，例如「心血管」「癌症/腫瘤」「糖尿病/代謝/內分泌」）、
`forms`（12 類內容型態，例如「疾病介紹」「護理指導/照護須知」「檢查/手術說明」「用藥」）。
這兩個欄位是從標題做關鍵字歸類的，目的是方便瀏覽與抽樣，不保證精準。

## 已知限制

- **JS 渲染的頁面拿不到內容**：正文由 JavaScript 或 iframe 載入的站台（問卷、影音頁）只會得到 `thin`。爬蟲不跑瀏覽器。
- **掃描版 PDF 需要 OCR**：1,665 筆 `no-text` 是圖片式 PDF。
- **少數頁面抓到的是站台首頁**：部分 CMS 對不存在的中文網址回首頁而非 404，這類記錄的 `title` 會與 `indexTitle` 明顯不符，可用這點過濾。
- **版面文字可能有殘留**：剔除規則是「同站台 25% 以上頁面都出現的行」，只出現在少數頁面的版面文字（例如即時瀏覽人數）會留下。
- **夾雜外語版內容**：約 1,550 篇是英／越／印尼等外語版衛教（其中 464 篇網址帶 `locale=en`、`lang=en` 這類參數），
  只要中文的話可依此過濾。
- **醫療正確性未經審核**：內容原樣取自各院官網，沒有做任何醫療審閱或更新日期檢查。
- **著作權屬各醫院**：這份資料是為 MediAgent+ 的衛教檢索而抓取，對外使用前需確認各院授權。

## 重跑

爬蟲管線在專案裡：`MediAgentPlus/data/hospital-health-education/scripts/`
（`06_crawl_content.py` 抓內文 → `07_build_content_index.py` 建索引 → `08_clean_content.py` 清版面文字
→ `09_export_jsonl.py` 匯出 JSONL）。
