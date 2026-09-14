"""訊息建構工具：集中處理 TextMessage / FlexMessage / QuickReply 的組裝。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
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
    def uri_item(label: str, uri: str) -> QuickReplyItem:
        return QuickReplyItem(action=URIAction(label=label, uri=uri))

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
