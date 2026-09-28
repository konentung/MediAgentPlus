"""處方箋：拍照辨識 → 草稿 → 病人確認 / 修改 → 慢箋領藥提醒日。

照片以 `FieldCipher` 加密後完整保留在 `MEDIA_ROOT/<user_id>/<prescription_id>.bin`。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.domain.principal import Principal
from app.domain.reminder_rules import (
    FREQUENCY_CODES,
    TIMING_CODES,
    normalize_frequency,
    normalize_timing,
)
from app.integrations.vision import ExtractedItem, VisionClient
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.pharmacy_service import PharmacyService
from app.utils.crypto import FieldCipher

logger = logging.getLogger(__name__)

PUBLIC_EXCLUDE = ("ocr_raw", "image_path", "line_message_id", "user_id")
ITEM_TEXT_FIELDS: dict[str, int] = {
    "drug_name": 150,
    "drug_name_raw": 150,
    "dose_text": 50,
    "quantity": 30,
    "instructions": 200,
}
HEADER_TEXT_FIELDS: dict[str, int] = {"hospital_name": 100}
HEADER_INT_FIELDS = ("days_supply", "refill_seq", "refill_total")


class PrescriptionService:
    def __init__(
        self,
        prescriptions: PrescriptionRepository,
        pharmacies: PharmacyService,
        vision: Callable[[], VisionClient],
        cipher: FieldCipher,
        media_root: str | Path,
        refill_days_before: int = 3,
    ) -> None:
        self._prescriptions = prescriptions
        self._pharmacies = pharmacies
        self._vision = vision
        self._cipher = cipher
        self._media_root = Path(media_root)
        self._refill_days_before = refill_days_before

    # ---- 建立 ----

    def create_from_image(
        self, principal: Principal, image: bytes, content_type: str, line_message_id: str | None
    ) -> dict[str, Any]:
        vision = self._vision()
        extraction = vision.extract_prescription(image, content_type or "image/jpeg")
        if not extraction.is_prescription:
            raise ValidationError(
                "這張圖看起來不是處方箋或藥袋", details={"not_prescription": True}
            )
        prescription_id = uuid.uuid4().hex
        image_path = self._store_image(principal.user_id, prescription_id, image)
        items = [self._item_from_extraction(x) for x in extraction.items]
        row = self._prescriptions.create(
            id=prescription_id,
            user_id=principal.user_id,
            status="draft",
            source="ocr",
            hospital_name=(extraction.hospital_name or None) and extraction.hospital_name[:100],
            issued_on=_parse_date(extraction.issued_on),
            days_supply=extraction.days_supply,
            is_chronic_refill=bool(extraction.is_chronic_refill),
            refill_seq=extraction.refill_seq,
            refill_total=extraction.refill_total,
            items=items,
            warnings=list(extraction.warnings or []),
            image_path=str(image_path),
            image_content_type=content_type or "image/jpeg",
            line_message_id=line_message_id,
            ocr_model=getattr(vision, "model", None),
            ocr_raw=extraction.model_dump(),
        )
        return self.to_public(row)

    def _item_from_extraction(self, item: ExtractedItem) -> dict[str, Any]:
        match = self._pharmacies.match_drug(item.drug_name_en, item.drug_name_raw)
        return {
            "drug_name": (match["name_en"] if match else item.drug_name_en or item.drug_name_raw)[
                :150
            ],
            "drug_name_raw": item.drug_name_raw[:150],
            "dose_text": (item.dose_text or None) and item.dose_text[:50],
            "frequency_code": normalize_frequency(item.frequency_code),
            "timing": normalize_timing(item.timing),
            "duration_days": item.duration_days,
            "quantity": (item.quantity or None) and item.quantity[:30],
            "instructions": (item.instructions or None) and item.instructions[:200],
            "nhi_code": match["nhi_code"] if match else None,
            "category": match["category"] if match else None,
            "confidence": round(float(item.confidence), 2),
        }

    def _store_image(self, user_id: str, prescription_id: str, image: bytes) -> Path:
        folder = self._media_root / user_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{prescription_id}.bin"
        path.write_bytes(self._cipher.encrypt_bytes(image))
        return path

    # ---- 讀取 ----

    def get_own(self, principal: Principal, prescription_id: str) -> dict[str, Any]:
        row = self._prescriptions.get(prescription_id)
        if row is None or row["user_id"] != principal.user_id or row["status"] == "discarded":
            raise NotFoundError("找不到這張處方")
        return row

    def get_public(self, principal: Principal, prescription_id: str) -> dict[str, Any]:
        return self.to_public(self.get_own(principal, prescription_id))

    def list_own(self, principal: Principal) -> list[dict[str, Any]]:
        return [self.to_public(r) for r in self._prescriptions.list_for_user(principal.user_id)]

    def read_image(self, principal: Principal, prescription_id: str) -> tuple[bytes, str]:
        row = self.get_own(principal, prescription_id)
        if not row.get("image_path") or not Path(row["image_path"]).exists():
            raise NotFoundError("這張處方沒有照片")
        data = self._cipher.decrypt_bytes(Path(row["image_path"]).read_bytes())
        return data, row.get("image_content_type") or "image/jpeg"

    # ---- 修改 / 確認 ----

    def update(self, principal: Principal, prescription_id: str, body: dict[str, Any]) -> dict:
        row = self.get_own(principal, prescription_id)
        changes: dict[str, Any] = {}
        for key, max_length in HEADER_TEXT_FIELDS.items():
            if key in body:
                text = str(body[key] or "").strip()
                if len(text) > max_length:
                    raise ValidationError(f"{key} 不可超過 {max_length} 字")
                changes[key] = text or None
        for key in HEADER_INT_FIELDS:
            if key in body:
                changes[key] = _parse_int(body[key], key)
        if "issued_on" in body:
            changes["issued_on"] = _parse_date(body["issued_on"])
        if "is_chronic_refill" in body:
            changes["is_chronic_refill"] = bool(body["is_chronic_refill"])
        if "items" in body:
            changes["items"] = self.validate_items(body["items"])
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        merged = {**row, **changes}
        if merged["status"] == "confirmed":
            changes["refill_reminder_on"] = self.refill_reminder_date(merged)
        return self.to_public(self._prescriptions.update(prescription_id, **changes))

    def confirm(self, principal: Principal, prescription_id: str) -> dict[str, Any]:
        row = self.get_own(principal, prescription_id)
        if not row["items"]:
            raise ValidationError("處方至少要有一項藥品才能確認")
        changes = {
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC),
            "refill_reminder_on": self.refill_reminder_date(row),
        }
        return self.to_public(self._prescriptions.update(prescription_id, **changes))

    def discard(self, principal: Principal, prescription_id: str) -> None:
        self.get_own(principal, prescription_id)
        self._prescriptions.update(prescription_id, status="discarded")

    def mark_refill_reminded(self, prescription_id: str) -> None:
        self._prescriptions.update(prescription_id, refill_reminded_at=datetime.now(UTC))

    def refill_reminder_date(self, row: dict[str, Any]) -> date | None:
        """慢箋：下次可領藥日（開立日 + 給藥天數）前 N 天提醒；最後一次不提醒。"""
        if not row.get("is_chronic_refill"):
            return None
        seq, total = row.get("refill_seq"), row.get("refill_total")
        issued_on = _parse_date(row.get("issued_on"))
        days = row.get("days_supply")
        if not (issued_on and days and seq and total and seq < total):
            return None
        return issued_on + timedelta(days=days * seq - self._refill_days_before)

    @staticmethod
    def validate_items(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise ValidationError("items 必須是非空陣列")
        if len(raw) > 30:
            raise ValidationError("藥品最多 30 項")
        items: list[dict[str, Any]] = []
        for index, entry in enumerate(raw, start=1):
            if not isinstance(entry, dict):
                raise ValidationError(f"第 {index} 項格式錯誤")
            item: dict[str, Any] = {}
            for key, max_length in ITEM_TEXT_FIELDS.items():
                text = str(entry.get(key) or "").strip()
                if len(text) > max_length:
                    raise ValidationError(f"第 {index} 項 {key} 不可超過 {max_length} 字")
                item[key] = text or None
            if not item["drug_name"]:
                raise ValidationError(f"第 {index} 項缺少藥名")
            freq = entry.get("frequency_code") or "other"
            if freq not in FREQUENCY_CODES:
                raise ValidationError(
                    f"第 {index} 項頻率不合法", details={"allowed": FREQUENCY_CODES}
                )
            timing = entry.get("timing") or None
            if timing is not None and timing not in TIMING_CODES:
                raise ValidationError(f"第 {index} 項服用時機不合法")
            item.update(
                {
                    "frequency_code": freq,
                    "timing": timing,
                    "duration_days": _parse_int(entry.get("duration_days"), "duration_days"),
                    "nhi_code": entry.get("nhi_code") or None,
                    "category": entry.get("category") or None,
                    "confidence": entry.get("confidence"),
                }
            )
            items.append(item)
        return items

    @staticmethod
    def to_public(row: dict[str, Any]) -> dict[str, Any]:
        data = {k: v for k, v in row.items() if k not in PUBLIC_EXCLUDE}
        data["has_image"] = bool(row.get("image_path"))
        return data


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValidationError("日期格式須為 YYYY-MM-DD") from exc


def _parse_int(value: Any, key: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} 必須是整數") from exc
    if number < 0 or number > 3650:
        raise ValidationError(f"{key} 超出合理範圍")
    return number
