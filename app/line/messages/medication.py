"""藥物諮詢訊息：拍處方箋、辨識結果、找藥局、藥品查詢。"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message

from app.domain.enums import Intent
from app.domain.reminder_rules import FREQUENCY_LABELS, TIMING_LABELS
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M

DISCLAIMER = "以上依處方箋內容整理，用藥疑問請洽藥師或原院所。"
BRAND = "#2F6F62"


def menu_messages() -> list[Message]:
    return [
        M.text(
            "去哪拿藥：拍下處方箋，我會幫你整理藥品、找可領藥的健保特約藥局，也能一鍵建立服藥提醒。",
            quick_replies=[
                M.camera_item("📷 拍處方箋"),
                M.camera_roll_item("🖼 從相簿選"),
                M.postback_item("📍 找附近藥局", Intent.MEDICATION, action="locate_prompt"),
                M.postback_item("🔍 輸入藥名查詢", Intent.MEDICATION, action="drug"),
            ],
        )
    ]


def photo_prompt_messages() -> list[Message]:
    return [
        M.text(
            "請拍下整張處方箋或藥袋，文字清楚、不要反光。",
            quick_replies=[M.camera_item("📷 拍處方箋"), M.camera_roll_item("🖼 從相簿選")],
        )
    ]


def image_ask_messages(message_id: str) -> list[Message]:
    """使用者沒按選單就直接傳圖：先問是不是處方箋，不要每張圖都送去辨識。"""
    return [
        M.text(
            "收到圖片。這是處方箋或藥袋嗎？我可以幫你辨識藥品並建立提醒。",
            quick_replies=[
                M.postback_item(
                    "是，幫我辨識", Intent.MEDICATION, action="image", message_id=message_id
                ),
                M.postback_item("不是", Intent.MEDICATION, action="ignore_image"),
            ],
        )
    ]


def not_prescription_messages() -> list[Message]:
    return [
        M.text(
            "這張圖看起來不是處方箋或藥袋。請拍下整張處方箋再試一次。",
            quick_replies=[M.camera_item("📷 重拍"), M.camera_roll_item("🖼 從相簿選")],
        )
    ]


def ocr_failed_messages(reason: str) -> list[Message]:
    return [
        M.text(
            f"{reason}\n你也可以直接輸入藥名查詢。",
            quick_replies=[
                M.camera_item("📷 重拍"),
                M.postback_item("輸入藥名", Intent.MEDICATION, action="drug"),
            ],
        )
    ]


def prescription_result_messages(prescription: dict[str, Any], edit_url: str) -> list[Message]:
    items = prescription.get("items") or []
    header_lines = []
    if prescription.get("hospital_name"):
        header_lines.append(prescription["hospital_name"])
    if prescription.get("issued_on"):
        header_lines.append(f"開立日 {prescription['issued_on']}")
    if prescription.get("days_supply"):
        header_lines.append(f"給藥 {prescription['days_supply']} 天")
    if prescription.get("is_chronic_refill"):
        seq, total = prescription.get("refill_seq"), prescription.get("refill_total")
        header_lines.append(f"慢箋 第 {seq or '?'}/{total or '?'} 次")

    contents: list[dict[str, Any]] = [
        {"type": "text", "text": "辨識結果，請確認是否正確", "weight": "bold", "size": "md"},
    ]
    if header_lines:
        contents.append(
            {
                "type": "text",
                "text": "　".join(header_lines),
                "size": "xs",
                "color": "#888888",
                "wrap": True,
            }
        )
    for index, item in enumerate(items[:10], start=1):
        detail = "　".join(
            part
            for part in (
                item.get("dose_text"),
                FREQUENCY_LABELS.get(item.get("frequency_code") or "other"),
                TIMING_LABELS.get(item.get("timing") or ""),
                f"{item['duration_days']} 天" if item.get("duration_days") else None,
            )
            if part
        )
        category = f"（{item['category']}）" if item.get("category") else ""
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{index}. {item['drug_name']}{category}",
                        "size": "sm",
                        "weight": "bold",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": detail or "（用法未辨識）",
                        "size": "xs",
                        "color": "#666666",
                        "wrap": True,
                    },
                ],
            }
        )
    if len(items) > 10:
        contents.append(
            {"type": "text", "text": f"…另有 {len(items) - 10} 項，請到編輯頁查看", "size": "xs"}
        )
    for warning in (prescription.get("warnings") or [])[:3]:
        contents.append(
            {
                "type": "text",
                "text": f"⚠ {warning}",
                "size": "xs",
                "color": "#B45309",
                "wrap": True,
                "margin": "sm",
            }
        )
    contents.append(
        {
            "type": "text",
            "text": DISCLAIMER,
            "size": "xxs",
            "color": "#999999",
            "wrap": True,
            "margin": "lg",
        }
    )
    pid = prescription["id"]
    bubble = {
        "type": "bubble",
        "size": "mega",
        "body": {"type": "box", "layout": "vertical", "contents": contents},
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BRAND,
                    "action": {
                        "type": "postback",
                        "label": "✅ 正確，找可領藥的藥局",
                        "data": postback_data(
                            Intent.MEDICATION, action="confirm", prescription_id=pid
                        ),
                        "displayText": "正確，找藥局",
                    },
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": BRAND,
                    "action": {
                        "type": "postback",
                        "label": "⏰ 正確，建立服藥提醒",
                        "data": postback_data(
                            Intent.REMINDER, action="create", prescription_id=pid
                        ),
                        "displayText": "正確，建立服藥提醒",
                    },
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {"type": "uri", "label": "✏️ 有誤，修改內容", "uri": edit_url},
                },
            ],
        },
    }
    return [M.flex("處方辨識結果", bubble)]


def location_prompt_messages(has_profile_address: bool) -> list[Message]:
    quick = [M.location_item("📍 分享目前位置")]
    if has_profile_address:
        quick.append(
            M.postback_item("🏠 用我填的住址", Intent.MEDICATION, action="locate", source="profile")
        )
    return [
        M.text("要找哪附近的藥局？分享你的位置，我會列出同區的健保特約藥局。", quick_replies=quick)
    ]


def location_ask_messages() -> list[Message]:
    return [
        M.text(
            "收到位置。要找什麼？",
            quick_replies=[
                M.postback_item("找附近藥局", Intent.MEDICATION, action="locate", source="state"),
                M.postback_item("找醫院", Intent.FIND_HOSPITAL, source="state"),
            ],
        )
    ]


def pharmacy_result_messages(result: dict[str, Any], note: str | None = None) -> list[Message]:
    pharmacies = result.get("pharmacies") or []
    if not result.get("city"):
        return [
            M.text(
                "我無法從這個位置判斷縣市，請再分享一次位置，或輸入地址（例如：台北市松山區）。",
                quick_replies=[M.location_item("📍 重新分享位置")],
            )
        ]
    area = f"{result['city']}{result.get('district') or ''}"
    if not pharmacies:
        return [
            M.text(
                f"{area} 附近查無健保特約藥局資料，建議回原院所藥局領藥或撥打 1957 諮詢。",
                quick_replies=[M.location_item("📍 重新分享位置")],
            )
        ]
    bubbles = [_pharmacy_bubble(p) for p in pharmacies[:5]]
    scope_note = (
        ""
        if result.get("scope") == "district"
        else f"（{result.get('district') or ''}沒有資料，改列同縣市）"
    )
    messages: list[Message] = [
        M.text(
            f"{area} 的健保特約藥局{scope_note}，持處方箋即可調劑：" + (f"\n{note}" if note else "")
        )
    ]
    messages.append(M.flex(f"{area} 藥局", {"type": "carousel", "contents": bubbles}))
    return messages


def _pharmacy_bubble(p: dict[str, Any]) -> dict[str, Any]:
    open_text = (
        "🟢 現在營業中"
        if p.get("open_now")
        else "⚪ 目前非營業時段"
        if p.get("open_now") is False
        else "營業時間請電洽"
    )
    today = "、".join(p.get("today_hours") or []) or "—"
    buttons: list[dict[str, Any]] = []
    if p.get("address"):
        maps = "https://www.google.com/maps/search/?api=1&query=" + _url_quote(p["address"])
        buttons.append(
            {
                "type": "button",
                "style": "primary",
                "color": BRAND,
                "height": "sm",
                "action": {"type": "uri", "label": "地圖導航", "uri": maps},
            }
        )
    if p.get("phone"):
        tel = "".join(ch for ch in p["phone"] if ch.isdigit() or ch == "+")
        if tel:
            buttons.append(
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "action": {"type": "uri", "label": "撥打電話", "uri": f"tel:{tel}"},
                }
            )
    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": p["name"], "weight": "bold", "size": "md", "wrap": True},
                {
                    "type": "text",
                    "text": p.get("address") or "—",
                    "size": "xs",
                    "color": "#666666",
                    "wrap": True,
                    "margin": "sm",
                },
                {"type": "text", "text": open_text, "size": "xs", "margin": "sm"},
                {"type": "text", "text": f"今日時段：{today}", "size": "xs", "color": "#888888"},
            ],
        },
    }
    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons,
        }
    return bubble


def _url_quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def drug_prompt_messages() -> list[Message]:
    return [M.text("請輸入藥品名稱（中文或英文，例如 Metformin 或 普拿疼），我會查健保用藥資料。")]


def drug_lookup_messages(query: str, drugs: list[dict[str, Any]]) -> list[Message]:
    if not drugs:
        return [
            M.text(
                f"查無「{query}」的資料。可以試試英文學名或商品名。",
                quick_replies=[M.postback_item("再查一次", Intent.MEDICATION, action="drug")],
            )
        ]
    lines = []
    for d in drugs[:5]:
        parts = [d["name_en"]]
        if d.get("name_zh"):
            parts.append(d["name_zh"])
        detail = "　".join(x for x in (d.get("dosage_form"), d.get("category")) if x)
        lines.append(f"• {' / '.join(parts)}" + (f"\n　{detail}" if detail else ""))
    text = f"「{query}」查詢結果（健保用藥資料）：\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"
    return [
        M.text(
            text,
            quick_replies=[
                M.postback_item("再查一種", Intent.MEDICATION, action="drug"),
                M.postback_item("找附近藥局", Intent.MEDICATION, action="locate_prompt"),
            ],
        )
    ]
