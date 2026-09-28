"""找醫院訊息：入口（請分享位置）、依距離排序的急診即時看板、附近診所。"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message

from app.domain import triage_rules as rules
from app.domain.enums import Intent
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M
from app.utils.geo import format_distance

BRAND = "#2F6F62"
ALERT = "#B42318"
MAX_ROWS = 10
EMERGENCY_HINT = "🚨 有生命危險請直接撥打 119，救護車會依現場狀況送到最適合的醫院。"
BOARD_HINT = "燈號依「等住院 / 等 ICU / 等看診 / 119 滿床通報」判斷，供就醫前參考。"


def menu_messages(*, emergency: bool) -> list[Message]:
    """入口：只認使用者位置，請對方分享位置（紅旗時先給 119）。"""
    lead = (
        "正在為你找最近、目前不壅塞的急救責任醫院。請分享位置，我會依距離與急診即時狀況推薦。"
        if emergency
        else "找醫院：請分享位置，我會列出離你最近的急救責任醫院與目前急診壅塞狀況，"
        "並推薦最適合前往的一家；非急症也可以改找附近診所。"
    )
    items = [M.location_item("📍 分享位置")]
    if emergency:
        items.insert(0, M.uri_item("🚨 撥打 119", "tel:119"))
    text = f"{EMERGENCY_HINT}\n{lead}" if emergency else lead
    return [M.text(text, quick_replies=items)]


def no_coordinates_messages() -> list[Message]:
    """院所主檔還沒有座標（尚未匯入 data/er_hospitals.csv）時的提示。"""
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
    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"更新 {updated}{stale}　健保署急診即時訊息",
            "size": "xxs",
            "color": "#888888",
            "wrap": True,
        }
    ]
    if by_distance:
        if recommended:
            headline = (
                f"✅ 建議前往：{recommended['hospital_name']}"
                f"（{format_distance(recommended.get('distance_km'))}，{recommended['level_label']}）"
            )
        else:
            headline = "⚠ 附近的急救責任醫院目前都壅塞，請撥打 119 由救護車調度，或參考下方等候人數"
        contents.append(
            {
                "type": "text",
                "text": headline,
                "size": "sm",
                "weight": "bold",
                "color": BRAND if recommended else ALERT,
                "wrap": True,
                "margin": "md",
            }
        )
    for h in hospitals[:MAX_ROWS]:
        detail = f"等看診 {h['wait_see']}・等住院 {h['wait_admit']}・等 ICU {h['wait_icu']}"
        if h.get("full_119"):
            detail += "・119 滿床通報"
        distance = _distance_prefix(h)
        area_tag = f"{h['area_name']} " if by_distance and h.get("area_name") != area else ""
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{rules.LEVEL_EMOJI[h['level']]} {distance}{area_tag}"
                        f"{h['hospital_name']}（{h['tier_label']}）{h['level_label']}",
                        "size": "sm",
                        "weight": "bold",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": detail,
                        "size": "xs",
                        "color": "#666666",
                        "wrap": True,
                    },
                ],
            }
        )
    if len(hospitals) > MAX_ROWS:
        contents.append(
            {
                "type": "text",
                "text": f"…另有 {len(hospitals) - MAX_ROWS} 家，請開啟完整看板",
                "size": "xs",
                "color": "#888888",
            }
        )
    contents.append(
        {
            "type": "text",
            "text": BOARD_HINT,
            "size": "xxs",
            "color": "#888888",
            "wrap": True,
            "margin": "md",
        }
    )
    buttons: list[dict[str, Any]] = []
    if emergency:
        buttons.append(
            {
                "type": "button",
                "style": "primary",
                "color": ALERT,
                "height": "sm",
                "action": {"type": "uri", "label": "🚨 撥打 119", "uri": "tel:119"},
            }
        )
    clinic_params = {"action": "clinics", "city": area}
    if board.get("district"):
        clinic_params["district"] = board["district"]
    if board.get("origin"):
        clinic_params["lat"] = str(board["origin"]["lat"])
        clinic_params["lng"] = str(board["origin"]["lng"])
    buttons.append(
        {
            "type": "button",
            "style": "primary",
            "color": BRAND,
            "height": "sm",
            "action": {
                "type": "postback",
                "label": "🩺 非急症：找附近診所",
                "data": postback_data(Intent.FIND_HOSPITAL, **clinic_params),
                "displayText": "非急症，找附近診所",
            },
        }
    )
    buttons.append(
        {
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "action": {"type": "uri", "label": "完整看板 / 其他縣市", "uri": liff_url},
        }
    )
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": ALERT if emergency else BRAND,
            "contents": [
                {
                    "type": "text",
                    "text": "🏥 離你最近的急救責任醫院"
                    if by_distance
                    else f"🏥 {area} 急診即時狀況",
                    "weight": "bold",
                    "color": "#FFFFFF",
                    "size": "md",
                }
            ],
        },
        "body": {"type": "box", "layout": "vertical", "contents": contents},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons},
    }
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


def clinics_messages(result: dict[str, Any], liff_url: str) -> list[Message]:
    rows = result.get("hospitals") or []
    area = f"{result['city']}{result.get('district') or ''}"
    if not rows:
        return [
            M.text(
                f"{area} 目前沒有院所主檔資料可推薦。"
                "輕症可先到住家附近的診所，或撥打 1922 / 各縣市衛生局詢問。",
                quick_replies=[M.uri_item("急診看板", liff_url)],
            )
        ]
    scope = {"district": "同區", "distance": "離你最近"}.get(result.get("scope"), "同縣市")
    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"{area}（{scope}）非急症可先就近看診，減少急診等候：",
            "size": "xs",
            "color": "#666666",
            "wrap": True,
        }
    ]
    tier_label = {"clinic": "診所", "district": "地區醫院", "regional": "區域醫院"}
    for h in rows[:MAX_ROWS]:
        tier = tier_label.get(h.get("capacity_tier"), "院所")
        line_items: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"{_distance_prefix(h)}{h['name']}（{tier}）",
                "size": "sm",
                "weight": "bold",
                "wrap": True,
            }
        ]
        if h.get("address"):
            line_items.append(
                {
                    "type": "text",
                    "text": h["address"],
                    "size": "xs",
                    "color": "#666666",
                    "wrap": True,
                }
            )
        if h.get("contact_phone"):
            line_items.append(
                {
                    "type": "text",
                    "text": f"📞 {h['contact_phone']}",
                    "size": "xs",
                    "color": BRAND,
                    "action": {"type": "uri", "label": "撥打", "uri": _tel(h["contact_phone"])},
                }
            )
        contents.append(
            {"type": "box", "layout": "vertical", "margin": "md", "contents": line_items}
        )
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": BRAND,
            "contents": [
                {"type": "text", "text": "🩺 附近診所 / 醫院", "weight": "bold", "color": "#FFFFFF"}
            ],
        },
        "body": {"type": "box", "layout": "vertical", "contents": contents},
    }
    return [M.flex("附近診所", bubble)]


def _distance_prefix(h: dict[str, Any]) -> str:
    return f"{format_distance(h['distance_km'])}　" if h.get("distance_km") is not None else ""


def _tel(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return f"tel:{digits or phone}"
