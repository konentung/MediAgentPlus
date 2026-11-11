"""急診分流（找醫院）公開 API：開放資料，只做 rate limit、不需登入。

GET /api/v1/triage/board?lat=&lng=      以使用者位置為準：依距離排序 + 推薦最近未壅塞的醫院
GET /api/v1/triage/board?area=01        某縣市 / 全國急診即時看板（LIFF /liff/er 的縣市切換）
GET /api/v1/triage/areas                地區代碼與燈號門檻說明
"""

from __future__ import annotations

from flask import Blueprint, request

from app.core.container import get_container
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.triage_rules import public_rules
from app.utils.geo import parse_coords

triage_bp = Blueprint("triage", __name__, url_prefix="/triage")


@triage_bp.get("/board")
@rate_limit("triage_board", limit=60, per_seconds=60)
def public_board():
    """?lat=&lng= 依距離（使用者位置）；否則 ?area= 縣市 / 全國。"""
    triage = get_container().triage
    coords = parse_coords(request.args.get("lat"), request.args.get("lng"))
    if coords:
        board = triage.board_for_location(coords[0], coords[1], address=request.args.get("address"))
        if board is not None and not board.get("unavailable"):
            return ok(board)
    return ok(triage.board(area_code=request.args.get("area") or None))


@triage_bp.get("/areas")
def public_areas():
    return ok(public_rules())
