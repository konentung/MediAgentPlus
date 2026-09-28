"""處方箋照片 → 結構化資料。提示詞與輸出模型在這裡，呼叫交給 `LlmClient`（帶圖片）。

模型只「抄寫處方上有的內容」，不做任何醫療判斷、不輸出病人個資。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from app.domain.reminder_rules import FREQUENCY_CODES, TIMING_CODES
from app.integrations.llm import LlmClient


class ExtractedItem(BaseModel):
    drug_name_raw: str = Field(description="處方上的藥名原文（含劑量規格，例如 Metformin 500mg）")
    drug_name_en: str | None = Field(default=None, description="英文學名或商品名，沒有則 null")
    dose_text: str | None = Field(default=None, description="每次用量原文，例如 1 tab、5 ml")
    frequency_code: str = Field(description=f"頻率代碼，只能是：{', '.join(FREQUENCY_CODES)}")
    timing: str | None = Field(
        default=None, description=f"服用時機：{', '.join(TIMING_CODES)} 或 null"
    )
    route: str | None = Field(default=None, description="途徑，例如 PO、外用；沒有則 null")
    duration_days: int | None = Field(default=None, description="這項藥的給藥天數")
    quantity: str | None = Field(default=None, description="總量原文，例如 56 tab")
    instructions: str | None = Field(default=None, description="其他醫囑原文")
    confidence: float = Field(default=1.0, ge=0, le=1, description="辨識信心 0–1")


class PrescriptionExtraction(BaseModel):
    is_prescription: bool = Field(description="這張圖是否為處方箋 / 藥袋 / 用藥明細")
    hospital_name: str | None = Field(default=None, description="開立院所名稱")
    issued_on: str | None = Field(default=None, description="開立日期 YYYY-MM-DD（民國年請換算）")
    days_supply: int | None = Field(default=None, description="整張處方的給藥天數")
    is_chronic_refill: bool = Field(default=False, description="是否為慢性病連續處方箋")
    refill_seq: int | None = Field(default=None, description="慢箋第幾次調劑")
    refill_total: int | None = Field(default=None, description="慢箋共可調劑幾次")
    items: list[ExtractedItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, description="看不清楚或不確定的地方")


PROMPT = f"""你是處方箋抄寫助手。
請把圖片中「處方箋 / 藥袋 / 用藥明細」上實際印出的內容，逐項抄寫成結構化資料。
規則：
1. 只抄寫圖片上有的內容，不要推測或補充任何藥名、劑量、頻率。
   看不清楚就降低 confidence 並寫進 warnings。
2. 絕對不要輸出病人姓名、身分證字號、病歷號、出生日期等個人資料。
3. frequency_code 只能是 {", ".join(FREQUENCY_CODES)}
   （BID→bid、每日三次→tid、睡前→hs、需要時→prn，其他寫 other）。
4. timing 只能是 {", ".join(TIMING_CODES)} 或 null（飯後→pc、飯前→ac）。
5. 日期一律轉成 YYYY-MM-DD，民國年加 1911。
6. 如果圖片不是處方箋或藥袋，is_prescription 設 false、items 留空。"""


class VisionClient(Protocol):
    model: str

    def extract_prescription(self, image: bytes, content_type: str) -> PrescriptionExtraction: ...


class LlmVisionClient:
    def __init__(self, llm: LlmClient) -> None:
        self._llm = llm

    @property
    def model(self) -> str:
        return self._llm.model

    def extract_prescription(self, image: bytes, content_type: str) -> PrescriptionExtraction:
        return self._llm.complete_structured(
            PROMPT, PrescriptionExtraction, images=[(image, content_type or "image/jpeg")]
        )
