"""
Postback / 文字 → Intent 解析
Postback data 統一採 querystring 格式：`intent=discharge&step=x`
方便在 Rich Menu、Quick Reply、Flex 按鈕間共用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode

from app.domain.enums import Intent

TEXT_KEYWORDS: dict[str, Intent] = {
    "主選單": Intent.MENU,
    "病況諮詢": Intent.SYMPTOM,
    "找醫院": Intent.FIND_HOSPITAL,
    "衛教諮詢": Intent.DISCHARGE,
    "找藥局": Intent.MEDICATION,
    "服藥小幫手": Intent.REMINDER,
    "問題回報": Intent.FEEDBACK,
    "設定": Intent.SETTINGS,
    "基本資料": Intent.INTAKE,
}


@dataclass(frozen=True)
class IntentRequest:
    intent: Intent
    params: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_postback(cls, data: str) -> IntentRequest | None:
        parsed = {k: v[0] for k, v in parse_qs(data).items()}
        raw = parsed.pop("intent", None)
        if raw is None:
            return None
        try:
            return cls(Intent(raw), parsed)
        except ValueError:
            return None

    @classmethod
    def from_text(cls, text: str) -> IntentRequest | None:
        normalized = text.strip()
        intent = TEXT_KEYWORDS.get(normalized)
        return cls(intent, {"text": normalized}) if intent else None


def keyword_intent(text: str) -> Intent | None:
    """strip 後與 TEXT_KEYWORDS 完全相等才命中；不做包含比對，其餘交給 AI。"""
    return TEXT_KEYWORDS.get(text.strip())


def postback_data(intent: Intent, **params: str) -> str:
    """建立 postback data 字串。"""
    return urlencode({"intent": intent.value, **params})
