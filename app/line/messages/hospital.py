"""找醫院訊息：入口、選縣市、急診即時看板、附近診所、分流建議推播（給 119 / 診所訂閱者）。"""

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
EVENT_TITLES = {
    "opened": "🔴 急診分流建議",
    "updated": "🟠 急診分流建議（更新）",
    "resolved": "🟢 急診壅塞解除",
    "test": "🧪 測試通知：急診分流建議",
}


def menu_messages(*, has_profile_address: bool, emergency: bool) -> list[Message]:
    lead = (
        "正在為你查詢最近的急救責任醫院。請分享你的位置，或選擇縣市，我會列出各醫院目前的急診壅塞狀況。"
        if emergency
        else "找醫院：分享位置或選擇縣市，我會列出該縣市急救責任醫院的急診即時狀況，"
        "並幫你找非急症可去的附近診所。"
    )
    items = [M.location_item("📍 分享位置")]
    if has_profile_address:
        items.append(M.postback_item("🏠 用基本資料住址", Intent.FIND_HOSPITAL, source="profile"))
    items.append(M.postback_item("🗺 選縣市", Intent.FIND_HOSPITAL, action="areas"))
    if emergency:
        items.insert(0, M.uri_item("🚨 撥打 119", "tel:119"))
    else:
        items.append(M.postback_item("找真人", Intent.HUMAN, reason="hospital"))
    text = f"{EMERGENCY_HINT}\n{lead}" if emergency else lead
    return [M.text(text, quick_replies=items)]


def area_picker_messages(*, emergency: bool) -> list[Message]:
    bubbles = []
    for label, codes in rules.AREA_GROUPS:
        buttons = [
            {
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {
                    "type": "postback",
                    "label": rules.AREA_NAMES[code],
                    "data": postback_data(
                        Intent.FIND_HOSPITAL,
                        action="area",
                        area=code,
                        **({"emergency": "1"} if emergency else {}),
                    ),
                    "displayText": f"看 {rules.AREA_NAMES[code]} 的急診狀況",
                },
            }
            for code in codes
        ]
        bubbles.append(
            {
                "type": "bubble",
                "size": "kilo",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": BRAND,
                    "contents": [
                        {
                            "type": "text",
                            "text": label,
                            "weight": "bold",
                            "color": "#FFFFFF",
                            "size": "sm",
                        }
                    ],
                },
                "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons},
            }
        )
    return [M.flex("選擇縣市", {"type": "carousel", "contents": bubbles})]


def unknown_location_messages() -> list[Message]:
    return [
        M.text(
            "我無法從這個位置判斷縣市，請再分享一次位置，或直接選擇縣市。",
            quick_replies=[
                M.location_item("📍 重新分享位置"),
                M.postback_item("🗺 選縣市", Intent.FIND_HOSPITAL, action="areas"),
            ],
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
                quick_replies=[
                    M.uri_item("急診看板", liff_url),
                    M.postback_item("找真人", Intent.HUMAN),
                ],
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


def advisory_messages(advisory: dict[str, Any], event: str, liff_url: str) -> list[Message]:
    """推播給訂閱者（119 勤指中心 / 診所）的分流建議。"""
    title = EVENT_TITLES.get(event, EVENT_TITLES["opened"])
    area = advisory.get("area_name") or advisory.get("area_code")
    if event == "resolved":
        body = f"{area} 重度級急救責任醫院的壅塞狀況已解除，恢復正常調度。"
    else:
        body = advisory.get("report") or ""
    bubble = M.button_bubble(
        title=f"{title}｜{area}",
        body=body[:1800],
        buttons=[{"type": "uri", "label": "開啟急診即時看板", "uri": liff_url}],
        color="#2F6F62" if event == "resolved" else ALERT,
    )
    return [M.flex(f"{title} {area}", bubble)]


def _distance_prefix(h: dict[str, Any]) -> str:
    return f"{format_distance(h['distance_km'])}　" if h.get("distance_km") is not None else ""


def _tel(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return f"tel:{digits or phone}"
