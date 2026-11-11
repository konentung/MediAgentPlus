"""LINE Messaging API 客戶端封裝（line-bot-sdk v3）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    Message,
    MessagingApi,
    MessagingApiBlob,
    PushMessageRequest,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    UserProfileResponse,
)

logger = logging.getLogger(__name__)


class LineMessagingClient:
    """所有對 LINE Messaging API 的呼叫都經過這裡，方便測試時替換成假物件。"""

    def __init__(self, channel_access_token: str) -> None:
        self._configuration = Configuration(access_token=channel_access_token)

    def reply(self, reply_token: str, messages: Sequence[Message]) -> None:
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=list(messages))
            )

    def push(self, to: str, messages: Sequence[Message]) -> None:
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=to, messages=list(messages))
            )

    def show_loading_animation(self, chat_id: str, seconds: int = 20) -> None:
        """在使用者的聊天室顯示「處理中」動畫（5–60 秒，收到回覆後自動消失）。"""
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=chat_id, loading_seconds=seconds)
            )

    def get_message_content(self, message_id: str) -> bytes:
        """下載使用者傳來的圖片 / 檔案內容。"""
        with ApiClient(self._configuration) as api_client:
            return bytes(MessagingApiBlob(api_client).get_message_content(message_id))

    def get_profile(self, user_id: str) -> UserProfileResponse:
        with ApiClient(self._configuration) as api_client:
            return MessagingApi(api_client).get_profile(user_id)
