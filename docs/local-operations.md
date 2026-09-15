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
4. LIFF 分頁點 Add，Endpoint URL 先填前端網址（可先填 `https://localhost`），Scope 勾 `profile`、`openid`。
5. 取得 **LIFF ID** → `LIFF_ID`。`LIFF_CHANNEL_ID` 留空即沿用 `LINE_LOGIN_CHANNEL_ID`。
6. 在 LINE Login channel 的 Basic settings 底下「Linked OA」連結到上面的 Messaging API 官方帳號，登入時才會提示加好友。

### 4.3 JWT 與其他

```bash
# 產生足夠長度的隨機金鑰
python3.11 -c "import secrets; print(secrets.token_urlsafe(48))"
```

分別產生兩組填入 `SECRET_KEY` 與 `JWT_SECRET_KEY`。

`FRONTEND_URL` 有填時，LINE Login callback 會 302 導向前端並把 token 放在 URL fragment；留空則直接回傳 JSON，方便用瀏覽器測試。

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
可用 `PORT`、`WORKERS`、`THREADS` 環境變數調整。

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
| `pytest tests/test_binding.py -v` | 只跑綁定流程測試 |
| `ruff check . && ruff format .` | Lint 與格式化 |
| `flask --app wsgi routes` | 列出所有 API 路由 |
| `flask --app wsgi rbac show` | 輸出角色權限矩陣 |
| `flask --app wsgi user set-role <user_id> super_admin` | 以系統身分指派角色 |
| `flask --app wsgi shell` | 進入有 app context 的 Python shell |

---

## 9. 手動測試 API

### LINE Login 流程

1. 瀏覽器開 http://localhost:5000/api/v1/auth/line/login，會導向 LINE 授權頁。
2. 授權後回到 callback，畫面顯示 `access_token` 與 `refresh_token`。
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

### 綁定流程（B1 到 B9）

以下用 `TOKEN` 代表上一步拿到的 access_token。

```bash
BASE=http://localhost:5000/api/v1/liff/binding
AUTH="Authorization: Bearer $TOKEN"

curl -X POST $BASE/start -H "$AUTH"
curl -X POST $BASE/identity -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"identity_type": "self"}'
curl -X POST $BASE/profile -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name": "王小明", "birth_date": "1990-01-01", "phone_last4": "5678", "hospital_id": "H1", "department": "心臟內科"}'
curl -X POST $BASE/verification -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"method": "sms_otp"}'
curl -X POST $BASE/consent -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"accepted": true}'
curl -X POST $BASE/verify -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"code": "<OTP>"}'
```

開發模式的 OTP 不會真的發簡訊，會印在伺服器 log：

```
INFO [app.services.verification] [DEV] SMS OTP for ***5678: 123456
```

要讓比對成功，需先由院所管理員把該病患加進識別名單：

```bash
curl -X POST http://localhost:5000/api/v1/admin/roster \
  -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" \
  -d '{"id": "r1", "hospital_id": "H1", "name": "王小明", "birth_date": "1990-01-01", "phone_last4": "5678"}'
```

目前 Repository 是 in-memory 實作，伺服器重啟後資料會清空。接上資料庫模型後即可持久化。

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
