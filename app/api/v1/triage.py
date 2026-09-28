"""急診動態分流 API。

公開（開放資料，只做 rate limit）：
  GET /api/v1/triage/board?area=01        某縣市 / 全國急診即時看板（LIFF /liff/er 用）
  GET /api/v1/triage/board?lat=&lng=      以使用者位置為準：依距離排序 + 推薦最近未壅塞的醫院
  GET /api/v1/triage/areas                地區代碼與門檻說明
  GET /api/v1/triage/advisories           進行中的分流建議，需 `X-Api-Key`（訂閱的 secret）

院方後台（/api/v1/admin/triage/*，頁面在 /admin/triage）：
  GET  board / advisories / hospitals/<id>/history / stats     triage:read
  GET/POST/PATCH/DELETE subscriptions[/<id>]、POST subscriptions/<id>/test   triage:subscriptions
  POST poll   立即抓取並評估（等同 cron 一次）                 triage:subscriptions
"""

from __future__ import annotations

from flask import Blueprint, request

from app.auth.decorators import current_principal, require_permission
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission
from app.domain.triage_rules import public_rules
from app.utils.geo import parse_coords

triage_bp = Blueprint("triage", __name__, url_prefix="/triage")
admin_triage_bp = Blueprint("admin_triage", __name__, url_prefix="/admin/triage")


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


def _area_arg() -> str | None:
    return request.args.get("area") or None


def _board_from_args() -> dict:
    """?lat=&lng= 依距離（使用者位置）；否則 ?area= 縣市 / 全國。"""
    triage = get_container().triage
    coords = parse_coords(request.args.get("lat"), request.args.get("lng"))
    if coords:
        board = triage.board_for_location(coords[0], coords[1], address=request.args.get("address"))
        if board is not None:
            return board
    return triage.board(area_code=_area_arg())


# ---- 公開 ----


@triage_bp.get("/board")
@rate_limit("triage_board", limit=60, per_seconds=60)
def public_board():
    return ok(_board_from_args())


@triage_bp.get("/areas")
def public_areas():
    return ok(public_rules())


@triage_bp.get("/advisories")
@rate_limit("triage_pull", limit=60, per_seconds=60)
def pull_advisories():
    """外部系統（119 勤務指揮中心）主動拉取；`X-Api-Key` 為後台建立訂閱時給的 secret。"""
    api_key = request.headers.get("X-Api-Key") or request.args.get("api_key")
    return ok(get_container().triage.advisories_for_api_key(api_key))


# ---- 院方後台 ----


@admin_triage_bp.get("/board")
@require_permission(Permission.TRIAGE_READ)
def admin_board():
    return ok(_board_from_args())


@admin_triage_bp.get("/stats")
@require_permission(Permission.TRIAGE_READ)
def admin_stats():
    return ok(get_container().triage.stats())


@admin_triage_bp.get("/advisories")
@require_permission(Permission.TRIAGE_READ)
def admin_advisories():
    status = request.args.get("status") or None
    if status == "all":
        status = None
    return ok(get_container().triage.list_advisories(status=status, area_code=_area_arg()))


@admin_triage_bp.get("/hospitals/<nhi_hospital_id>/history")
@require_permission(Permission.TRIAGE_READ)
def admin_history(nhi_hospital_id: str):
    hours = request.args.get("hours", type=int) or 24
    return ok(get_container().triage.hospital_history(nhi_hospital_id, hours=hours))


@admin_triage_bp.post("/poll")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
@rate_limit("triage_poll", limit=6, per_seconds=600, by="user")
def admin_poll():
    return ok(get_container().triage.poll())


@admin_triage_bp.get("/subscriptions")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
def list_subscriptions():
    return ok(get_container().triage.list_subscriptions(current_principal()))


@admin_triage_bp.post("/subscriptions")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
def create_subscription():
    return ok(
        get_container().triage.create_subscription(current_principal(), _json_body()), status=201
    )


@admin_triage_bp.patch("/subscriptions/<subscription_id>")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
def update_subscription(subscription_id: str):
    return ok(
        get_container().triage.update_subscription(
            current_principal(), subscription_id, _json_body()
        )
    )


@admin_triage_bp.delete("/subscriptions/<subscription_id>")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
def delete_subscription(subscription_id: str):
    get_container().triage.delete_subscription(current_principal(), subscription_id)
    return ok(None)


@admin_triage_bp.post("/subscriptions/<subscription_id>/test")
@require_permission(Permission.TRIAGE_SUBSCRIPTION_MANAGE)
@rate_limit("triage_test", limit=10, per_seconds=600, by="user")
def test_subscription(subscription_id: str):
    return ok(get_container().triage.send_test(current_principal(), subscription_id))
