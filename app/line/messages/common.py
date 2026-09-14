"""共用訊息：緊急畫面、需綁定提示、轉真人、預設回覆。"""

from __future__ import annotations

from linebot.v3.messaging import Message

from app.domain.enums import Intent
from app.line.messages.builder import MessageBuilder as M

EMERGENCY_TEXT = "🚨 這可能是緊急狀況，請立即撥打 119。\n若你能移動，請前往最近的急救責任醫院。"
FALLBACK_TEXT = "我不太確定你的意思，可以從下方選單選擇服務，或輸入「找真人」由客服協助你。"
HANDOFF_TEXT = "已為你轉接真人客服，客服人員將盡快與你聯繫。"


def emergency_messages() -> list[Message]:
    return [
        M.text(
            EMERGENCY_TEXT,
            quick_replies=[
                M.uri_item("撥打 119", "tel:119"),
                M.postback_item("最近急救責任醫院", Intent.FIND_HOSPITAL, emergency="1"),
            ],
        )
    ]


def binding_required_messages(liff_url: str, feature: str) -> list[Message]:
    bubble = M.button_bubble(
        title="需先完成基本資料綁定",
        body=f"「{feature}」為個人化功能，需填寫基本資料並完成驗證後才能使用。",
        buttons=[{"type": "uri", "label": "前往填寫", "uri": liff_url}],
    )
    return [M.flex("需先完成基本資料綁定", bubble)]


def locked_messages() -> list[Message]:
    return [M.text("你的帳號目前需要由真人客服協助核對，請稍候客服聯繫。")]


def fallback_messages() -> list[Message]:
    return [M.text(FALLBACK_TEXT, quick_replies=[M.postback_item("找真人", Intent.HUMAN)])]


def handoff_messages() -> list[Message]:
    return [M.text(HANDOFF_TEXT)]
