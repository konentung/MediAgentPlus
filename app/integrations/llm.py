"""OpenAI 客戶端：結構化輸出（對話助理、處方辨識）與文字向量（知識庫檢索）。

- `complete_structured(prompt, response_model, system=, images=)`：
  Chat Completions structured outputs，直接回 pydantic 實例。
- `embed(texts)`：Embeddings API，回每段文字的向量（`EMBEDDING_MODEL`，留空則停用語意檢索）。
設定：`OPENAI_API_KEY`、`AI_MODEL`（文字）、`OCR_MODEL`（看圖）、`EMBEDDING_MODEL`。
測試以 `tests/conftest.py` 的 `FakeLlmClient` / `FakeVisionClient` 替換容器內的物件。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Image = tuple[bytes, str]  # (bytes, content_type)

DEFAULT_TEXT_MODEL = "gpt-4.1-mini"
DEFAULT_VISION_MODEL = "gpt-4.1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBED_BATCH_SIZE = 100
FAILURE_TEXT = "AI 服務暫時無法使用，請稍後再試"


class LlmClient(Protocol):
    model: str
    embedding_model: str | None

    def complete_structured(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system: str = "",
        images: Sequence[Image] = (),
    ) -> T: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_output(response_model: type[T], raw: Any) -> T:
    try:
        return response_model.model_validate(raw)
    except ValidationError as exc:
        logger.error("LLM output failed validation: %s", str(exc)[:300])
        raise ExternalServiceError(f"{FAILURE_TEXT}（回傳格式錯誤）") from exc


class DisabledLlmClient:
    """未設定 `OPENAI_API_KEY` 時的替身（新增）：任何呼叫都回 502，
    讓對話助理退回關鍵字比對、處方辨識回失敗訊息。"""

    def __init__(self, model: str) -> None:
        self.model = model
        self.embedding_model: str | None = None

    def complete_structured(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system: str = "",
        images: Sequence[Image] = (),
    ) -> T:
        raise ExternalServiceError(f"{FAILURE_TEXT}（未設定 OPENAI_API_KEY）")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return []


class OpenAiClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: int = 60,
        *,
        embedding_model: str | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError("需要設定 OPENAI_API_KEY")
        self.model = model
        self.embedding_model = embedding_model or None
        self._api_key = api_key
        self._timeout = timeout
        self._client = None

    def _sdk(self):
        if self._client is None:
            import openai

            self._client = openai.OpenAI(api_key=self._api_key, timeout=self._timeout)
        return self._client

    def complete_structured(
        self,
        prompt: str,
        response_model: type[T],
        *,
        system: str = "",
        images: Sequence[Image] = (),
    ) -> T:
        content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{ct or 'image/jpeg'};base64,"
                    f"{base64.standard_b64encode(data).decode()}"
                },
            }
            for data, ct in images
        ]
        content.append({"type": "text", "text": prompt})
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        try:
            completion = self._sdk().chat.completions.parse(
                model=self.model, messages=messages, response_format=response_model
            )
        except Exception as exc:  # noqa: BLE001 - SDK 例外種類多，統一轉成 502
            logger.exception("OpenAI request failed")
            raise ExternalServiceError(FAILURE_TEXT) from exc
        choice = completion.choices[0] if completion.choices else None
        message = getattr(choice, "message", None)
        if message is None or getattr(message, "refusal", None) or message.parsed is None:
            logger.error("OpenAI returned no parsed output: %s", getattr(message, "refusal", ""))
            raise ExternalServiceError(f"{FAILURE_TEXT}（模型未回傳結構化結果）")
        return message.parsed

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """回傳與 `texts` 同順序的向量；未設定 embedding 模型時回空清單。"""
        if not self.embedding_model or not texts:
            return []
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), EMBED_BATCH_SIZE):
                batch = [t[:8000] for t in texts[start : start + EMBED_BATCH_SIZE]]
                response = self._sdk().embeddings.create(model=self.embedding_model, input=batch)
                vectors.extend(
                    item.embedding for item in sorted(response.data, key=lambda d: d.index)
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenAI embedding request failed")
            raise ExternalServiceError(f"{FAILURE_TEXT}（向量計算失敗）") from exc
        return vectors
