# 本機操作說明

本文件說明如何在自己的電腦上安裝、設定、啟動、測試與除錯 MediAgent+ 後端。
指令以 macOS（zsh）為主，Linux 差異處另行標註。

---

## 1. 環境需求

| 項目 | 版本 | 用途 |
|---|---|---|
| Python | 3.11.x | 執行後端 |
| PostgreSQL | 16（14 以上皆可） | 資料庫 |
| pgAdmin 4 | 最新版 | 圖形介面管理資料庫 |
| Git | 任意 | 版本控制 |
| ngrok（選用） | 任意 | 讓 LINE 平台能連到本機 webhook |

### 安裝

```bash
# macOS
brew install python@3.11 postgresql@16
brew install --cask pgadmin4
brew services start postgresql@16
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# Ubuntu / Debian
sudo apt update
sudo apt install python3.11 python3.11-venv postgresql
sudo systemctl enable --now postgresql
# pgAdmin 4 依官方說明安裝：https://www.pgadmin.org/download/pgadmin-4-apt/
```

也可以直接到 https://www.pgadmin.org/download/ 下載安裝檔。

確認版本：

```bash
python3.11 --version    # Python 3.11.x
psql --version          # psql (PostgreSQL) 16.x
```

---

## 2. 取得程式碼與建立虛擬環境

```bash
git clone git@github.com:konentung/MediAgentPlus.git
cd MediAgentPlus
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
```

之後每次開新終端機都要先 `source .venv/bin/activate`。

---

## 3. 建立資料庫（pgAdmin）

### 3.1 連線到本機 PostgreSQL

1. 開啟 pgAdmin 4，第一次啟動會要求設定 master password（保護 pgAdmin 內儲存的連線密碼）。
2. 左側 Object Explorer 對 **Servers** 按右鍵 → **Register → Server…**。
3. **General** 分頁：Name 填 `Local`。
4. **Connection** 分頁：

   | 欄位 | 值 |
   |---|---|
   | Host name/address | `localhost` |
   | Port | `5432` |
   | Maintenance database | `postgres` |
   | Username | macOS Homebrew 為你的系統帳號（如 `konen`）；Linux 為 `postgres` |
   | Password | Homebrew 預設沒有密碼可留空；Linux 需先為 `postgres` 設密碼（見下方） |

5. 勾 Save password → **Save**。左側出現 `Local` 表示連線成功。

Linux 若 `postgres` 尚未設密碼，先在終端機執行一次：

```bash
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
```

### 3.2 建立登入角色

1. 展開 `Local` → 對 **Login/Group Roles** 按右鍵 → **Create → Login/Group Role…**。
2. **General**：Name 填 `mediagent`。
3. **Definition**：Password 填 `mediagent`（正式環境請換強密碼）。
4. **Privileges**：開啟 **Can login?** 與 **Create databases?**。
5. **Save**。

### 3.3 建立資料庫

1. 對 **Databases** 按右鍵 → **Create → Database…**。
2. **General**：Database 填 `mediagent`，Owner 選 `mediagent`。
3. **Definition**：Encoding 保持 `UTF8`。
4. **Save**。

### 3.4 驗證

對剛建立的 `mediagent` 資料庫按右鍵 → **Query Tool**，執行：

```sql
SELECT current_user, current_database(), now();
```

看到結果即代表可用。對應的連線字串為：

```
postgresql+psycopg://mediagent:mediagent@localhost:5432/mediagent
```

之後資料表建立後，都可以在 `mediagent → Schemas → public → Tables` 底下瀏覽、用 **View/Edit Data** 直接看資料。

### 3.5 命令列替代方案（選用）

不想開 pgAdmin 時，也可以用腳本一次建立同樣的角色與資料庫：

```bash
./scripts/setup_db.sh                    # 預設 mediagent / mediagent / mediagent
./scripts/setup_db.sh my_db my_user pw   # 自訂
```

---

## 4. 設定環境變數

```bash
cp .env.example .env
```

打開 `.env` 填入以下值。所有金鑰只放在 `.env`，不要寫進程式碼或提交到 git。

### 4.1 LINE Messaging API（官方帳號 / Bot）

1. 進入 [LINE Developers Console](https://developers.line.biz/console/)。
2. 建立 Provider → 建立 **Messaging API** channel。
3. 在 Basic settings 複製 **Channel secret** → `LINE_CHANNEL_SECRET`。
4. 在 Messaging API 分頁點 Issue 取得 **Channel access token (long-lived)** → `LINE_CHANNEL_ACCESS_TOKEN`。
5. 同分頁關閉「Auto-reply messages」與「Greeting messages」，避免與程式回覆重複。

### 4.2 LINE Login（OAuth）與 LIFF

1. 同一個 Provider 下建立 **LINE Login** channel。
2. Basic settings 的 **Channel ID** → `LINE_LOGIN_CHANNEL_ID`，**Channel secret** → `LINE_LOGIN_CHANNEL_SECRET`。
3. LINE Login 分頁的 Callback URL 填入 `LINE_LOGIN_REDIRECT_URI`（本機為 `http://localhost:5000/api/v1/auth/line/callback`）。
4. LIFF 分頁點 Add，Endpoint URL 填 `https://<你的網域>/liff`（本機用 ngrok 網址），Scope 勾 `profile`、`openid`。這樣 `https://liff.line.me/<LIFF_ID>/intake?hospital=H1` 會開到 `/liff/intake`。
5. 取得 **LIFF ID** → `LIFF_ID`。`LIFF_CHANNEL_ID` 留空即沿用 `LINE_LOGIN_CHANNEL_ID`。
6. 在 LINE Login channel 的 Basic settings 底下「Linked OA」連結到上面的 Messaging API 官方帳號，登入時才會提示加好友。

### 4.3 JWT 與其他

```bash
# 產生足夠長度的隨機金鑰
python3.11 -c "import secrets; print(secrets.token_urlsafe(48))"
```

分別產生兩組填入 `SECRET_KEY` 與 `JWT_SECRET_KEY`。

### 4.4 Redis（正式環境）

多 worker（gunicorn `-w 2` 以上）或多台機器部署時，OAuth state 與 rate limit 必須共用儲存，
請設定 `REDIS_URL=redis://host:6379/0`；本機單一程序可留空。

### 4.5 建立第一家院所與管理員

```bash
flask --app wsgi hospital add H1 "第一醫院" --region 台北市
flask --app wsgi user set-role <你的 user_id> super_admin      # user_id 可在 /api/v1/auth/me 或 pgAdmin 的 users 表查到
```

之後到後台 `/admin/settings` 就能新增或用 CSV 匯入其他院所（支援健保署「特約醫事機構」開放資料格式），`/admin/staff` 指派人員到院所。批次匯入也可用 CLI：

```bash
flask --app wsgi hospital import data/pharmacy/A21030000I-D21005-001.csv --only-hospitals
```

病人身分證字號以 Fernet 加密儲存，金鑰放 `PATIENT_DATA_KEY`：

```bash
python3.11 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

本機留空會由 `SECRET_KEY` 衍生（重啟不影響解密）；正式環境必填，**金鑰一旦更換既有資料就無法解密**，請妥善備份。

### 4.6 藥局 / 藥品開放資料與處方箋辨識

「去哪拿藥」與「服藥小幫手」需要藥局與健保用藥資料，匯入一次即可（可重跑）。系統管理員登入後台
`/admin/settings`，在「參考資料（藥局 / 藥品）」直接上傳 CSV；或用 CLI 從 `data/pharmacy/` 匯入：

```bash
flask --app wsgi pharmacy import data/pharmacy/A21030000I-D21005-001.csv   # 健保署特約藥局（約 1 萬筆）
flask --app wsgi pharmacy import data/pharmacy/35_2.csv                    # 衛福部藥局，補非特約（約 1.5 千筆）
flask --app wsgi drug import data/pharmacy/A21030000I-E41001-001.csv       # 健保用藥（約 4.5 萬筆）
flask --app wsgi pharmacy stats
flask --app wsgi pharmacy find "台北市松山區"                               # 測試找藥局
```

處方箋照片辨識與 AI 對話都用 OpenAI API：在 `.env` 填 `OPENAI_API_KEY` 即可，模型留空時辨識用 `gpt-4.1`
（一張約 5–15 秒）、對話用 `gpt-4.1-mini`。辨識期間 LINE 端顯示「處理中」動畫，不另發訊息。
照片以 `PATIENT_DATA_KEY` 加密後完整保留在 `MEDIA_ROOT`（預設 `var/media/`，已在 .gitignore）。

這個專案沒有獨立前端，LINE Login callback（`/api/v1/auth/line/callback`）直接用 Jinja render
`app/templates/auth/callback.html` 顯示登入成功頁，並把 token 交給頁面上的 script 存進
`sessionStorage`；若 `/api/v1/auth/line/login?redirect_to=...` 有帶 `redirect_to`，該頁會在存好
token 後自動導向該路徑。

---

## 5. 啟動服務

### 開發模式（自動重載）

```bash
./scripts/run_dev.sh
# 或
flask --app wsgi run --debug
```

開啟 http://localhost:5000/api/v1/health 應看到：

```json
{"success": true, "data": {"status": "ok"}}
```

### 正式模式（gunicorn）

```bash
FLASK_ENV=production ./scripts/run_prod.sh
```

正式模式啟動時會檢查必要環境變數，缺少會直接報錯並列出缺哪些。
可用 `PORT`、`WORKERS`、`THREADS`、`TIMEOUT`（預設 120 秒，處方辨識需要）環境變數調整。

### 服藥提醒排程（cron）

提醒推播不是常駐程序，由外部 cron 每 5 分鐘呼叫一次 CLI（可重複執行、不會重複推播）：

```cron
*/5 * * * * cd /path/to/MediAgentPlus && FLASK_ENV=production flask --app wsgi reminder run-due >> var/reminder.log 2>&1
```

本機測試可以指定時間：`flask --app wsgi reminder run-due --now 2026-09-28T08:31:00+08:00`。
注意 LINE 官方帳號免費方案每月只有 200 則推播，一位病人一天三次一個月就 90 則，正式上線前請升級方案。

### AI 對話助理（OpenAI）

自由文字由 AI 助理判斷事件（`docs/ai-assistant-design.md`）。在 `.env` 填 `OPENAI_API_KEY` 即可（對話模型留空為 `gpt-4.1-mini`）。初始化：

```bash
flask --app wsgi knowledge import data/knowledge      # 匯入通用知識種子
flask --app wsgi knowledge import-health-education "data/衛教內文資料/health_education_content.jsonl"  # 全台醫院衛教（約 20 秒）
flask --app wsgi knowledge embed                      # 算向量（41,737 片段約 7 分鐘、US$0.25；可中斷重跑）
flask --app wsgi knowledge stats
flask --app wsgi knowledge search "糖尿病腳有傷口怎麼照顧"   # 確認檢索（關鍵字 + 語意）
flask --app wsgi assistant ask "我頭痛兩天了"          # 實際呼叫模型，看脈絡與決策 JSON
```

OpenAI 一次約 2–6 秒；`AI_TIMEOUT_SECONDS` 預設 60，gunicorn `TIMEOUT` 預設 120。
`AI_ENABLED=false` 時文字退回關鍵字比對。院所自己的衛教 / FAQ 在後台「AI 知識庫」上傳 Markdown 即可，不需改程式。
語意檢索需要 PostgreSQL 的 pgvector 擴充套件（Homebrew：`brew install pgvector`；migration 會自動 `CREATE EXTENSION vector`）。
沒有 pgvector 或 `EMBEDDING_MODEL` 留空時只用關鍵字檢索，功能仍可用。

### 急診動態分流排程（cron）

健保署急診即時訊息約每 15 分鐘更新一次，同樣用 cron 呼叫 CLI（同一輪回報不會重複寫入、不會重複通知）：

```cron
*/15 * * * * cd /path/to/MediAgentPlus && FLASK_ENV=production flask --app wsgi triage poll >> var/triage.log 2>&1
```

沒有 cron 的環境（例如 Docker 單一容器）可以用常駐模式：`flask --app wsgi triage poll --loop --interval 900`。
初始化時先匯入 59 家重度級急救責任醫院的座標種子檔：`flask --app wsgi hospital import data/er_hospitals.csv`，
「找醫院」才能依使用者位置算距離、推薦最近未壅塞的醫院（沒匯入會退回縣市看板）。
本機檢查：`flask --app wsgi triage board --area 台北市`、`flask --app wsgi triage advisories --all`。
接收者（119 勤務指揮中心 / 診所）在後台「急診動態分流」建立：LINE 推播需要對方先加官方帳號；Webhook 與 API 拉取會產生一次性顯示的 secret。

---

## 6. 讓 LINE 連到本機（ngrok）

LINE 平台只能呼叫 HTTPS 公開網址，本機開發需要 ngrok 之類的通道工具。

```bash
brew install ngrok          # 或到 https://ngrok.com 下載
ngrok http 5000
```

複製顯示的 `https://xxxx.ngrok-free.app`，到 LINE Developers Console：

- Messaging API 分頁 → Webhook URL 填 `https://xxxx.ngrok-free.app/line/webhook` → 點 Verify → 開啟 Use webhook。
- LINE Login 分頁 → Callback URL 加上 `https://xxxx.ngrok-free.app/api/v1/auth/line/callback`，並同步改 `.env` 的 `LINE_LOGIN_REDIRECT_URI`。

ngrok 免費版每次重啟網址會變，記得同步更新。

---

## 7. 設定 Rich Menu（主選單六格）

準備一張 2500×1686 的 PNG 或 JPG，六格排列為 3 欄 × 2 列，順序為：

| 我不舒服 | 找醫院 | 我剛出院 |
|---|---|---|
| 去哪拿藥 | 服藥提醒 | 找真人 |

```bash
flask --app wsgi richmenu setup --image ./assets/richmenu.png
```

預設會先刪掉既有的 Rich Menu 再建立新的；加 `--keep-existing` 可保留。
用手機打開官方帳號聊天室，底部應出現主選單。

---

## 8. 常用指令

| 指令 | 說明 |
|---|---|
| `pytest` | 執行全部測試（不需要 DB） |
| `pytest tests/test_webhook.py -v` | 只跑 webhook 測試 |
| `ruff check . && ruff format .` | Lint 與格式化 |
| `flask --app wsgi routes` | 列出所有 API 路由 |
| `flask --app wsgi rbac show` | 輸出角色權限矩陣 |
| `flask --app wsgi user set-role <user_id> super_admin` | 以系統身分指派角色 |
| `flask --app wsgi pharmacy import <csv>` / `drug import <csv>` | 匯入藥局 / 健保用藥開放資料 |
| `flask --app wsgi pharmacy find "台北市松山區"` | 測試依地址找藥局 |
| `flask --app wsgi reminder run-due [--now <iso>]` | 推播到期的服藥提醒（cron 用） |
| `flask --app wsgi triage poll [--loop --interval 900]` | 抓健保署急診即時資料、評估壅塞、通知接收者（cron 用） |
| `flask --app wsgi triage board [--area 台北市]` / `triage advisories [--all]` | 印出急診看板 / 分流建議 |
| `flask --app wsgi knowledge import <dir 或 .md>` / `knowledge search "<q>"` / `knowledge list` | AI 知識庫匯入 / 試搜 / 列表 |
| `flask --app wsgi knowledge import-health-education <jsonl>` / `knowledge embed` / `knowledge stats` | 衛教內文匯入 / 算向量 / 統計 |
| `flask --app wsgi assistant ask "<text>" [--user-id]` | 本機直接問 AI 助理 |
| `flask --app wsgi shell` | 進入有 app context 的 Python shell |

---

## 9. 手動測試 API

### LINE Login 流程

1. 瀏覽器開首頁 http://localhost:5000/ 按「使用 LINE 登入」（或直接開 /api/v1/auth/line/login），會導向 LINE 授權頁。
2. 授權後回到 callback，畫面顯示「登入成功」，`access_token`/`refresh_token` 由頁面上的 script
   存進瀏覽器的 `sessionStorage`，接著自動導回首頁並顯示登入者名稱與角色（手動測試可以打開
   DevTools Console 執行 `sessionStorage.getItem('access_token')` 取值）。
3. 帶 token 查詢身分：

```bash
curl -H "Authorization: Bearer <access_token>" http://localhost:5000/api/v1/auth/me
```

### LIFF 登入

前端用 `liff.getIDToken()` 取得 id_token 後：

```bash
curl -X POST http://localhost:5000/api/v1/liff/auth \
  -H "Content-Type: application/json" \
  -d '{"id_token": "<id_token>"}'
```

---

## 10. 常見問題

**`ModuleNotFoundError: No module named 'app'`**
沒有在專案根目錄執行，或沒有啟用虛擬環境。`cd` 到專案根目錄並 `source .venv/bin/activate`。

**`Invalid LINE signature`（HTTP 400）**
`.env` 的 `LINE_CHANNEL_SECRET` 填錯，或修改 `.env` 後沒重啟伺服器。

**LINE Developers Console 的 Verify 失敗**
ngrok 沒開、網址填錯，或後端沒在跑。先用 curl 打 `/api/v1/health` 確認後端存活。

**`state 無效或已過期`（HTTP 401）**
OAuth state 存在記憶體，伺服器重啟後就失效；重新從 `/auth/line/login` 開始即可。正式環境請改用 Redis 實作 `OAuthStateStore`。

**`Missing required environment variables`**
正式模式缺少必要變數，依錯誤訊息補齊 `.env`。

**`psql: connection refused`**
PostgreSQL 沒啟動。macOS 執行 `brew services start postgresql@16`，Linux 執行 `sudo systemctl start postgresql`。

**pgAdmin 連線顯示 `password authentication failed`**
Homebrew 安裝的 PostgreSQL 預設以系統帳號免密碼登入，Username 要填你的 macOS 帳號而不是 `postgres`。
若要用 `postgres` 帳號，先在終端機執行 `createuser -s postgres` 建立。

**pgAdmin 連線顯示 `connection refused`**
PostgreSQL 沒啟動，見上一則 `psql: connection refused`。

**修改 `.env` 沒有生效**
設定在啟動時載入一次，改完要重啟伺服器。
