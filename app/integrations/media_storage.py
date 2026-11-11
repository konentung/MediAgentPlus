"""處方箋照片的儲存後端：本機磁碟（開發 / 測試）或 Azure Blob Storage（雲端）。

呼叫端傳進來的一律是**已加密**的 bytes（`FieldCipher`），這一層只負責放與取，不碰明文。
key 的格式是 `<user_id>/<prescription_id>.bin`，存在 `prescriptions.image_path`。
設了 `AZURE_STORAGE_CONNECTION_STRING` 就用 Azure Blob，否則存在 `MEDIA_ROOT`。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

DEFAULT_CONTAINER = "prescriptions"


class MediaStorage(Protocol):
    def save(self, key: str, data: bytes) -> None:
        """寫入（同 key 覆蓋）；失敗 raise `ExternalServiceError`。"""

    def load(self, key: str) -> bytes | None:
        """讀出內容；不存在回 None，其他失敗 raise `ExternalServiceError`。"""


class LocalMediaStorage:
    """存在本機磁碟 `MEDIA_ROOT/<key>`。雲端主機的磁碟不保證持久，正式環境請用 Azure Blob。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, key: str, data: bytes) -> None:
        path = self._root / key
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise ExternalServiceError("照片儲存失敗") from exc

    def load(self, key: str) -> bytes | None:
        # 舊資料的 image_path 存的是含 MEDIA_ROOT 的完整路徑，新資料只存 key，兩種都要讀得到
        for path in (self._root / key, Path(key)):
            if path.is_file():
                return path.read_bytes()
        return None


class AzureBlobMediaStorage:
    """存在 Azure Blob Storage 的單一 container（私有，不開放匿名讀取）。"""

    def __init__(
        self,
        connection_string: str,
        container: str = DEFAULT_CONTAINER,
        *,
        container_client: Any | None = None,
    ) -> None:
        if container_client is None:
            # SDK 只在真的用 Azure 時才載入，本機開發不需要連線
            from azure.storage.blob import BlobServiceClient

            service = BlobServiceClient.from_connection_string(connection_string)
            container_client = service.get_container_client(container)
        self._client = container_client

    def save(self, key: str, data: bytes) -> None:
        from azure.core.exceptions import AzureError, ResourceNotFoundError

        try:
            try:
                self._client.upload_blob(key, data, overwrite=True)
            except ResourceNotFoundError:
                # container 還沒建立：建好再傳一次（預設就是私有）
                self._client.create_container()
                self._client.upload_blob(key, data, overwrite=True)
        except AzureError as exc:
            raise ExternalServiceError("照片儲存失敗") from exc

    def load(self, key: str) -> bytes | None:
        from azure.core.exceptions import AzureError, ResourceNotFoundError

        try:
            return self._client.download_blob(key).readall()
        except ResourceNotFoundError:
            return None
        except AzureError as exc:
            raise ExternalServiceError("照片讀取失敗") from exc


def build_media_storage(cfg: Mapping[str, Any]) -> MediaStorage:
    """依設定選儲存後端：有 Azure 連線字串用 Blob，否則本機磁碟。"""
    connection_string = cfg.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        return AzureBlobMediaStorage(
            connection_string, cfg.get("AZURE_STORAGE_CONTAINER") or DEFAULT_CONTAINER
        )
    if cfg.get("ENV") == "production":
        logger.warning(
            "AZURE_STORAGE_CONNECTION_STRING 未設定：處方箋照片存在本機磁碟，重新部署後可能遺失"
        )
    return LocalMediaStorage(cfg.get("MEDIA_ROOT", "var/media"))
