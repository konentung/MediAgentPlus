"""Flask 應用程式工廠。"""

from __future__ import annotations

from flask import Flask, Response, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.admin_pages import admin_pages_bp
from app.api.v1 import api_v1_bp
from app.cli import register_cli
from app.core.container import ServiceContainer
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging
from app.extensions import db, migrate
from app.liff_pages import liff_pages_bp
from app.line.dispatcher import EventDispatcher
from app.line.webhook import line_bp
from app.pages import pages_bp
from config import get_config


def create_app(config_name: str | None = None, container: ServiceContainer | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    app.json.ensure_ascii = False  # 讓 JSON 直接輸出中文

    configure_logging(app)
    _register_extensions(app)
    app.extensions["container"] = container or ServiceContainer.build(app)
    app.extensions["line_dispatcher"] = EventDispatcher.default(
        app.extensions["container"], app.config["LIFF_ID"]
    )
    _register_blueprints(app)
    register_error_handlers(app)
    register_cli(app)
    _register_security(app)
    return app


def _register_security(app: Flask) -> None:
    proxies = app.config.get("TRUSTED_PROXY_COUNT", 0)
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # 後台不允許被嵌入 iframe（clickjacking）；LIFF 頁面由 LINE 的 WebView 開啟，不受影響
        if request.path.startswith("/admin"):
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response


def _register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)


def _register_blueprints(app: Flask) -> None:
    app.register_blueprint(pages_bp)
    app.register_blueprint(liff_pages_bp)
    app.register_blueprint(admin_pages_bp)
    app.register_blueprint(api_v1_bp)
    app.register_blueprint(line_bp)
