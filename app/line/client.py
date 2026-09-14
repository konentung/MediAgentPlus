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
    RichMenuRequest,
    UserProfileResponse,
)

logger = logging.getLogger(__name__)


class LineMessagingClient:
    """所有對 LINE Messaging API 的呼叫都經過這裡，方便測試時替換成假物件。"""

    def __init__(self, channel_access_token: str) -> None:
        self._configuration = Configuration(access_token=channel_access_token)

    # ---- 訊息 ----
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

    def get_profile(self, user_id: str) -> UserProfileResponse:
        with ApiClient(self._configuration) as api_client:
            return MessagingApi(api_client).get_profile(user_id)

    # ---- Rich Menu ----
    def create_rich_menu(
        self, request: RichMenuRequest, image_bytes: bytes, content_type: str = "image/png"
    ) -> str:
        with ApiClient(self._configuration) as api_client:
            rich_menu_id = MessagingApi(api_client).create_rich_menu(request).rich_menu_id
            MessagingApiBlob(api_client).set_rich_menu_image(
                rich_menu_id, body=image_bytes, _headers={"Content-Type": content_type}
            )
        return rich_menu_id

    def set_default_rich_menu(self, rich_menu_id: str) -> None:
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).set_default_rich_menu(rich_menu_id)

    def link_rich_menu_to_user(self, user_id: str, rich_menu_id: str) -> None:
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).link_rich_menu_id_to_user(user_id, rich_menu_id)

    def list_rich_menus(self) -> list[str]:
        with ApiClient(self._configuration) as api_client:
            return [m.rich_menu_id for m in MessagingApi(api_client).get_rich_menu_list().richmenus]

    def delete_rich_menu(self, rich_menu_id: str) -> None:
        with ApiClient(self._configuration) as api_client:
            MessagingApi(api_client).delete_rich_menu(rich_menu_id)
