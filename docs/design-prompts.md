# MediAgent+ 視覺設計 Prompt（後台網頁 + Rich Menu）

以下內容從 `svg/` 裡 8 個 logo 檔整理而來，可直接複製給 AI 使用。
共三段：**A. 共用品牌規範**（每次都貼）、**B. 後台網頁 prompt**（給寫程式的 AI）、**C. Rich Menu prompt**（C1 給寫程式的 AI 產 SVG→PNG，推薦；C2 給生圖 AI）。

---

## A. 共用品牌規範（貼在每個 prompt 最前面）

```
【MediAgent+ 品牌規範】
品牌是醫療照護 LINE 機器人「MediAgent+」。Logo 由三個元素組成：
1. 心電圖折線（ECG / heartbeat line）：一條水平線 → 小上升 → 深 V 下探 → 回升 → 回到水平，
   線寬粗、端點與轉角皆為圓角（stroke-linecap/linejoin: round），不填色。
   參考座標（viewBox 0 0 200 200）：polyline 30,104 62,104 78,72 98,138 116,88 126,104 138,104，stroke-width 10。
2. 紅色十字「+」：由兩個圓角矩形交叉組成（圓角約為短邊的 30%），接在心電圖線右側，
   同時代表醫療十字與品牌名的「+」。
3. 字標「mediagent」：全小寫、幾何無襯線、字重 Semi-bold，後面緊接紅色「+」。

容器形狀：圓形（icon 版）或大圓角矩形（橫式版，圓角約為高度的 20%）。
整體風格：扁平（flat）、無漸層、無陰影、無擬物、大量留白、線條粗而圓潤、幾何感、冷靜專業。

【色票】（Coolors 經典「Space Cadet / Cool Gray / Platinum / Red」組合）
- Space Cadet 深靛藍  #2B2D42 —— 主色：深色背景、主要文字、線條
- Cool Gray  灰藍紫   #8D99AE —— 輔助色：次要文字、邊框、深底上的心電圖線、停用狀態
- Platinum  白金灰    #EDF2F4 —— 淺色背景、卡片底、深底上的文字
- White              #FFFFFF —— 卡片、輸入框
- Red Pantone 亮紅    #EF233C —— 強調色（用在深色 / 白金底上）
- Fire Engine Red 深紅 #D90429 —— 強調色（用在白色 / 灰藍底上，對比較足）

【配色規則】
- 紅色只當「重點」：+ 號、主要 CTA、緊急 / 危險狀態，面積控制在畫面 5% 以內，不可大面積鋪紅。
- 四種 logo 變體的配法（背景 / 線條 / 十字）：
  indigo   #2B2D42 / #EDF2F4 / #EF233C
  lavender #8D99AE / #2B2D42 / #D90429
  platinum #EDF2F4 / #2B2D42 / #EF233C
  white    #FFFFFF(+#EDF2F4 細框) / #2B2D42 / #D90429
- 文字對比需符合 WCAG AA；#8D99AE 在白底上只能用於 ≥18px 的輔助文字或圖示，不可用於內文。
```

---

## B. 後台網頁 Prompt（給 Claude Code / Cursor 等寫程式 AI）

```
（先貼上 A. 共用品牌規範）

請依上述品牌規範重新設計 MediAgent+ 院方後台的視覺，只改外觀、不改功能與資料流程。

【技術限制（務必遵守）】
- 專案是 Flask + Jinja2，後台模板在 app/templates/admin/（base.html、login.html、dashboard.html、
  patients/list.html、patients/detail.html、handoffs.html、staff.html、settings.html、audit.html、permissions.html）。
- 繼續使用 Bootstrap 5.3（CDN），用 CSS 變數覆寫主題，不要引入 Tailwind、React 或建置工具。
- 不要改動 <script> 裡的 JS 邏輯、API 呼叫、data-perm 屬性、元素 id（gate、shell、me-name、toast-area 等）。
- 把目前寫死的綠色系（#1f3d36、#2f6f62、#245648）全部換成品牌色，集中定義在 base.html 的
  :root CSS 變數（例如 --ma-ink、--ma-gray、--ma-platinum、--ma-red、--ma-red-strong），
  並覆寫 Bootstrap 的 --bs-primary、--bs-link-color、--bs-danger 等。
- Logo 用內嵌 SVG（心電圖線 + 紅十字），不要引用外部圖片。

【版面與元件】
- 側邊欄：背景 #2B2D42；頂端放橫式 logo（白金字 #EDF2F4、心電圖線 #8D99AE、+ 號 #EF233C）；
  選單文字 #EDF2F4 透明度 75%，hover/active 用 rgba(237,242,244,.10) 底 + 左側 3px 紅色 #EF233C 指示條；
  底部使用者資訊區用 #8D99AE。
- 主內容區：背景 #EDF2F4；卡片白底、圓角 16px、1px #DDE3E8 邊框、不加陰影或只加極淡陰影。
- 標題文字 #2B2D42、Semi-bold；次要文字 #6B7489（由 #8D99AE 加深以符合對比）。
- 按鈕：
  主要（.btn-brand）= #2B2D42 底 + 白字，hover 稍微變亮；
  強調 / 危險（鎖定帳號、駁回）= #D90429；
  次要 = 白底 + #2B2D42 1.5px 框。圓角 10px。
- 表格：表頭 #EDF2F4 底、#2B2D42 字；列 hover 用 #F6F8FA；分隔線 #E3E8EC。
- 狀態徽章（binding_status）：
  UNBOUND 灰藍 #8D99AE 底白字；PENDING 白底 + #2B2D42 框（待核對）；
  VERIFIED #2B2D42 底白字 + 小勾勾；LOCKED #D90429 底白字。
  不要引入綠色 / 黃色新色，靠明度與圖示區分。
- 儀表板統計卡：大數字 #2B2D42，標籤 #6B7489，卡片左上角可放小的心電圖線裝飾（#8D99AE，低透明度）。
- 登入頁（login.html）：全頁 #2B2D42 底，中央白色卡片放圓形 logo（white 變體）與 LINE 登入按鈕
  （LINE 按鈕維持官方綠 #06C755，這是唯一允許的非品牌色）。
- Loading（#gate）：用 logo 的心電圖線做描線動畫（stroke-dasharray），取代 Bootstrap spinner。
- 焦點框：2px #EF233C outline，offset 2px。
- 字型：繼續用系統字 "PingFang TC", "Microsoft JhengHei"；英數可加 "Inter"（Google Fonts）。

【交付】
逐檔修改模板，最後列出改了哪些檔、新增了哪些 CSS 變數，並確認 pytest 仍全數通過。
```

---

## C1. Rich Menu Prompt（給寫程式 AI 產出 SVG → PNG，推薦）

> 生圖 AI 很難把中文字寫對、也很難精準對齊 6 格點擊區，所以建議用程式畫 SVG 再轉 PNG。

```
（先貼上 A. 共用品牌規範）

請產出 MediAgent+ 的 LINE Rich Menu 圖檔：先寫一個 SVG，再轉成 PNG（可用 rsvg-convert 或 cairosvg）。

【尺寸與格線（必須完全符合，否則點擊區會錯位）】
- 畫布 2500 × 1686 px，PNG，檔案 < 1MB。
- 3 欄 × 2 列，每格約 833 × 843（寬 2500/3 取整、高 1686/2），順序由左到右、由上到下：
  第 1 列：我不舒服 ｜ 找醫院 ｜ 我剛出院
  第 2 列：去哪拿藥 ｜ 服藥提醒 ｜ 找真人

【視覺】
- 整體底色 #EDF2F4（platinum），每格是一張白色 #FFFFFF 圓角卡片（圓角 48px，四周留 24px 間距），
  讓 6 格看起來像 6 顆大按鈕。
- 每格中上方放一個線條圖示（stroke 16–20px、#2B2D42、圓角端點，與 logo 心電圖線同一筆觸語言），
  圖示下方放中文標籤：#2B2D42、字重 Bold、約 96px、置中。
- 每格右上角或圖示旁放一個小的紅色圓角「+」作為品牌點綴（#D90429），不要每格都一樣位置太死板，
  但大小一致、面積要小。
- 圖示建議：
  我不舒服 → 心電圖折線（直接沿用 logo 的 ECG 線）
  找醫院   → 建築物 + 十字 或 定位針 + 十字
  我剛出院 → 門 + 向外箭頭 或 病床 + 箭頭
  去哪拿藥 → 藥袋 / 藥瓶
  服藥提醒 → 時鐘 + 膠囊
  找真人   → 人像 + 對話框
- 「我不舒服」格可用深色反轉（#2B2D42 卡片底、#EDF2F4 圖示與文字、#EF233C 十字），
  作為視覺重心引導症狀諮詢；其餘 5 格維持白卡。
- 扁平、無漸層、無照片、無陰影（或極淡），字要在手機縮小後仍清楚。
- 中文字型用 Noto Sans TC Bold（先確認系統有安裝，沒有就下載），文字轉成 path 或確保渲染正確。

【交付】
輸出 richmenu.svg 與 richmenu.png，並用以下指令設定：
flask --app wsgi richmenu setup --image richmenu.png
```

---

## C2. Rich Menu Prompt（給生圖 AI：Midjourney / DALL·E / Gemini 等）

> 生圖 AI 常把中文字畫錯，建議讓它**只產圖示與版面，不含文字**，文字之後再用 Figma / Canva 疊上。

```
A LINE chatbot rich menu for a healthcare brand, 2500x1686 pixels, landscape,
a 3-column by 2-row grid of six equal rounded-square buttons on a very light cool gray background (#EDF2F4).
Each button is a white card with large rounded corners and generous padding.
Inside each card, one simple bold line icon centered in the upper half, leaving empty space below for a text label.
Icons, left to right, top to bottom: a heartbeat ECG line, a hospital building with a medical cross,
a door with an outward arrow, a medicine bottle, a clock with a capsule, a person with a speech bubble.
The first card is inverted: dark indigo (#2B2D42) card with light icon.
Icon style: thick uniform strokes with round caps and joins, dark indigo (#2B2D42), flat, geometric, minimal.
Small accent: a tiny rounded red medical plus sign (#D90429) in each card.
Flat vector design, no gradients, no shadows, no photos, no text, no letters, clean medical UI, calm and trustworthy.
--ar 3:2
```

（`--ar 3:2` 為 Midjourney 參數；產出後放大 / 裁切到 2500×1686。）
