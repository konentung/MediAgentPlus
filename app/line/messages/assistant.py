"""AI 助理回覆的組裝：回答文字 + 參考來源（原文連結）+ 下一步 Quick Reply。

有引用片段時用 Flex：正文在上、來源按鈕在下（最多 3 個、開原文網址）；沒有就純文字。
"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message, QuickReply, QuickReplyItem

from app.domain.enums import Intent
from app.domain.retrieval import hit_label
from app.line.messages.builder import MessageBuilder as M

AI_FAILED_TEXT = "我暫時無法判斷你的問題，可以從下方選單選擇服務，或稍後再試一次。"
CLARIFY_TEXT = "可以再多說一點嗎？例如哪裡不舒服、持續多久，或你想找哪一種服務。"
DISCLAIMER = "以上整理自各醫院公開衛教資料，僅供參考，實際狀況請以醫師或原院所說明為準。"
MAX_SOURCES = 3
BRAND = "#2F6F62"


def action_items(actions: list[str], liff_url: str) -> list[QuickReplyItem]:
    """決策的 actions → Quick Reply；對應表在這裡，模型不需要知道 postback 格式。"""
    items: list[QuickReplyItem] = []
    for action in actions:
        if action == "call_119":
            items.append(M.uri_item("🚨 撥打 119", "tel:119"))
        elif action == "find_hospital":
            items.append(M.postback_item("🏥 找醫院", Intent.FIND_HOSPITAL))
        elif action == "find_clinic":
            items.append(M.postback_item("🏠 附近診所", Intent.FIND_HOSPITAL, action="clinics"))
        elif action == "share_location":
            items.append(M.location_item("📍 分享位置"))
        elif action == "find_pharmacy":
            items.append(
                M.postback_item("💊 找附近藥局", Intent.MEDICATION, action="locate_prompt")
            )
        elif action == "drug_lookup":
            items.append(M.postback_item("🔍 查藥名", Intent.MEDICATION, action="drug"))
        elif action == "take_prescription_photo":
            items.append(M.postback_item("📷 拍處方箋", Intent.MEDICATION, action="photo"))
        elif action == "reminder_menu":
            items.append(M.postback_item("⏰ 服藥提醒", Intent.REMINDER))
        elif action == "discharge_menu":
            items.append(M.postback_item("🏠 出院衛教", Intent.DISCHARGE))
        elif action == "intake_form":
            items.append(M.uri_item("📝 填寫基本資料", liff_url))
        elif action == "feedback":
            items.append(M.postback_item("💬 意見回饋", Intent.FEEDBACK))
    return items[:13]


def answer_messages(
    answer: str, actions: list[str], liff_url: str, sources: list[dict[str, Any]] | None = None
) -> list[Message]:
    quick = action_items(actions, liff_url) or None
    text = answer or CLARIFY_TEXT
    links = [s for s in (sources or []) if (s.get("source") or "").startswith("http")]
    if not links:
        return [M.text(text, quick_replies=quick)]
    message = M.flex(text[:100], _answer_bubble(text, links[:MAX_SOURCES]))
    if quick:
        message.quick_reply = QuickReply(items=quick)
    return [message]


def _answer_bubble(text: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    buttons = [
        {
            "type": "button",
            "style": "link",
            "height": "sm",
            "action": {"type": "uri", "label": hit_label(hit)[:40], "uri": hit["source"]},
        }
        for hit in sources
    ]
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": text, "wrap": True, "size": "sm"},
                {
                    "type": "text",
                    "text": DISCLAIMER,
                    "wrap": True,
                    "size": "xxs",
                    "color": "#999999",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "none",
            "contents": [
                {"type": "text", "text": "參考來源（點開看原文）", "size": "xs", "color": BRAND},
                *buttons,
            ],
        },
    }


def failed_messages() -> list[Message]:
    return [M.text(AI_FAILED_TEXT, quick_replies=[M.postback_item("主選單", Intent.MENU)])]
