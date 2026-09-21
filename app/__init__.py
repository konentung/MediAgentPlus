"""Flask 應用程式工廠。"""

from __future__ import annotations

from flask import Flask

from app.api.v1 import api_v1_bp
from app.cli import register_cli
from app.core.container import ServiceContainer
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging
from app.extensions import db, migrate
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
    return app


def _register_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)


def _register_blueprints(app: Flask) -> None:
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_v1_bp)
    app.register_blueprint(line_bp)
