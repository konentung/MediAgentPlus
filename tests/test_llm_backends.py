"""OpenAI 客戶端：結構化輸出解析、圖片 payload、錯誤轉換（假 SDK，不真的呼叫）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import ExternalServiceError
from app.domain.assistant_schema import AssistantDecision
from app.integrations.llm import DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL, OpenAiClient
from app.integrations.vision import LlmVisionClient, PrescriptionExtraction
from tests.conftest import TINY_PNG


class FakeCompletions:
    def __init__(self, parsed=None, refusal=None, error=None):
        self.parsed, self.refusal, self.error = parsed, refusal, error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(parsed=self.parsed, refusal=self.refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _client(completions) -> OpenAiClient:
    client = OpenAiClient("gpt-test", "sk-test")
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


def test_structured_text_call():
    decision = AssistantDecision(event="faq", answer="hi")
    completions = FakeCompletions(parsed=decision)
    result = _client(completions).complete_structured("問題", AssistantDecision, system="規則")
    assert result is decision
    call = completions.calls[0]
    assert call["model"] == "gpt-test" and call["response_format"] is AssistantDecision
    assert call["messages"][0] == {"role": "system", "content": "規則"}
    assert call["messages"][1]["content"] == [{"type": "text", "text": "問題"}]


def test_vision_client_sends_image_as_data_url():
    completions = FakeCompletions(parsed=PrescriptionExtraction(is_prescription=False))
    result = LlmVisionClient(_client(completions)).extract_prescription(TINY_PNG, "image/png")
    assert result.is_prescription is False
    content = completions.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1]["type"] == "text" and "處方箋" in content[1]["text"]


def test_errors_become_external_service_error():
    for completions in (
        FakeCompletions(error=RuntimeError("boom")),
        FakeCompletions(refusal="no"),
        FakeCompletions(parsed=None),
    ):
        with pytest.raises(ExternalServiceError):
            _client(completions).complete_structured("x", AssistantDecision)


def test_missing_api_key_fails_fast():
    with pytest.raises(RuntimeError):
        OpenAiClient(DEFAULT_TEXT_MODEL, "")


def test_container_builds_openai_clients_with_default_models():
    from app import create_app

    container = create_app("testing").extensions["container"]
    assert isinstance(container.llm_client, OpenAiClient)
    assert container.llm_client.model == DEFAULT_TEXT_MODEL
    assert container.vision_client.model == DEFAULT_VISION_MODEL
