"""病人的就醫院所（LIFF「我的就醫院所」表單 + LINE 上回答院方的確認）。

就醫院所與基本資料分開：病人填一次基本資料，之後去哪家院所
就在這裡加哪家，每家院所一筆（`patient_hospitals`），互不影響：
- 病人自己加入 = 病人本人在自己的 LINE 登入下指定，當下即視為已確認，院所可直接核對建檔。
- 院方在後台「搜尋病人」加入本院 = 未確認，推播兩個按鈕請病人按「是」（`confirm()`）。
- 病人可移除還沒建檔的院所（院方建檔前先開的暫存紀錄一起作廢）；已建檔的要請院所處理。
院方那一側（加入本院、核對建檔）在 `PatientRecordService`。
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.domain.principal import Principal
from app.repositories.patient_hospital_repository import (
    SOURCE_HOSPITAL,
    SOURCE_PATIENT,
    PatientHospitalRepository,
)
from app.repositories.patient_record_repository import (
    RECORD_STATUS_DRAFT,
    PatientRecordRepository,
)
from app.repositories.patient_repository import PatientRepository
from app.services.hospital_service import HospitalService

MAX_HOSPITALS = 20  # 一位病人最多加幾家院所（避免亂加）


class PatientHospitalService:
    def __init__(
        self,
        links: PatientHospitalRepository,
        patients: PatientRepository,
        records: PatientRecordRepository,
        hospitals: HospitalService,
    ) -> None:
        self._links = links
        self._patients = patients
        self._records = records
        self._hospitals = hospitals

    def list_mine(self, principal: Principal) -> dict[str, Any]:
        """LIFF 頁面：我的就醫院所（含是否已確認、是否已建檔）+ 基本資料是否已送出。"""
        self._require_login(principal)
        profile = self._patients.get_profile(principal.user_id) or {}
        records = {
            r["hospital_id"]: r
            for r in self._records.list_for_user(principal.user_id)
            if r["status"] != RECORD_STATUS_DRAFT
        }
        return {
            "hospitals": [
                self._public(link, records) for link in self._links.list_for_user(principal.user_id)
            ],
            "submitted": bool(profile.get("submitted_at")),
            "max_hospitals": MAX_HOSPITALS,
        }

    def add_mine(self, principal: Principal, hospital_id: Any) -> dict[str, Any]:
        """病人自己加入一家就醫院所：本人在自己的 LINE 登入下指定，當下即視為已確認。"""
        self._require_login(principal)
        hospital = self._hospitals.require_active(self._clean_id(hospital_id))
        existing = self._links.list_for_user(principal.user_id)
        if (
            not any(h["hospital_id"] == hospital["id"] for h in existing)
            and len(existing) >= MAX_HOSPITALS
        ):
            raise ValidationError(f"最多只能加入 {MAX_HOSPITALS} 家院所，請先移除不再就醫的院所")
        link = self._links.upsert(
            principal.user_id, hospital["id"], source=SOURCE_PATIENT, confirmed=True
        )
        return self._public(link, self._active_records(principal.user_id))

    def remove_mine(self, principal: Principal, hospital_id: str) -> None:
        """病人移除一家院所：還沒建檔的才能移除，院方先開的暫存紀錄一起作廢。"""
        self._require_login(principal)
        link = self._links.get(principal.user_id, hospital_id)
        if link is None:
            raise NotFoundError("你沒有加入這家院所")
        self._ensure_not_filed(principal.user_id, hospital_id)
        self._discard_draft_record(principal.user_id, hospital_id)
        self._links.delete(principal.user_id, hospital_id)

    def confirm(self, principal: Principal, hospital_id: str, *, accepted: bool) -> dict[str, Any]:
        """病人回答「是否在某院所就醫」（LINE 按鈕或 LIFF 頁面）。

        接受：記下確認時間（院方還沒加入也可以，等同自己加入）；否認：刪掉這家院所，
        院方先開的暫存紀錄一起作廢。已建檔的不能否認，請病人聯絡院所。回傳院所資料供回覆文案用。
        """
        self._require_login(principal)
        hospital = self._hospitals.require_active(hospital_id)
        profile = self._patients.get_profile(principal.user_id)
        if not profile or not profile.get("submitted_at"):
            raise ValidationError("請先填寫並送出基本資料")
        if accepted:
            self._links.upsert(
                principal.user_id, hospital_id, source=SOURCE_HOSPITAL, confirmed=True
            )
        else:
            self._ensure_not_filed(principal.user_id, hospital_id)
            self._discard_draft_record(principal.user_id, hospital_id)
            self._links.delete(principal.user_id, hospital_id)
        return hospital

    # ---- helpers ----

    @staticmethod
    def _require_login(principal: Principal) -> None:
        if not principal.is_authenticated:
            raise AuthorizationError("請先登入")
        if principal.is_locked:
            raise AuthorizationError("帳號已鎖定，請聯絡院所協助處理")

    @staticmethod
    def _clean_id(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValidationError(
                "請選擇院所", details={"fields": {"hospital_id": "請從搜尋結果選擇院所"}}
            )
        return text

    def _active_records(self, user_id: str) -> dict[str, dict[str, Any]]:
        return {
            r["hospital_id"]: r
            for r in self._records.list_for_user(user_id)
            if r["status"] != RECORD_STATUS_DRAFT
        }

    def _ensure_not_filed(self, user_id: str, hospital_id: str) -> None:
        record = self._records.get(user_id, hospital_id)
        if record is not None and record["status"] != RECORD_STATUS_DRAFT:
            raise ConflictError("你已在這家院所建檔，若不再就醫請聯絡院所處理")

    def _discard_draft_record(self, user_id: str, hospital_id: str) -> None:
        record = self._records.get(user_id, hospital_id)
        if record is not None and record["status"] == RECORD_STATUS_DRAFT:
            self._records.delete(record["id"])

    @staticmethod
    def _public(link: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "hospital_id": link["hospital_id"],
            "hospital_name": link["hospital_name"],
            "source": link["source"],
            "confirmed": link["confirmed"],
            "requested_at": link["requested_at"],
            "confirmed_at": link.get("confirmed_at"),
            # 院所已核對建檔：病人端不能移除
            "filed": link["hospital_id"] in records,
        }
