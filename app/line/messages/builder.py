"""訊息建構工具：集中處理 TextMessage / FlexMessage / QuickReply 的組裝。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from linebot.v3.messaging import (
    CameraAction,
    CameraRollAction,
    FlexContainer,
    FlexMessage,
    LocationAction,
    LocationMessage,
    MessageAction,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TextMessage,
    URIAction,
)

from app.domain.enums import Intent
from app.line.intents import postback_data


class MessageBuilder:
    @staticmethod
    def text(text: str, quick_replies: Sequence[QuickReplyItem] | None = None) -> TextMessage:
        return TextMessage(
            text=text, quick_reply=QuickReply(items=list(quick_replies)) if quick_replies else None
        )

    @staticmethod
    def flex(alt_text: str, contents: dict[str, Any]) -> FlexMessage:
        return FlexMessage(alt_text=alt_text, contents=FlexContainer.from_dict(contents))

    @staticmethod
    def postback_item(
        label: str, intent: Intent, display_text: str | None = None, **params: str
    ) -> QuickReplyItem:
        return QuickReplyItem(
            action=PostbackAction(
                label=label,
                data=postback_data(intent, **params),
                display_text=display_text or label,
            )
        )

    @staticmethod
    def message_item(label: str, text: str | None = None) -> QuickReplyItem:
        """按下後以使用者身分送出文字（走一般文字訊息的分派流程）。"""  # 新增
        return QuickReplyItem(action=MessageAction(label=label, text=text or label))

    @staticmethod
    def uri_item(label: str, uri: str) -> QuickReplyItem:
        return QuickReplyItem(action=URIAction(label=label, uri=uri))

    @staticmethod
    def camera_item(label: str = "📷 拍照") -> QuickReplyItem:
        """LINE 內建相機（只在手機版 LINE 顯示）。"""
        return QuickReplyItem(action=CameraAction(label=label))

    @staticmethod
    def camera_roll_item(label: str = "🖼 從相簿選") -> QuickReplyItem:
        return QuickReplyItem(action=CameraRollAction(label=label))

    @staticmethod
    def location_item(label: str = "📍 分享位置") -> QuickReplyItem:
        """LINE 內建位置分享，不需要任何地圖服務金鑰。"""
        return QuickReplyItem(action=LocationAction(label=label))

    @staticmethod
    def location(title: str, address: str, latitude: float, longitude: float) -> LocationMessage:
        return LocationMessage(
            title=title[:100], address=address[:100], latitude=latitude, longitude=longitude
        )

    @staticmethod
    def button_bubble(
        title: str, body: str, buttons: list[dict[str, Any]], color: str = "#2F6F62"
    ) -> dict[str, Any]:
        """簡易 Flex bubble：標題 + 內文 + 多個按鈕（action dict）。"""
        return {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": color,
                "contents": [
                    {
                        "type": "text",
                        "text": title,
                        "weight": "bold",
                        "color": "#FFFFFF",
                        "size": "md",
                    }
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [{"type": "text", "text": body, "wrap": True, "size": "sm"}],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "button", "style": "primary", "color": color, "action": action}
                    for action in buttons
                ],
            },
        }
