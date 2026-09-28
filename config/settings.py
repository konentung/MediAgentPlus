"""應用程式設定。

所有敏感資訊一律由環境變數（.env）注入，程式碼內不得硬編碼任何金鑰。
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

# override=True：以 .env 檔為準，避免 IDE（例如 VS Code 的 envFile 快取）注入過期的環境變數。
# 正式環境通常沒有 .env 檔，由部署平台注入環境變數，不受影響。
load_dotenv(override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class BaseConfig:
    """所有環境共用的預設值。"""

    ENV = "base"
    DEBUG = False
    TESTING = False
    SECRET_KEY = _env("SECRET_KEY", "dev-secret-key")
    # 信任的 reverse proxy 層數（ngrok / nginx），讓 request.remote_addr 拿到真實 IP；0 = 不信任
    TRUSTED_PROXY_COUNT = int(_env("TRUSTED_PROXY_COUNT", "0"))
    # 128MB：後台上傳健保用藥開放資料 CSV（約 96MB）
    MAX_CONTENT_LENGTH = 128 * 1024 * 1024
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")

    # ---- PostgreSQL ----
    SQLALCHEMY_DATABASE_URI = _env(
        "DATABASE_URL", "postgresql+psycopg://mediagent:mediagent@localhost:5432/mediagent"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # ---- Redis（選用）：設定後 OAuth state 與 rate limit 改用 Redis，多 worker 共用 ----
    REDIS_URL = _env("REDIS_URL")

    # ---- LINE Messaging API ----
    LINE_CHANNEL_SECRET = _env("LINE_CHANNEL_SECRET")
    LINE_CHANNEL_ACCESS_TOKEN = _env("LINE_CHANNEL_ACCESS_TOKEN")

    # ---- LINE Login (OAuth 2.0 / OIDC) ----
    LINE_LOGIN_CHANNEL_ID = _env("LINE_LOGIN_CHANNEL_ID")
    LINE_LOGIN_CHANNEL_SECRET = _env("LINE_LOGIN_CHANNEL_SECRET")
    LINE_LOGIN_REDIRECT_URI = _env(
        "LINE_LOGIN_REDIRECT_URI", "http://localhost:5000/api/v1/auth/line/callback"
    )
    LINE_LOGIN_SCOPE = _env("LINE_LOGIN_SCOPE", "profile openid")
    OAUTH_STATE_TTL_SECONDS = int(_env("OAUTH_STATE_TTL_SECONDS", "600"))

    # ---- LIFF ----
    LIFF_ID = _env("LIFF_ID")
    LIFF_CHANNEL_ID = _env("LIFF_CHANNEL_ID") or _env("LINE_LOGIN_CHANNEL_ID")

    # ---- JWT ----
    JWT_SECRET_KEY = _env("JWT_SECRET_KEY") or SECRET_KEY
    JWT_ALGORITHM = "HS256"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(seconds=int(_env("JWT_ACCESS_EXPIRES_SECONDS", "3600")))
    # 沒有 refresh token：到期就重新登入。院方後台角色給較長效期（預設 8 小時）
    JWT_STAFF_ACCESS_TOKEN_EXPIRES = timedelta(
        seconds=int(_env("JWT_STAFF_ACCESS_EXPIRES_SECONDS", str(8 * 3600)))
    )

    # ---- 病人資料欄位加密（身分證字號）----
    # Fernet 金鑰，產生方式見 .env.example。
    # 未設定時由 SECRET_KEY 衍生，僅供開發；正式環境必填（見 ProductionConfig.REQUIRED_KEYS）。
    PATIENT_DATA_KEY = _env("PATIENT_DATA_KEY")

    # ---- OpenAI（對話助理與處方辨識）----
    OPENAI_API_KEY = _env("OPENAI_API_KEY")
    AI_MODEL = _env("AI_MODEL") or None  # 對話，預設 gpt-4.1-mini
    OCR_MODEL = _env("OCR_MODEL") or None  # 處方辨識（看圖），預設 gpt-4.1
    # 知識庫語意檢索用的向量模型；留空只用關鍵字檢索
    EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "text-embedding-3-small")
    AI_TIMEOUT_SECONDS = int(_env("AI_TIMEOUT_SECONDS", "60"))
    OCR_TIMEOUT_SECONDS = int(_env("OCR_TIMEOUT_SECONDS", "90"))
    MEDIA_ROOT = _env("MEDIA_ROOT", "var/media")  # 處方箋照片（加密後）存放目錄

    # ---- AI 對話助理 ----
    AI_ENABLED = _env("AI_ENABLED", "true").lower() in ("1", "true", "yes")  # 關閉時退回關鍵字比對
    AI_HISTORY_TURNS = int(_env("AI_HISTORY_TURNS", "6"))  # 帶進 prompt 的最近對話輪數
    AI_HISTORY_TTL_SECONDS = int(_env("AI_HISTORY_TTL_SECONDS", "1800"))
    KNOWLEDGE_TOP_K = int(_env("KNOWLEDGE_TOP_K", "4"))  # 每次檢索帶幾個知識片段

    # ---- 服藥提醒 ----
    REMINDER_WINDOW_MINUTES = int(_env("REMINDER_WINDOW_MINUTES", "15"))  # cron 允許的延遲
    REFILL_REMINDER_DAYS_BEFORE = int(_env("REFILL_REMINDER_DAYS_BEFORE", "3"))  # 慢箋提前幾天提醒

    # ---- 急診動態分流（健保署急診即時訊息，cron 每 15 分鐘 `flask triage poll`）----
    NHI_ER_API_URL = _env(
        "NHI_ER_API_URL", "https://info.nhi.gov.tw/api/inae4000/inae4001s01/SQL0002"
    )
    NHI_ER_TIMEOUT_SECONDS = int(_env("NHI_ER_TIMEOUT_SECONDS", "20"))
    TRIAGE_STALE_MINUTES = int(_env("TRIAGE_STALE_MINUTES", "30"))  # 超過就視為過舊、查詢時即時補抓
    TRIAGE_RETENTION_DAYS = int(_env("TRIAGE_RETENTION_DAYS", "30"))  # 快照保留天數

    # ---- 程序內排程（服藥提醒推播、急診資料抓取），取代外部 cron ----
    SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes"}
    # 系統管理員的 LINE userId（逗號分隔）：這些人登入 / 加好友時自動成為 SUPER_ADMIN，
    # 其他角色都在後台「人員與角色」指派
    SUPER_ADMIN_LINE_USER_IDS = _env("SUPER_ADMIN_LINE_USER_IDS", "")


class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True


class TestingConfig(BaseConfig):
    ENV = "testing"
    TESTING = True
    # 測試不依賴 PostgreSQL，改用 in-memory SQLite；StaticPool 讓整個 engine
    # 共用同一條連線，否則 in-memory SQLite 每條連線各自獨立，跨連線就看不到已建立的表。
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    LINE_CHANNEL_SECRET = "test-channel-secret"
    LINE_CHANNEL_ACCESS_TOKEN = "test-access-token"
    LINE_LOGIN_CHANNEL_ID = "1234567890"
    LINE_LOGIN_CHANNEL_SECRET = "test-login-secret"
    LIFF_ID = "1234567890-abcdefgh"
    LIFF_CHANNEL_ID = "1234567890"
    JWT_SECRET_KEY = "test-jwt-secret"
    PATIENT_DATA_KEY = ""  # 由 SECRET_KEY 衍生
    RATELIMIT_ENABLED = False
    SCHEDULER_ENABLED = False
    OPENAI_API_KEY = "test-openai-key"  # 測試以 FakeLlmClient / FakeVisionClient 替換，不會真的呼叫
    MEDIA_ROOT = os.path.join(tempfile.gettempdir(), "mediagent-test-media")


class ProductionConfig(BaseConfig):
    ENV = "production"

    REQUIRED_KEYS = (
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_LOGIN_CHANNEL_ID",
        "LINE_LOGIN_CHANNEL_SECRET",
        "LIFF_ID",
        "PATIENT_DATA_KEY",
        "OPENAI_API_KEY",
    )

    @classmethod
    def validate(cls) -> None:
        """正式環境啟動前檢查必要環境變數是否齊全。"""
        missing = [key for key in cls.REQUIRED_KEYS if not os.getenv(key)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


CONFIG_MAP: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


DEFAULT_SECRETS = ("dev-secret-key", "change-me", "change-me-too")


def get_config(name: str | None = None) -> type[BaseConfig]:
    """依名稱（或 FLASK_ENV）取得設定類別。

    FLASK_ENV 未設定時視為 production：漏設環境變數時寧可啟動失敗，
    也不要用寫死的開發金鑰對外服務（否則任何人都能簽出任意使用者的 JWT）。
    """
    env_name = name or os.getenv("FLASK_ENV", "production")
    config = CONFIG_MAP.get(env_name, ProductionConfig)
    if config is ProductionConfig:
        ProductionConfig.validate()
        if config.SECRET_KEY in DEFAULT_SECRETS or config.JWT_SECRET_KEY in DEFAULT_SECRETS:
            raise RuntimeError("SECRET_KEY / JWT_SECRET_KEY 不可使用範例預設值")
    return config
