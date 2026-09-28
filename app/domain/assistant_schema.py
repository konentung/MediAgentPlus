"""AI 對話助理的決策格式（單一來源）。

模型只輸出這個結構，由 `AssistantHandler` 套用 LINE 模組回應。
模型負責「理解事件 + 依知識庫片段生成回答」，**不**直接決定訊息長什麼樣；
事件 → 模組的對應在 `app/line/handlers/intents/assistant.py`，文案在 `app/line/messages/`。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

Event = Literal[
    "emergency",  # 紅旗：立即撥 119
    "symptom",  # 身體不適 / 症狀諮詢
    "hospital",  # 找醫院 / 急診狀況 / 掛哪一科
    "medication",  # 藥物、處方、藥名查詢
    "pharmacy",  # 找藥局 / 領藥
    "reminder",  # 服藥提醒
    "discharge",  # 出院後照護、衛教
    "intake",  # 基本資料 / 綁定 / 驗證
    "human",  # 想找真人、客訴、機器人無法處理
    "faq",  # 一般問答（依知識庫回答即可）
    "smalltalk",  # 打招呼、閒聊
    "unknown",  # 聽不懂 / 超出範圍
]
Risk = Literal["none", "watch", "urgent", "emergency"]
Action = Literal[
    "call_119",
    "find_hospital",
    "find_clinic",  # 非急症：附近診所 / 地區醫院
    "share_location",
    "find_pharmacy",
    "drug_lookup",
    "take_prescription_photo",
    "reminder_menu",
    "discharge_menu",
    "intake_form",
    "human",
]

EVENTS: tuple[str, ...] = Event.__args__  # type: ignore[attr-defined]
ACTIONS: tuple[str, ...] = Action.__args__  # type: ignore[attr-defined]
MAX_ANSWER_CHARS = 400


class Slots(BaseModel):
    city: str | None = Field(default=None, description="使用者提到的縣市，例如 台北市")
    district: str | None = Field(default=None, description="鄉鎮市區，例如 大安區")
    drug_name: str | None = Field(default=None, description="提到的藥名（中或英）")
    symptom: str | None = Field(default=None, description="主要症狀摘要")
    department: str | None = Field(
        default=None, description="症狀諮詢時建議掛的科別，例如 家醫科（依知識庫片段判斷）"
    )
    hospital_name: str | None = Field(default=None, description="提到的醫院名稱")


class AssistantDecision(BaseModel):
    event: Event = Field(description="這則訊息屬於哪種事件")
    risk: Risk = Field(default="none", description="健康風險：emergency=有生命危險立即 119")
    answer: str = Field(
        default="",
        description=f"給使用者的回覆（繁體中文、{MAX_ANSWER_CHARS} 字內、口語、不做診斷）；"
        "可留空讓功能模組回應",
    )
    slots: Slots = Field(default_factory=Slots)
    actions: list[Action] = Field(
        default_factory=list, description="建議使用者的下一步（最多 4 個，會變成按鈕）"
    )
    citations: list[str] = Field(
        default_factory=list, description="回答依據的知識片段編號，例如 K1、K3；沒有依據留空"
    )
    needs_clarification: bool = Field(default=False, description="問題太模糊、需要再問一句")
    handoff_reason: str | None = Field(default=None, description="event=human 時的原因摘要")

    # 服務端依 citations 對回的片段（含出處與網址），不在模型輸出的 schema 內
    _sources: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    @property
    def sources(self) -> list[dict[str, Any]]:
        return self._sources

    def attach_sources(self, hits: list[dict[str, Any]]) -> None:
        """K1、K3 … → 對應的檢索片段；同一份文件只留一次。"""
        seen: set[str] = set()
        sources: list[dict[str, Any]] = []
        for label in self.citations:
            index = _citation_index(label)
            if index is None or index >= len(hits):
                continue
            hit = hits[index]
            if hit["document_id"] in seen:
                continue
            seen.add(hit["document_id"])
            sources.append(hit)
        self._sources = sources


def _citation_index(label: str) -> int | None:
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) - 1 if digits and int(digits) >= 1 else None


def decision_schema() -> dict:
    return AssistantDecision.model_json_schema()
