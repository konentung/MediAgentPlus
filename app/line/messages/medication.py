"""藥物諮詢訊息：拍處方箋、辨識結果、找藥局、藥品查詢。"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message

from app.domain.enums import Intent
from app.domain.reminder_rules import FREQUENCY_LABELS, TIMING_LABELS
from app.line.intents import postback_data
from app.line.messages import theme
from app.line.messages.builder import MessageBuilder as M

DISCLAIMER = "以上依處方箋內容整理，用藥疑問請洽藥師或原院所。"


def _frequency_text(item: dict[str, Any]) -> str:
    """頻率顯示：認得出代碼就用中文標籤，認不出來（other）但有抄到原文就直接顯示原文。"""
    code = item.get("frequency_code") or "other"
    if code == "other" and item.get("frequency_raw"):
        return str(item["frequency_raw"])
    return FREQUENCY_LABELS.get(code, FREQUENCY_LABELS["other"])


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

    contents: list[dict[str, Any]] = []
    for index, item in enumerate(items[:10], start=1):
        detail = "　".join(
            part
            for part in (
                item.get("dose_text"),
                _frequency_text(item),
                TIMING_LABELS.get(item.get("timing") or ""),
                f"{item['duration_days']} 天" if item.get("duration_days") else None,
            )
            if part
        )
        category = f"（{item['category']}）" if item.get("category") else ""
        contents.extend(
            theme.row(
                [
                    theme.text(f"{index}. {item['drug_name']}{category}", bold=True),
                    theme.secondary_text(detail or "（用法未辨識）"),
                ],
                first=index == 1,
            )
        )
    if len(items) > 10:
        contents.append(
            theme.secondary_text(f"…另有 {len(items) - 10} 項，請到編輯頁查看", margin="md")
        )
    for warning in (prescription.get("warnings") or [])[:3]:
        contents.append(theme.text(f"⚠ {warning}", size="xs", color=theme.RED_STRONG, margin="sm"))
    pid = prescription["id"]
    bubble = theme.bubble(
        contents,
        header=theme.header("辨識結果，請確認是否正確", "　".join(header_lines) or None),
        footer=[
            theme.primary_button(
                {
                    "type": "postback",
                    "label": "✅ 正確，找可領藥的藥局",
                    "data": postback_data(Intent.MEDICATION, action="confirm", prescription_id=pid),
                    "displayText": "正確，找藥局",
                },
                height="md",
            ),
            theme.primary_button(
                {
                    "type": "postback",
                    "label": "⏰ 正確，建立服藥提醒",
                    "data": postback_data(Intent.REMINDER, action="create", prescription_id=pid),
                    "displayText": "正確，建立服藥提醒",
                },
                height="md",
            ),
            theme.secondary_button(
                {"type": "uri", "label": "✏️ 有誤，修改內容", "uri": edit_url}, height="md"
            ),
        ],
        size="mega",
    )
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


def pharmacy_result_messages(
    result: dict[str, Any], note: str | None = None, prescription_id: str | None = None
) -> list[Message]:
    """`prescription_id` 有值（從已確認的處方找藥局）時，每家藥局多一顆「我要去這裡領藥」。"""
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
    bubbles = [_pharmacy_bubble(p, prescription_id) for p in pharmacies[:5]]
    scope_note = (
        ""
        if result.get("scope") == "district"
        else f"（{result.get('district') or ''}沒有資料，改列同縣市）"
    )
    messages: list[Message] = [
        M.text(
            f"{area} 的健保特約藥局{scope_note}，持處方箋即可調劑"
            + ("，選一家按「我要去這裡領藥」我會幫你記下：" if prescription_id else "：")
            + (f"\n{note}" if note else "")
        )
    ]
    messages.append(M.flex(f"{area} 藥局", {"type": "carousel", "contents": bubbles}))
    return messages


def pickup_requested_messages(pickup: dict[str, Any]) -> list[Message]:
    """建立領藥需求後的回覆：藥局有人員帳號會備藥並推播；沒有的只記下選擇、提醒直接前往。"""
    if pickup.get("staffed"):
        lines = [
            f"已通知 {pickup['pharmacy_name']} 先幫你備藥，領藥編號 {pickup['code']}。",
            "藥備好後我會再通知你，領藥時請攜帶處方箋正本與健保卡。",
        ]
    else:
        lines = [
            f"已記下你要到 {pickup['pharmacy_name']} 領藥，領藥編號 {pickup['code']}。",
            "這家藥局還沒有加入線上備藥服務，請直接前往並出示處方箋正本與健保卡；"
            "建議先電話確認藥品有無庫存。",
        ]
    if pickup.get("pharmacy_address"):
        lines.append(f"地址：{pickup['pharmacy_address']}")
    if pickup.get("pharmacy_phone"):
        lines.append(f"藥局電話：{pickup['pharmacy_phone']}")
    return [M.text("\n".join(lines))]


def _pharmacy_bubble(p: dict[str, Any], prescription_id: str | None = None) -> dict[str, Any]:
    open_text = (
        "🟢 現在營業中"
        if p.get("open_now")
        else "⚪ 目前非營業時段"
        if p.get("open_now") is False
        else "營業時間請電洽"
    )
    today = "、".join(p.get("today_hours") or []) or "—"
    buttons: list[dict[str, Any]] = []
    if prescription_id:
        buttons.append(
            theme.primary_button(
                {
                    "type": "postback",
                    "label": "我要去這裡領藥",
                    "data": postback_data(
                        Intent.MEDICATION,
                        action="pickup",
                        prescription_id=prescription_id,
                        pharmacy_id=p["id"],
                    ),
                    "displayText": f"我要去 {p['name']} 領藥",
                }
            )
        )
    if p.get("address"):
        maps = "https://www.google.com/maps/search/?api=1&query=" + _url_quote(p["address"])
        # 一張卡只留一顆主要按鈕：有「我要去這裡領藥」時地圖導航降為次要
        style = theme.secondary_button if buttons else theme.primary_button
        buttons.append(style({"type": "uri", "label": "地圖導航", "uri": maps}))
    if p.get("phone"):
        tel = "".join(ch for ch in p["phone"] if ch.isdigit() or ch == "+")
        if tel:
            buttons.append(
                theme.secondary_button({"type": "uri", "label": "撥打電話", "uri": f"tel:{tel}"})
            )
    # 營業狀態放品牌橫條，藥局名稱在內文第一列
    return theme.bubble(
        [
            theme.text(p["name"], size="md", bold=True),
            theme.secondary_text(p.get("address") or "—", margin="sm"),
            theme.secondary_text(f"今日時段：{today}", margin="sm"),
        ],
        header=theme.brand_strip(open_text),
        footer=buttons or None,
        size="kilo",
    )


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
