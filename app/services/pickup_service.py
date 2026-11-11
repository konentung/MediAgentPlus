"""領藥需求：病人把已確認的處方指定到某家藥局，藥局人員在後台備藥、通知、完成。

- 病人端（LINE「去哪拿藥」的藥局清單）：只有「有人員帳號」的藥局才出現「請這家先備藥」按鈕，
  按下去就是同意把這張處方的藥品清單給該藥局看；處方照片、病人基本資料都不提供。
- 藥局端（後台「領藥需求」）：只看得到指定到自己藥局的需求（`AccessPolicy.pharmacy_scope`）。
- 狀態：requested → ready（推播病人可以來領）→ picked_up；
  requested / ready → cancelled（推播原因）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import PAGE_SIZE, page_payload
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.domain.reminder_rules import FREQUENCY_LABELS, TIMING_LABELS
from app.line.messages.builder import MessageBuilder as M
from app.repositories.pharmacy_repository import PharmacyRepository
from app.repositories.pickup_repository import PickupRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "requested": "待備藥",
    "ready": "可領取",
    "picked_up": "已領取",
    "cancelled": "無法調劑",
}
# 目前狀態 → 可以改成哪些狀態
TRANSITIONS: dict[str, frozenset[str]] = {
    "requested": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"picked_up", "cancelled"}),
}
NOTE_MAX_LENGTH = 300
# 給藥局看的處方欄位：只有領藥需要的內容，不含照片與辨識原始資料
PRESCRIPTION_FIELDS = (
    "hospital_name",
    "issued_on",
    "days_supply",
    "is_chronic_refill",
    "refill_seq",
    "refill_total",
)
ITEM_FIELDS = ("drug_name", "dose_text", "duration_days", "quantity", "instructions")


def pickup_code(pickup_id: str) -> str:
    """領藥編號：病人在 LINE 上看到、到藥局報給藥師對照（不是驗證碼，只是方便找單）。"""
    return pickup_id[:6].upper()


class PickupService:
    def __init__(
        self,
        pickups: PickupRepository,
        prescriptions: PrescriptionRepository,
        pharmacies: PharmacyRepository,
        users: UserRepository,
        line_client: Callable[[], Any],
    ) -> None:
        self._pickups = pickups
        self._prescriptions = prescriptions
        self._pharmacies = pharmacies
        self._users = users
        self._line = line_client

    # ---- 病人端 ----

    def staffed_ids(self, pharmacy_ids: list[str]) -> set[str]:
        """這些藥局裡哪幾家有人員帳號（會在後台看到需求、備好藥推播）。"""
        return self._users.pharmacy_ids_with_staff(pharmacy_ids)

    def request(
        self, principal: Principal, prescription_id: str, pharmacy_id: str
    ) -> dict[str, Any]:
        """病人選擇要去哪家藥局領藥：每家推薦的藥局都可以選（不限有人員帳號的藥局），
        一律建立領藥需求；藥局有人員帳號時會在後台看到並備藥，沒有的話只是記下病人的選擇
        （回傳 `staffed` 讓回覆文案說明）。同一張處方再選別家 = 換藥局；已領過的不能再送。"""
        prescription = self._prescriptions.get(prescription_id)
        if (
            prescription is None
            or prescription["user_id"] != principal.user_id
            or prescription["status"] == "discarded"
        ):
            raise NotFoundError("找不到這張處方")
        if prescription["status"] != "confirmed":
            raise ValidationError("請先確認處方內容，再指定領藥的藥局")
        pharmacy = self._pharmacies.get(pharmacy_id)
        if pharmacy is None or not pharmacy.get("active", True):
            raise NotFoundError("找不到這家藥局")
        existing = self._pickups.get_for_prescription(prescription_id)
        if existing and existing["status"] == "picked_up":
            raise ConflictError("這張處方已經領過藥了")
        row = self._pickups.request(
            prescription_id=prescription_id, user_id=principal.user_id, pharmacy_id=pharmacy_id
        )
        return {
            **row,
            "code": pickup_code(row["id"]),
            "pharmacy_name": pharmacy["name"],
            "pharmacy_phone": pharmacy.get("phone"),
            "pharmacy_address": pharmacy.get("address"),
            "staffed": pharmacy_id in self.staffed_ids([pharmacy_id]),
        }

    # ---- 藥局端 ----

    def list(
        self,
        actor: Principal,
        *,
        status: str | None = None,
        page: int = 1,
        pharmacy_id: str | None = None,
    ) -> dict[str, Any]:
        """藥局人員只看得到自己藥局的需求；SUPER_ADMIN 看全部，可用 `pharmacy_id` 篩選。"""
        scope = AccessPolicy.pharmacy_scope(actor)
        if status and status not in STATUS_LABELS:
            raise ValidationError("status 不合法", details={"allowed": list(STATUS_LABELS)})
        rows, total = self._pickups.paginate(
            pharmacy_id=scope or pharmacy_id or None, status=status, page=page, per_page=PAGE_SIZE
        )
        return page_payload([self._public(row) for row in rows], total, page)

    def count_requested(self, actor: Principal) -> int:
        return self._pickups.count(
            pharmacy_id=AccessPolicy.pharmacy_scope(actor), status="requested"
        )

    def set_status(
        self, actor: Principal, pickup_id: str, status: str, note: Any = None
    ) -> dict[str, Any]:
        row = self._pickups.get(pickup_id)
        # 別家藥局的需求一律當作不存在，不透露有這筆
        if row is None or not AccessPolicy.can_access_pharmacy(actor, row["pharmacy_id"]):
            raise NotFoundError("找不到這筆領藥需求")
        if status not in TRANSITIONS.get(row["status"], frozenset()):
            raise ConflictError(
                f"目前是「{STATUS_LABELS[row['status']]}」，不能改成"
                f"「{STATUS_LABELS.get(status, status)}」"
            )
        note_text = str(note or "").strip()
        if len(note_text) > NOTE_MAX_LENGTH:
            raise ValidationError(f"備註不可超過 {NOTE_MAX_LENGTH} 字")
        if status == "cancelled" and not note_text:
            raise ValidationError("請填寫無法調劑的原因，病人會收到這段說明")
        changes: dict[str, Any] = {"status": status, "handled_by": actor.user_id}
        if note_text or status == "cancelled":
            changes["note"] = note_text or None
        now = datetime.now(UTC)
        if status == "ready":
            changes["ready_at"] = now
        elif status == "picked_up":
            changes["picked_up_at"] = now
        updated = self._pickups.update(pickup_id, **changes)
        if status in ("ready", "cancelled"):
            self._notify_patient(updated)
        return self._public(updated)

    # ---- helpers ----

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        prescription = self._prescriptions.get(row["prescription_id"]) or {}
        patient = self._users.get_by_id(row["user_id"])
        pharmacy = self._pharmacies.get(row["pharmacy_id"]) or {}
        items = [
            {
                **{key: item.get(key) for key in ITEM_FIELDS},
                "frequency_label": FREQUENCY_LABELS.get(item.get("frequency_code") or "other"),
                "timing_label": TIMING_LABELS.get(item.get("timing") or ""),
            }
            for item in prescription.get("items") or []
        ]
        return {
            "id": row["id"],
            "code": pickup_code(row["id"]),
            "status": row["status"],
            "status_label": STATUS_LABELS.get(row["status"], row["status"]),
            "next_statuses": sorted(TRANSITIONS.get(row["status"], frozenset())),
            "note": row.get("note"),
            "requested_at": row.get("requested_at"),
            "ready_at": row.get("ready_at"),
            "picked_up_at": row.get("picked_up_at"),
            "pharmacy_id": row["pharmacy_id"],
            "pharmacy_name": pharmacy.get("name"),
            "patient_name": patient.display_name if patient else None,
            "prescription": {
                **{key: prescription.get(key) for key in PRESCRIPTION_FIELDS},
                "items": items,
            },
        }

    def _notify_patient(self, row: dict[str, Any]) -> None:
        """推播失敗只記 log：狀態已寫入，不能讓藥局端的操作變成 500。"""
        patient = self._users.get_by_id(row["user_id"])
        if patient is None or not patient.line_user_id or not patient.push_enabled:
            return
        pharmacy = self._pharmacies.get(row["pharmacy_id"]) or {}
        name = pharmacy.get("name") or "藥局"
        if row["status"] == "ready":
            text = (
                f"{name} 已備好你的藥，領藥編號 {pickup_code(row['id'])}。\n"
                "請攜帶處方箋正本與健保卡前往領藥。"
            )
        else:
            text = f"{name} 無法調劑這張處方，請改到其他藥局或回原院所領藥。"
        if row.get("note"):
            text += f"\n藥局說明：{row['note']}"
        try:
            self._line().push(patient.line_user_id, [M.text(text)])
        except Exception:  # noqa: BLE001 - 推播失敗不影響狀態更新
            logger.exception("LINE push failed for pickup %s", row["id"])
