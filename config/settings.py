"""應用程式設定。

所有敏感資訊一律由環境變數（.env）注入，程式碼內不得硬編碼任何金鑰。
"""

from __future__ import annotations

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class BaseConfig:
    """所有環境共用的預設值。"""

    ENV = "base"
    DEBUG = False
    TESTING = False
    SECRET_KEY = _env("SECRET_KEY", "dev-secret-key")
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")

    # ---- PostgreSQL ----
    SQLALCHEMY_DATABASE_URI = _env(
        "DATABASE_URL", "postgresql+psycopg://mediagent:mediagent@localhost:5432/mediagent"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

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

    # ---- 前端 / 業務參數 ----
    FRONTEND_URL = _env("FRONTEND_URL", "")
    BINDING_MAX_FAILURES = int(_env("BINDING_MAX_FAILURES", "3"))


class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True


class TestingConfig(BaseConfig):
    ENV = "testing"
    TESTING = True
    # 測試不依賴 PostgreSQL，模型層尚未定義時以 in-memory SQLite 佔位
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS: dict = {}
    LINE_CHANNEL_SECRET = "test-channel-secret"
    LINE_CHANNEL_ACCESS_TOKEN = "test-access-token"
    LINE_LOGIN_CHANNEL_ID = "1234567890"
    LINE_LOGIN_CHANNEL_SECRET = "test-login-secret"
    LIFF_ID = "1234567890-abcdefgh"
    LIFF_CHANNEL_ID = "1234567890"
    JWT_SECRET_KEY = "test-jwt-secret"


class ProductionConfig(BaseConfig):
    ENV = "production"

    REQUIRED_KEYS = (
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_LOGIN_CHANNEL_ID",
        "LINE_LOGIN_CHANNEL_SECRET",
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


def get_config(name: str | None = None) -> type[BaseConfig]:
    """依名稱（或 FLASK_ENV）取得設定類別。"""
    env_name = name or os.getenv("FLASK_ENV", "development")
    config = CONFIG_MAP.get(env_name, DevelopmentConfig)
    if config is ProductionConfig:
        ProductionConfig.validate()
    return config
