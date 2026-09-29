"""找醫院（急診動態分流）→ 依使用者位置推薦適合去的急救責任醫院（不需基本資料）。

流程：
  menu     → 設定「等位置（for=hospital）」，Quick Reply 只有「分享位置」（紅旗時多「撥打 119」）
  位置訊息（MessageHandler 依狀態轉來，params 帶 address + lat / lng；PENDING_LOCATION 也存座標）
           → `board_for_location()` 依距離列最近的醫院、推薦最近未壅塞者，
             並附上該醫院的位置訊息（可導航）
  clinics  → 非急症：找附近診所 / 地區醫院（同鄉鎮市區優先、診所優先；`department` 依 AI 建議的
             科別篩選）；沒有縣市也沒有座標時先請對方分享位置（狀態 for=clinics 帶 department），
             位置訊息回來再查
  emergency=1（紅旗命中）：文案改急救導向、按鈕加撥打 119
"""

from __future__ import annotations

from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import hospital as msg
from app.line.state_store import AWAIT_LOCATION, PENDING_LOCATION
from app.services.pharmacy_service import parse_address
from app.utils.geo import nearest_district, parse_coords


class FindHospitalHandler(IntentHandler):
    intent = Intent.FIND_HOSPITAL
    feature_name = "找醫院"
    required_permission = Permission.CONSULT_HOSPITAL

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        emergency = params.get("emergency") == "1"
        if params.get("action") == "clinics" or params.get("for") == "clinics":
            self._reply_clinics(ctx, params)
            return

        address = params.get("address")
        coords = parse_coords(params.get("lat"), params.get("lng"))
        if params.get("source") == "state":
            # 使用者先傳了位置才選「找醫院」：用剛才存下的座標
            state = ctx.container.conversation_state.pop(ctx.line_user_id)
            if state and state.action == PENDING_LOCATION:
                address = state.payload.get("address")
                coords = parse_coords(state.payload.get("lat"), state.payload.get("lng"))
        if coords:
            board = ctx.container.triage.board_for_location(coords[0], coords[1], address=address)
            if board is None:
                ctx.reply(msg.no_coordinates_messages())
                return
            if board.get("unavailable"):
                ctx.reply(msg.er_unavailable_messages(board, emergency=emergency))
                return
            ctx.container.conversation_state.clear(ctx.line_user_id)
            ctx.reply(msg.board_messages(board, self._liff(ctx, board), emergency=emergency))
            return

        payload = {"for": "hospital"}
        if emergency:
            payload["emergency"] = "1"
        ctx.container.conversation_state.set(ctx.line_user_id, AWAIT_LOCATION, payload)
        ctx.reply(msg.menu_messages(emergency=emergency))

    def _reply_clinics(self, ctx: BotContext, params: dict[str, str]) -> None:
        department = (params.get("department") or "").strip() or None
        coords = parse_coords(params.get("lat"), params.get("lng"))
        city = (params.get("city") or "").replace("臺", "台")
        district = params.get("district") or None
        if not city and coords:
            # 位置訊息回來：地址可能是英文或空白，抓不到就用座標反推鄉鎮市區
            city, district = parse_address(params.get("address"))
            if city is None:
                city, district = nearest_district(coords[0], coords[1]) or (None, None)
        if not city:
            payload = {"for": "clinics", "department": department or ""}
            ctx.container.conversation_state.set(ctx.line_user_id, AWAIT_LOCATION, payload)
            ctx.reply(msg.clinics_prompt_messages(department))
            return
        result = ctx.container.triage.nearby_clinics(
            city,
            district,
            lat=coords[0] if coords else None,
            lng=coords[1] if coords else None,
            department=department,
        )
        ctx.container.conversation_state.clear(ctx.line_user_id)
        ctx.reply(msg.clinics_messages(result, ctx.liff_url("/er")))

    @staticmethod
    def _liff(ctx: BotContext, board: dict) -> str:
        origin = board.get("origin")
        return ctx.liff_url(f"/er?lat={origin['lat']}&lng={origin['lng']}" if origin else "/er")
