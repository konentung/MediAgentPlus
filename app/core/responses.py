"""統一 API 回應格式。"""

from __future__ import annotations

from typing import Any

from flask import Response, jsonify


def ok(data: Any = None, status: int = 200, **meta: Any) -> tuple[Response, int]:
    """成功回應：{"success": true, "data": ..., "meta": {...}}"""
    body: dict[str, Any] = {"success": True, "data": data}
    if meta:
        body["meta"] = meta
    return jsonify(body), status


def fail(
    code: str, message: str, status: int = 400, details: dict | None = None
) -> tuple[Response, int]:
    """失敗回應：{"success": false, "error": {"code": ..., "message": ...}}"""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
