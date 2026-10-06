"""找醫院訊息：入口（請分享位置）、依距離排序的急診即時看板、附近診所。"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message

from app.domain import triage_rules as rules
from app.domain.enums import Intent
from app.line.intents import postback_data
from app.line.messages import theme
from app.line.messages.builder import MessageBuilder as M
from app.utils.geo import format_distance

MAX_ROWS = 10
EMERGENCY_HINT = "🚨 有生命危險請直接撥打 119，救護車會依現場狀況送到最適合的醫院。"


def menu_messages(*, emergency: bool, department: str | None = None) -> list[Message]:
    """入口：只認使用者位置，請對方分享位置（紅旗時先給 119）。`department` 是依症狀建議的科別。"""
    target = f"有{department}的" if department else ""  # 修改：說明會依症狀科別推薦
    lead = (
        f"正在為你找最近、目前不壅塞、{target}急救責任醫院。請分享位置，我會依距離與急診即時狀況推薦。"
        if emergency
        else f"找醫院：請分享位置，我會列出離你最近的急救責任醫院與目前急診壅塞狀況，"
        f"並推薦最適合前往的{target}一家；非急症也可以改找附近診所。"
    )
    items = [M.location_item("📍 分享位置")]
    if emergency:
        items.insert(0, M.uri_item("🚨 撥打 119", "tel:119"))
    text = f"{EMERGENCY_HINT}\n{lead}" if emergency else lead
    return [M.text(text, quick_replies=items)]


def no_coordinates_messages() -> list[Message]:
    """院所主檔沒有座標、地址也認不出鄉鎮市區（無法估距離）時的提示。"""
    return [
        M.text(
            "目前還無法依距離推薦醫院（院所座標尚未建置）。有生命危險請直接撥打 119。",
            quick_replies=[M.uri_item("🚨 撥打 119", "tel:119")],
        )
    ]


def er_unavailable_messages(board: dict[str, Any], *, emergency: bool) -> list[Message]:
    """健保署急診即時資料暫時抓不到（快照是空的）：不能依壅塞推薦，但仍可找附近診所。"""
    items = [M.uri_item("🚨 撥打 119", "tel:119")]
    city, district = board.get("area_name"), board.get("district")
    if city and not emergency:
        origin = board.get("origin") or {}
        items.append(
            M.postback_item(
                "🩺 找附近診所",
                Intent.FIND_HOSPITAL,
                action="clinics",
                city=city,
                district=district or "",
                lat=str(origin.get("lat", "")),
                lng=str(origin.get("lng", "")),
            )
        )
    items.append(M.location_item("📍 再試一次"))
    return [
        M.text(
            "健保署急診即時資料暫時無法取得，現在沒辦法依壅塞狀況推薦醫院，請稍後再試。"
            "有生命危險請直接撥打 119。",
            quick_replies=items,
        )
    ]


def board_messages(board: dict[str, Any], liff_url: str, *, emergency: bool) -> list[Message]:
    hospitals = board.get("hospitals") or []
    area = board.get("area_name") or "該地區"
    by_distance = board.get("mode") == "distance"
    recommended = board.get("recommended")
    if not hospitals:
        return [
            M.text(
                f"{area} 目前沒有重度級急救責任醫院的即時資料。{EMERGENCY_HINT}",
                quick_replies=[
                    M.uri_item("🚨 撥打 119", "tel:119"),
                    M.postback_item("🗺 選其他縣市", Intent.FIND_HOSPITAL, action="areas"),
                ],
            )
        ]
    updated = board.get("reported_at_label") or ""
    stale = "（資料可能延遲）" if board.get("stale") else ""
    contents: list[dict[str, Any]] = []  # 修改：更新時間改放表頭副標
    subtitle = f"更新 {updated}{stale}"
    department = board.get("department")
    if by_distance:
        if recommended:
            headline = (
                f"✅ 建議前往：{recommended['hospital_name']}"
                f"（{_distance_label(recommended)}，{recommended['level_label']}）"
            )
        else:
            headline = "⚠ 附近的急救責任醫院目前都壅塞，請撥打 119 由救護車調度，或參考下方等候人數"
        contents.append(
            theme.text(headline, bold=True, color=theme.INK if recommended else theme.RED_STRONG)
        )
    for index, h in enumerate(hospitals[:MAX_ROWS]):
        detail = f"等看診 {h['wait_see']}・等住院 {h['wait_admit']}・等 ICU {h['wait_icu']}"
        if h.get("full_119"):
            detail += "・119 滿床通報"
        distance = _distance_prefix(h)
        area_tag = f"{h['area_name']} " if by_distance and h.get("area_name") != area else ""
        dept_tag = f"・有{department}" if department and h.get("department_matched") else ""
        contents.extend(
            theme.row(
                [
                    theme.text(
                        f"{rules.LEVEL_EMOJI[h['level']]} {distance}{area_tag}"
                        f"{h['hospital_name']}（{h['tier_label']}）{h['level_label']}{dept_tag}",
                        bold=True,
                    ),
                    theme.secondary_text(detail),
                ],
                first=index == 0 and not contents,
            )
        )
    if len(hospitals) > MAX_ROWS:
        contents.append(
            theme.secondary_text(
                f"…另有 {len(hospitals) - MAX_ROWS} 家，請開啟完整看板", margin="md"
            )
        )
    buttons: list[dict[str, Any]] = []
    if emergency:
        buttons.append(
            theme.primary_button(
                {"type": "uri", "label": "🚨 撥打 119", "uri": "tel:119"}, alert=True
            )
        )
    clinic_params = {"action": "clinics", "city": area}
    if department:
        clinic_params["department"] = department  # 新增：找附近診所也依同一科別
    if board.get("district"):
        clinic_params["district"] = board["district"]
    if board.get("origin"):
        clinic_params["lat"] = str(board["origin"]["lat"])
        clinic_params["lng"] = str(board["origin"]["lng"])
    buttons.append(
        theme.primary_button(
            {
                "type": "postback",
                "label": "🩺 非急症：找附近診所",
                "data": postback_data(Intent.FIND_HOSPITAL, **clinic_params),
                "displayText": "非急症，找附近診所",
            }
        )
    )
    buttons.append(
        theme.secondary_button({"type": "uri", "label": "完整看板 / 其他縣市", "uri": liff_url})
    )
    bubble = theme.bubble(
        contents,
        header=theme.header(
            "🏥 離你最近的急救責任醫院" if by_distance else f"🏥 {area} 急診即時狀況",
            subtitle,
            alert=emergency,
        ),
        footer=buttons,
        size="mega",
    )
    messages: list[Message] = [M.flex(f"{area} 急診即時狀況", bubble)]
    if emergency:
        messages.insert(0, M.text(EMERGENCY_HINT))
    if recommended and recommended.get("lat") is not None:
        # 附上推薦醫院的位置訊息，點開就能導航
        messages.append(
            M.location(
                recommended["hospital_name"],
                recommended.get("address") or recommended.get("area_name") or "",
                recommended["lat"],
                recommended["lng"],
            )
        )
    return messages


def _departments_label(departments: str, limit: int = 6) -> str:
    """逗號分隔的科別 → 「A、B、C…」，最多列幾個。"""
    parts = [p for p in departments.split(",") if p]
    label = "、".join(parts[:limit])
    return f"{label}…" if len(parts) > limit else label


def clinics_prompt_messages(department: str | None) -> list[Message]:
    """找附近診所但還不知道使用者在哪：請分享位置（科別由 AI 依症狀建議）。"""
    target = f"有{department}的診所" if department else "診所"
    return [
        M.text(
            f"請分享位置，我幫你找附近{target}，非急症就近看診最快。",
            quick_replies=[M.location_item("📍 分享位置")],
        )
    ]


def clinics_messages(result: dict[str, Any], liff_url: str) -> list[Message]:
    rows = result.get("hospitals") or []
    area = f"{result['city']}{result.get('district') or ''}"
    department = result.get("department")
    by_ai = result.get("selected_by") == "ai"
    if not rows and (department or by_ai):
        # 修改：找不到適合的院所時不列不相干的診所，直接說明
        target = f"適合看{department}的診所" if department else "適合的診所"
        return [
            M.text(
                f"{area}的院所資料裡找不到{target}，沒辦法依你的症狀推薦。"
                "可以先打電話問住家附近的診所有沒有看這一科；症狀加重或很不舒服請到急診。",
                quick_replies=[
                    M.postback_item(
                        "🏥 找醫院",
                        Intent.FIND_HOSPITAL,
                        **({"department": department} if department else {}),
                    )
                ],
            )
        ]
    if not rows:
        return [
            M.text(
                f"{area} 目前沒有院所主檔資料可推薦。"
                "輕症可先到住家附近的診所，或撥打 1922 / 各縣市衛生局詢問。",
                quick_replies=[M.uri_item("急診看板", liff_url)],
            )
        ]
    # 修改：不放說明用的引言，表頭副標只留「地區・科別」；同區沒有、退到同縣市時只寫縣市
    if result.get("scope") == "city":
        area = result["city"]
    subtitle = f"{area}・{department}" if department else area
    contents: list[dict[str, Any]] = []
    tier_label = {"clinic": "診所", "district": "地區醫院", "regional": "區域醫院"}
    for h in rows[:MAX_ROWS]:
        tier = tier_label.get(h.get("capacity_tier"), "院所")
        line_items: list[dict[str, Any]] = [
            theme.text(f"{_distance_prefix(h)}{h['name']}（{tier}）", bold=True)
        ]
        if h.get("address"):
            line_items.append(theme.secondary_text(h["address"]))
        if h.get("departments"):
            line_items.append(theme.secondary_text(f"科別：{_departments_label(h['departments'])}"))
        if h.get("contact_phone"):
            line_items.append(
                theme.text(
                    f"📞 {h['contact_phone']}",
                    size="xs",
                    bold=True,
                    color=theme.INK,
                    action={"type": "uri", "label": "撥打", "uri": _tel(h["contact_phone"])},
                )
            )
        contents.extend(theme.row(line_items, first=not contents))
    bubble = theme.bubble(
        contents, header=theme.header("🩺 附近診所 / 醫院", subtitle), size="mega"
    )
    return [M.flex("附近診所", bubble)]


def _distance_label(h: dict[str, Any]) -> str:
    """距離文字；院所沒有座標、用鄉鎮市區中心點估的加「約」。"""
    return ("約 " if h.get("distance_approx") else "") + format_distance(h.get("distance_km"))


def _distance_prefix(h: dict[str, Any]) -> str:
    return f"{_distance_label(h)}　" if h.get("distance_km") is not None else ""


def _tel(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return f"tel:{digits or phone}"
