"""對外 Webhook 推送（分流建議 → 119 勤務指揮中心 / 診所自家系統）。

POST JSON，並以訂閱的 secret 做 HMAC-SHA256 簽章放在 `X-MediAgent-Signature`，
接收端可驗證來源。測試以 `FakeWebhookClient` 替換 `container.webhook_client`。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Protocol

import requests

from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-MediAgent-Signature"
EVENT_HEADER = "X-MediAgent-Event"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookClient(Protocol):
    def post(self, url: str, payload: dict[str, Any], *, secret: str, event: str) -> None: ...


class HttpWebhookClient:
    def __init__(self, timeout_seconds: int = 10) -> None:
        self._timeout = timeout_seconds

    def post(self, url: str, payload: dict[str, Any], *, secret: str, event: str) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            EVENT_HEADER: event,
            SIGNATURE_HEADER: sign(secret, body),
        }
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Webhook delivery to %s failed: %s", url, exc)
            raise ExternalServiceError(f"Webhook 推送失敗：{exc}") from exc
