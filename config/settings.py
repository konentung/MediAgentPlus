"""應用程式設定。

所有敏感資訊一律由環境變數（.env）注入，程式碼內不得硬編碼任何金鑰。
"""

from __future__ import annotations

import os
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
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024
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
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(_env("JWT_REFRESH_EXPIRES_DAYS", "30")))

    # ---- 病人資料欄位加密（身分證字號）----
    # Fernet 金鑰，產生方式見 .env.example。
    # 未設定時由 SECRET_KEY 衍生，僅供開發；正式環境必填（見 ProductionConfig.REQUIRED_KEYS）。
    PATIENT_DATA_KEY = _env("PATIENT_DATA_KEY")


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
