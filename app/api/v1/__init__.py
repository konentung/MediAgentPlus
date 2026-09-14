"""API v1：所有端點掛在 /api/v1 之下。"""

from __future__ import annotations

from flask import Blueprint

from app.api.v1.admin import admin_bp
from app.api.v1.auth import auth_bp
from app.api.v1.health import health_bp
from app.api.v1.liff import liff_bp

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
for bp in (health_bp, auth_bp, liff_bp, admin_bp):
    api_v1_bp.register_blueprint(bp)
