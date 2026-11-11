"""處方箋照片儲存後端：本機磁碟與 Azure Blob（用假的 container client，不連線）。"""

from __future__ import annotations

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from app.core.exceptions import ExternalServiceError
from app.integrations.media_storage import (
    AzureBlobMediaStorage,
    LocalMediaStorage,
    build_media_storage,
)


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class FakeContainerClient:
    """只模擬用到的三個方法；`exists=False` 模擬 container 還沒建立。"""

    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.blobs: dict[str, bytes] = {}
        self.error: Exception | None = None

    def upload_blob(self, name: str, data: bytes, overwrite: bool = False) -> None:
        if self.error:
            raise self.error
        if not self.exists:
            raise ResourceNotFoundError("container not found")
        self.blobs[name] = data

    def create_container(self) -> None:
        self.exists = True

    def download_blob(self, name: str) -> FakeDownload:
        if self.error:
            raise self.error
        if name not in self.blobs:
            raise ResourceNotFoundError("blob not found")
        return FakeDownload(self.blobs[name])


def test_local_storage_roundtrip_and_legacy_path(tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    storage.save("u1/p1.bin", b"abc")
    assert storage.load("u1/p1.bin") == b"abc"
    assert storage.load("u1/missing.bin") is None
    # 舊資料的 image_path 是完整路徑
    assert storage.load(str(tmp_path / "media" / "u1" / "p1.bin")) == b"abc"


def test_azure_storage_roundtrip_creates_container():
    client = FakeContainerClient(exists=False)
    storage = AzureBlobMediaStorage("", container_client=client)
    storage.save("u1/p1.bin", b"abc")
    assert client.exists and client.blobs == {"u1/p1.bin": b"abc"}
    assert storage.load("u1/p1.bin") == b"abc"
    assert storage.load("u1/missing.bin") is None


def test_azure_errors_become_external_service_error():
    client = FakeContainerClient()
    storage = AzureBlobMediaStorage("", container_client=client)
    client.error = HttpResponseError("boom")
    with pytest.raises(ExternalServiceError):
        storage.save("k", b"x")
    with pytest.raises(ExternalServiceError):
        storage.load("k")


def test_build_picks_backend_from_config(tmp_path):
    assert isinstance(build_media_storage({"MEDIA_ROOT": str(tmp_path)}), LocalMediaStorage)
    # 假的連線字串：只建立 client 物件，不會連線
    conn = "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=eA==;EndpointSuffix=core.windows.net"
    assert isinstance(
        build_media_storage({"AZURE_STORAGE_CONNECTION_STRING": conn}), AzureBlobMediaStorage
    )


def test_prescription_survives_storage_failure(container, make_user):
    """照片存不進去不影響辨識結果，只是沒有原圖。"""

    class BrokenStorage:
        def save(self, key: str, data: bytes) -> None:
            raise ExternalServiceError("照片儲存失敗")

        def load(self, key: str) -> bytes | None:
            return None

    container.media_storage = BrokenStorage()
    principal = container.users.to_principal(make_user())
    data = container.prescriptions.create_from_image(principal, b"img", "image/jpeg", None)
    assert data["has_image"] is False
