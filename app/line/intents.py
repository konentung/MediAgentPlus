"""Postback / 文字 → Intent 解析。

Postback data 統一採 querystring 格式：`intent=discharge&step=x`，
方便在 Rich Menu、Quick Reply、Flex 按鈕間共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode

from app.domain.enums import Intent

# Rich Menu / 文字關鍵字對照（服務地圖：主選單常駐 6 格）
TEXT_KEYWORDS: dict[str, Intent] = {
    "我不舒服": Intent.SYMPTOM,
    "找醫院": Intent.FIND_HOSPITAL,
    "我剛出院": Intent.DISCHARGE,
    "去哪拿藥": Intent.MEDICATION,
    "找藥局": Intent.MEDICATION,
    "服藥提醒": Intent.REMINDER,
    "找真人": Intent.HUMAN,
    "真人客服": Intent.HUMAN,
    "設定": Intent.SETTINGS,
}

# 紅旗關鍵字（命中即導向緊急畫面；正式版應交由病況諮詢模組的分級邏輯）
EMERGENCY_KEYWORDS: tuple[str, ...] = ("胸痛", "呼吸困難", "昏迷", "大量出血", "中風", "自殺")


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
        if any(keyword in normalized for keyword in EMERGENCY_KEYWORDS):
            return cls(Intent.EMERGENCY, {"text": normalized})
        intent = TEXT_KEYWORDS.get(normalized)
        return cls(intent, {"text": normalized}) if intent else None


def postback_data(intent: Intent, **params: str) -> str:
    """建立 postback data 字串。"""
    return urlencode({"intent": intent.value, **params})
