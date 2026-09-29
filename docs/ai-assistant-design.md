# AI 對話助理設計（RAG + 事件判斷 → 套用 LINE 模組）

> 狀態：**已實作**（2026-09-28 加入全台醫院衛教內文與混合檢索）。AI 只用 OpenAI API。程式位置見 CLAUDE.md「AI 對話助理」。

使用者需求：用 RAG 處理使用者提出的問題，不寫規則式的意圖邏輯；由 AI 針對事件判斷後，套用 LINE 機器人既有的模組回應；
回覆內容必須是依知識庫生成的、有出處的、相對準確的答案。

## 1. 決策

| 議題 | 決策 | 落地 |
|---|---|---|
| 模型 | OpenAI API（`OPENAI_API_KEY`），對話 `gpt-4.1-mini`、處方辨識 `gpt-4.1`、向量 `text-embedding-3-small`；`AI_MODEL` / `OCR_MODEL` / `EMBEDDING_MODEL` 可覆寫 | `app/integrations/llm.py`：`OpenAiClient.complete_structured()`、`embed()` |
| 知識來源 | 全台 129 家醫院官網衛教內文，依科別分類成 57 個 txt（35,784 篇）經篩選匯入約 25,600 篇並記科別；加上通用種子 Markdown 與院所自行上傳 | `KnowledgeService.import_department_files()`，後台「系統資料匯入」 |
| 檢索 | **混合檢索**：關鍵字（jieba 斷詞 + 字元 bigram → PostgreSQL tsvector 全文檢索）+ 語意（OpenAI embedding + pgvector），各取 20 個候選，RRF 合併，同一份文件最多 2 個片段 | `app/repositories/knowledge_repository.py`、`app/domain/retrieval.py` |
| 回答生成 | 模型只能依檢索片段寫答案：先結論、再 2–4 個重點、必要時提醒就醫；一定標 citations；片段不足要明說「知識庫沒有資料」並建議詢問原院所 | `ConversationService.SYSTEM_PROMPT` |
| LINE 呈現 | 有引用時用 Flex：回答正文 + 免責 + 最多 3 個「參考來源」按鈕（醫院《標題》，開原文網址）；下一步 Quick Reply 照舊 | `app/line/messages/assistant.py` |
| 涵蓋範圍 | 所有文字先過 AI 再派給模組；Rich Menu / 按鈕 postback、圖片、位置訊息直接走模組 | `MessageHandler._text_request()` → `Intent.ASSISTANT` |
| 安全例外 | 紅旗關鍵字直接進緊急畫面、不等模型（10 分鐘冷卻：送過就不再短路、AI 判 emergency 只回一句話 + 按鈕，避免反覆重傳）；「等藥名」狀態的文字當藥名查詢 | `EMERGENCY_KEYWORDS`、`app/line/emergency.py`、`AWAIT_DRUG_NAME` |
| AI 失敗 | 退回關鍵字比對，再不行回失敗訊息 + 主選單；`AI_ENABLED=false` 也走這條；查詢向量失敗只退成關鍵字檢索 | `AssistantHandler._fallback()`、`KnowledgeService._semantic_candidates()` |

## 2. 流程

```
使用者文字
  │ 紅旗關鍵字？──是──▶ 緊急畫面
  │ 等藥名狀態？──是──▶ 藥名查詢
  ▼
AssistantHandler（Intent.ASSISTANT）
  1. loading animation（最長 60 秒）
  2. ConversationService.decide(principal, text)
       ├ build_context
       │    ├ KnowledgeService.retrieve()：關鍵字候選 20 + 語意候選 20 → RRF → top_k（預設 4）
       │    ├ 使用者狀態、最近對話（TTL store）
       │    └ 即時資料（提到縣市 → 急診看板摘要；像藥名的詞 → 健保用藥主檔）
       ├ build_prompt：【使用者狀態】【最近對話】【知識庫片段 K1..（醫院《標題》：內容）】【即時資料】【使用者這則訊息】
       ├ OpenAiClient.complete_structured(prompt, AssistantDecision, system=SYSTEM_PROMPT)
       └ decision.attach_sources(hits)：citations K1、K3 → 對回片段（出處、網址）
  3. 事件 → 模組（回覆用 ctx.capture() 收集，與 answer 合併成一次 reply，最多 5 則）
       emergency → 緊急畫面；hospital → 找醫院；medication → 藥名查詢 / 選單；pharmacy → 找藥局；
       reminder / discharge / human / intake → 對應模組；
       symptom / faq / smalltalk / unknown → answer（有來源 → Flex + 原文按鈕）+ actions 變 Quick Reply
  4. reply；token 過期改 push
```

## 3. 衛教內文資料（`data/衛教內文資料/`）

| 檔案 | 用途 |
|---|---|
| `health_education_content.jsonl` | 一行一篇（35,784 篇、3,673 萬字、129 家醫院），原始檔；`分類腳本.py` 依標題 / 來源分類 / 內文關鍵字歸到單一科別 |
| `衛教內文_依科別分類/NN_科別.txt` | 每科一檔（57 檔、91MB），篇與篇用 60 個 `=` 分隔、四行檔頭（標題 / 醫院（層級）/ 來源分類 / 網址），**匯入用這個** |
| `content/<host>.json` | 每個站台的完整抓取紀錄（含失敗），不匯入 |
| `content_index.json` | 統計與索引，不匯入 |

匯入規則（`import_department_files`）：

- 資料是「依科別分類」的 txt（`01_心臟內科.txt` … `57_未分類.txt`，每科一檔、`====` 分隔、四行檔頭），
  檔名決定 `department`；「非衛教」整檔略過、「未分類」照收但科別留空。
- 內文 ≥ 200 字、中文比例 ≥ 0.4（濾掉英文頁、公告）；內文 SHA-1 去重（各院互相轉載的衛教單只留第一份）；
  `source` = 原始網址，**同網址更新**（補科別、依新規則重切），所以可重跑。
- 兩遍掃描：第一遍 `collect_boilerplate()` 統計全語料的網站導覽行，第二遍清理、切段、寫入（批次 500 篇一次 commit）。
- 一律存成通用文件（`hospital_id` 空），出處放 `meta`：`hospital_name`、`level`、`department`、`source_category`、
  `imported_from`；`tags` = [科別]。
- 結果回報片段長度分佈（數量 / 中位數 / 過短 / 過長），實測 25,642 篇 → 112,590 個片段、中位數 331 字。

```bash
後台 /admin/knowledge →「系統資料匯入」一次選多個科別 txt 上傳 →「計算向量」（背景工作，可中斷重跑）
切段規則改了 →「重建片段」（不必重新上傳；向量會清掉）→ 再「計算向量」
後台「試搜」確認檢索結果（關鍵字 + 語意，顯示科別與小節）
```

## 4. 知識庫（`knowledge_documents` + `knowledge_chunks`）

- 文件：`title`、`category`（service / faq / symptom / medication / education / hospital）、`content`、`tags`、
  `source`（檔名或網址，去重）、`content_hash`、`meta`（出處）、`hospital_id`（空 = 通用）、`active`。
- 片段：`chunk_document()` 結構感知 recursive chunking（見下節）；`heading`（標題路徑）、`kind`（text / list / table / qa）、
  `tokens`（斷詞，含科別與標題）、`search_vector`（tsvector，GIN 索引）、`embedding`（`vector(1536)`，HNSW 索引，
  輸入 = 科別 › 標題 + 片段）。文件新增 / 修改就重切，向量由後台「計算向量」補。
- 科別加權：`retrieve(department=)` 多兩個「只在該科別內」的關鍵字 / 語意排序清單一起 RRF，同科別片段分數變高但其他科別
  仍在候選內（分類正確率約 9 成，硬過濾會漏）。科別來源是已驗證病人最近就診的 `department`。

### 切段規則（`app/domain/retrieval.py`，2026-09-29）

資料特性（35,785 篇實測）：95% 有空行分段；標題是「一、」（28k 行）「（一）」（34k）「1.」（117k）或沒有句尾標點的短行，
Markdown `#` 只有 68 行；36% 的文章有連續 8 行以上短行（表格被抽成一格一段、或網站選單）；「衛教資訊」「Qrcode」
「(已掛號 人)」等頁面殼各出現上千次。舊規則（只認空行、800 字硬切、無 overlap）11% 片段撞硬切、4.7% 不到 100 字。

| 步驟 | 規則 |
|---|---|
| 清行 `clean_lines()` | 去 Qrcode / 圖片說明 / 麵包屑（`>`、`首頁…`）/ 日期 / 純數字行 / 下載提示；開頭 5 行內與標題相同的行；連續 5 行以頁碼結尾的目錄；語料級樣板行 |
| 樣板行 `collect_boilerplate()` | 每篇頭尾 15 行內、≤ 12 字、在一串短行中出現 ≥ 30 篇且占該行總出現 ≥ 0.8 → 導覽列（「注意事項」後面接段落、表格表頭在文中，都不會被誤刪） |
| 分類 `_classify()` | 標題：`#` / 壹 / 一 / （一） / 1. / ≤ 20 字無句尾標點且後面接較長段落；編號行後面接**同家族**編號行 → 條列不是標題。條列：連續同家族 + 以「：」結尾的引言。表格：連續 6 個 ≤ 25 字短 block，多數以句號結尾則視為一句一行的條列。問答：Q 行 + 下一段 |
| 大小 | 目標 400、硬上限 600（× 1.5）、下限 80（× 0.2）。超過硬上限依 換行 → 句號 → 逗號 → 字數 遞迴下探；同一小節貪婪合併到目標；相鄰片段重疊最後一句（≤ 120 字）；不到下限的尾巴併回前一段 |
| 前綴 | 「章 › 節：內文」，`heading` 另存；文件第一個標題視為標題本身不進路徑 |

實測：112,590 個片段、中位數 331 字、< 80 字 0.1%、> 600 字 0.03%、33% 有標題路徑；kind = text 77% / list 17% / table 5.5% / qa 0.6%。
- 檢索：PostgreSQL 上關鍵字走 `to_tsquery('simple', ...)` + `ts_rank_cd`，語意走 `<=>` 餘弦距離；
  SQLite（測試）用記憶體 BM25 與 Python 餘弦，行為一致。
- 範圍：通用文件所有人檢索得到；院所文件只有 `preferred_hospital_id` 或所屬院所是它的使用者檢索得到。
- 權限：讀取 = 所有院方角色；新增 / 修改 / 刪除 / Markdown 匯入 = `EDUCATION_CONTENT_MANAGE`（院所綁自己）；
  通用文件與衛教 txt 匯入 / 重建片段只有 SUPER_ADMIN。
- 後台 `/admin/knowledge`：列表、編輯、停用、多檔 Markdown 匯入、試搜。

## 5. 決策格式（`app/domain/assistant_schema.py`）

| 欄位 | 說明 |
|---|---|
| `event` | emergency / symptom / hospital / medication / pharmacy / reminder / discharge / intake / human / faq / smalltalk / unknown |
| `risk` | none / watch / urgent / emergency（emergency 一律走緊急畫面） |
| `answer` | 給使用者的話，400 字內、不用 Markdown；功能型事件可留空讓模組接手 |
| `slots` | city / district / drug_name / symptom / department / hospital_name |
| `actions` | 下一步按鈕（最多 4）：call_119 / find_hospital / share_location / find_pharmacy / drug_lookup / take_prescription_photo / reminder_menu / discharge_menu / intake_form / human |
| `citations` | 用到的知識片段編號（K1…）；服務端對回 `decision.sources`（不在模型輸出 schema 內） |
| `needs_clarification` | 太模糊再問一句 |

## 6. Prompt 規則（`ConversationService.SYSTEM_PROMPT`）

只能依片段與即時資料寫答案、逐條對照，片段之間有出入採保守說法；有依據必標 citations，沒有依據要明說並建議詢問原院所；
先一句結論、再 2–4 個重點（每點一行、「・」開頭）、視情況提醒何時就醫；400 字內、口語、無 Markdown；
不診斷不開藥；紅旗 → emergency；功能型事件可留空；actions 最多 4；提到縣市 / 藥名填 slots；超出範圍或想找人 → human。

兩個「諮詢」功能（Roadmap 第 2、3 項）也只靠 prompt 規則 + 脈絡，沒有另外的服務：

- **病況諮詢（event=symptom）**：依片段判斷「可能需要掛哪一科」填 `slots.department`，`risk` 分級
  none（自我照護）/ watch（就近診所）/ urgent（急診）。`AssistantHandler` 在 watch 時補 `find_clinic`
  （→ `FindHospitalHandler action=clinics`，依住址 / 位置找診所、地區醫院），urgent 時補 119 + 找醫院。
- **衛教諮詢（event=discharge）**：已驗證病人的脈絡多一段【病人資料】（`PatientRecordRepository.latest_encounter()`：
  最近就診科別 / 診斷 / 出院代碼 / 院方說明 + 自填慢性病）；問題像在問照護（出院 / 衛教 / 回家 / 傷口 …）時
  檢索查詢補上診斷名稱，才找得到對應衛教。模型要寫「現在該做的具體動作」（今日照護、何時回診、何時 119）；
  有 answer 就直接回（含來源），沒有【病人資料】則 answer 留空 → `DischargeHandler` 通用版。
  Rich Menu「我剛出院」對已驗證病人也用固定問題走同一條路（`DischargeHandler.PERSONAL_QUESTION`），AI 失敗退回通用版。

## 7. 費用與延遲

- 每則對話：1 次 embedding（查詢向量，毫秒級）+ 1 次 gpt-4.1-mini（約 2–6 秒）；約 4–6k input tokens。
- 建庫一次性：41,737 個片段的向量約 US$0.25；重跑匯入只補新文章，不重算舊向量。
- 沒有向量（`EMBEDDING_MODEL` 留空或尚未 `knowledge embed`）時自動只用關鍵字檢索。

## 8. 可延伸

- 對話紀錄目前只存 TTL store（30 分鐘）給模型當上下文；客服接手要看脈絡再落 DB。
- 院所自己的衛教 / FAQ 在後台上傳 Markdown 即可；衛教 txt 有新版時系統管理員在後台 `/admin/knowledge`「系統資料匯入」重新上傳（同網址更新）並按「計算向量」；切段規則改了按「重建片段」再「計算向量」（都是背景工作、頁面輪詢進度）。
- 回答品質直接在 LINE 上抽樣檢查；要更準可調 `KNOWLEDGE_TOP_K`（預設 4）或把 `AI_MODEL` 換成較大的模型。
