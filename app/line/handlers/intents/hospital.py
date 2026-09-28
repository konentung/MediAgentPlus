"""找醫院 → 急診即時看板（健保署急診即時訊息）+ 附近診所（不需基本資料）。

流程：
  menu   → 設定「等位置（for=hospital）」，Quick Reply：分享位置 / 住址 / 選縣市
  areas  → 四張 Flex 列出縣市按鈕 → area → 該縣市看板（依餘裕排序、燈號）
  位置訊息（MessageHandler 依狀態轉來，params 帶 address + lat / lng）
         → 依距離列出最近的醫院、推薦最近未壅塞者並附上該醫院的位置訊息；沒有院所座標時退回縣市看板
  clinics → 非急症：從院所主檔找同區診所 / 地區醫院
  emergency=1（紅旗命中）：文案改急救導向、按鈕加撥打 119
"""

from __future__ import annotations

from app.domain import triage_rules as rules
from app.domain.enums import Intent, Permission
from app.line.context import BotContext
from app.line.handlers.intents.base import IntentHandler
from app.line.messages import hospital as msg
from app.line.state_store import AWAIT_LOCATION, PENDING_LOCATION
from app.services.pharmacy_service import parse_address
from app.utils.geo import parse_coords


class FindHospitalHandler(IntentHandler):
    intent = Intent.FIND_HOSPITAL
    feature_name = "找醫院"
    required_permission = Permission.CONSULT_HOSPITAL

    def handle(self, ctx: BotContext, params: dict[str, str]) -> None:
        emergency = params.get("emergency") == "1"
        action = params.get("action")
        if action == "areas":
            ctx.reply(msg.area_picker_messages(emergency=emergency))
            return
        if action == "area":
            self._reply_board(ctx, params.get("area"), emergency)
            return
        if action == "clinics":
            self._reply_clinics(ctx, params)
            return

        address = params.get("address")
        coords = parse_coords(params.get("lat"), params.get("lng"))
        source = params.get("source")
        if source == "profile":
            address, coords = self._profile_address(ctx), None
        elif source == "state":
            state = ctx.container.conversation_state.pop(ctx.line_user_id)
            if state and state.action == PENDING_LOCATION:
                address = state.payload.get("address")
                coords = parse_coords(state.payload.get("lat"), state.payload.get("lng"))
        if coords and self._reply_for_location(ctx, coords, address, emergency):
            return
        if address:
            self._reply_for_address(ctx, address, emergency)
            return

        payload = {"for": "hospital"}
        if emergency:
            payload["emergency"] = "1"
        ctx.container.conversation_state.set(ctx.line_user_id, AWAIT_LOCATION, payload)
        ctx.reply(
            msg.menu_messages(
                has_profile_address=self._profile_address(ctx) is not None, emergency=emergency
            )
        )

    # ---- helpers ----

    def _reply_for_location(
        self, ctx: BotContext, coords: tuple[float, float], address: str | None, emergency: bool
    ) -> bool:
        board = ctx.container.triage.board_for_location(coords[0], coords[1], address=address)
        if board is None:
            return False  # 院所主檔沒有座標 → 退回縣市看板
        ctx.container.conversation_state.clear(ctx.line_user_id)
        ctx.reply(msg.board_messages(board, self._liff(ctx, board), emergency=emergency))
        return True

    def _reply_for_address(self, ctx: BotContext, address: str, emergency: bool) -> None:
        board = ctx.container.triage.board_for_address(address)
        if board is None:
            ctx.reply(msg.unknown_location_messages())
            return
        ctx.container.conversation_state.clear(ctx.line_user_id)
        ctx.reply(msg.board_messages(board, self._liff(ctx, board), emergency=emergency))

    def _reply_board(self, ctx: BotContext, area_code: str | None, emergency: bool) -> None:
        if area_code not in rules.AREA_NAMES:
            ctx.reply(msg.area_picker_messages(emergency=emergency))
            return
        ctx.container.conversation_state.clear(ctx.line_user_id)
        board = ctx.container.triage.board(area_code=area_code)
        ctx.reply(msg.board_messages(board, self._liff(ctx, board), emergency=emergency))

    def _reply_clinics(self, ctx: BotContext, params: dict[str, str]) -> None:
        city = (params.get("city") or "").replace("臺", "台")
        district = params.get("district") or None
        if not city:
            parsed_city, parsed_district = parse_address(self._profile_address(ctx))
            city, district = parsed_city or "", parsed_district
        if not city:
            ctx.reply(msg.unknown_location_messages())
            return
        coords = parse_coords(params.get("lat"), params.get("lng"))
        result = ctx.container.triage.nearby_clinics(
            city, district, lat=coords[0] if coords else None, lng=coords[1] if coords else None
        )
        ctx.reply(msg.clinics_messages(result, ctx.liff_url("/er")))

    @staticmethod
    def _liff(ctx: BotContext, board: dict) -> str:
        origin = board.get("origin")
        if origin:
            return ctx.liff_url(f"/er?lat={origin['lat']}&lng={origin['lng']}")
        area = board.get("area_code")
        return ctx.liff_url(f"/er?area={area}" if area else "/er")

    @staticmethod
    def _profile_address(ctx: BotContext) -> str | None:
        if not ctx.principal.user_id:
            return None
        profile = ctx.container.patient_repo.get_profile(ctx.principal.user_id) or {}
        city = profile.get("address_city")
        if not city:
            return None
        return f"{city}{profile.get('address_district') or ''}{profile.get('address_street') or ''}"
