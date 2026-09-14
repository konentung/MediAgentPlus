"""加入好友流程訊息：歡迎訊息 → 安全聲明（置頂）→ 資料使用說明 Flex。"""

from __future__ import annotations

from linebot.v3.messaging import Message

from app.domain.enums import Intent
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M

WELCOME_TEXT = (
    "嗨，我是 MediAgent+ 照護小幫手 👋\n"
    "我能幫你：病況諮詢、找醫院、出院衛教、用藥查詢與服藥提醒。\n"
    "請從下方選單選擇你需要的服務。"
)

SAFETY_TEXT = (
    "⚠️ 安全聲明\n"
    "• 若有緊急狀況請立即撥打 119。\n"
    "• 本服務提供健康資訊參考，不提供醫療診斷，亦不取代醫師判斷。"
)


def welcome_messages(liff_url: str) -> list[Message]:
    data_usage = M.button_bubble(
        title="資料使用說明",
        body="填寫基本資料並完成驗證後，可解鎖「個人化衛教」與「跨日服藥提醒」。\n資料僅用於本服務，你可隨時在設定頁解除綁定。",
        buttons=[
            {"type": "uri", "label": "填寫基本資料", "uri": liff_url},
            {
                "type": "postback",
                "label": "先逛逛就好",
                "data": postback_data(Intent.SETTINGS, action="skip"),
                "displayText": "先逛逛就好",
            },
        ],
    )
    return [M.text(WELCOME_TEXT), M.text(SAFETY_TEXT), M.flex("資料使用說明", data_usage)]
