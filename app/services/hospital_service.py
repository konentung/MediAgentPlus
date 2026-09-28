"""院所服務：清單、新增（SUPER_ADMIN）、修改（SUPER_ADMIN 任一院所 / HOSPITAL_ADMIN 自己院所）。

其他 Service 寫入 hospital_id 前呼叫 `require_active()` 確認院所存在且啟用。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.hospital_repository import HospitalRepository
from app.utils.geo import parse_coords

HOSPITAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
CAPACITY_TIERS = ("medical_center", "regional", "district", "clinic")
# 匯入時接受的欄位別名：自家範本欄位 + 健保署「特約醫事機構」開放資料欄位
IMPORT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "code", "院所代碼", "醫事機構代碼"),
    "name": ("name", "院所名稱", "名稱", "醫事機構名稱"),
    "region": ("region", "區域", "縣市", "地址縣市別"),
    "address": ("address", "地址"),
    "contact_phone": ("contact_phone", "phone", "電話", "聯絡電話"),
    "capacity_tier": ("capacity_tier", "層級", "醫事機構種類"),
    "lat": ("lat", "latitude", "緯度"),
    "lng": ("lng", "lon", "longitude", "經度"),
}
# 醫事機構種類 → capacity_tier；藥局等不屬於院所的種類整列略過
TIER_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("醫學中心", "medical_center"),
    ("區域醫院", "regional"),
    ("地區醫院", "district"),
    ("診所", "clinic"),
)
SKIP_KIND_KEYWORDS = (
    "藥局",
    "藥劑",
    "護理",
    "助產",
    "檢驗",
    "物理治療",
    "職能治療",
    "居家",
    "精神復健",
)
IMPORT_TEMPLATE_HEADER = "id,name,region,address,contact_phone,capacity_tier,lat,lng"

EDITABLE_FIELDS: dict[str, int] = {
    "name": 100,
    "region": 50,
    "address": 200,
    "contact_phone": 30,
}


class HospitalService:
    def __init__(self, hospitals: HospitalRepository) -> None:
        self._hospitals = hospitals

    def list(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        return self._hospitals.list(active_only=active_only)

    def public_list(self) -> list[dict[str, Any]]:
        """給 LIFF 表單的院所選單：只回公開欄位。"""
        return [
            {"id": h["id"], "name": h["name"], "region": h["region"]}
            for h in self._hospitals.list(active_only=True)
        ]

    def get(self, hospital_id: str) -> dict[str, Any]:
        hospital = self._hospitals.get(hospital_id)
        if hospital is None:
            raise NotFoundError("院所不存在")
        return hospital

    def require_active(self, hospital_id: str) -> dict[str, Any]:
        """寫入 hospital_id 前的檢查：必須存在且啟用。"""
        hospital = self._hospitals.get(hospital_id)
        if hospital is None or not hospital["active"]:
            raise ValidationError("院所代碼不存在或已停用", details={"hospital_id": hospital_id})
        return hospital

    def create(self, actor: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以新增院所")
        hospital_id = str(body.get("id") or "").strip()
        if not HOSPITAL_ID_PATTERN.match(hospital_id):
            raise ValidationError(
                "院所代碼須為 2–32 碼英數字、底線或連字號，且以英數字開頭",
                details={"id": hospital_id},
            )
        if self._hospitals.get(hospital_id) is not None:
            raise ConflictError("院所代碼已存在")
        values = self._clean(body, require_name=True)
        return self._hospitals.create(id=hospital_id, created_by=actor.user_id, **values)

    def update(self, actor: Principal, hospital_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.get(hospital_id)
        if not AccessPolicy.can_access_hospital(actor, hospital_id):
            raise AuthorizationError("不得修改其他院所的設定")
        changes = self._clean(body, require_name=False)
        if "active" in body:
            if actor.role is not Role.SUPER_ADMIN:
                raise AuthorizationError("只有系統管理員可以停用 / 啟用院所")
            changes["active"] = bool(body["active"])
        if not changes:
            raise ValidationError("沒有可更新的欄位")
        return self._hospitals.update(hospital_id, **changes)

    @staticmethod
    def _clean(body: dict[str, Any], *, require_name: bool) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, max_length in EDITABLE_FIELDS.items():
            if key not in body:
                continue
            text = str(body[key] or "").strip()
            if len(text) > max_length:
                raise ValidationError(f"{key} 不可超過 {max_length} 字")
            values[key] = text or None
        if (require_name or "name" in values) and not values.get("name"):
            raise ValidationError("院所名稱為必填")
        if "capacity_tier" in body:
            tier = body["capacity_tier"] or None
            if tier is not None and tier not in CAPACITY_TIERS:
                raise ValidationError(
                    "capacity_tier 不合法", details={"allowed": list(CAPACITY_TIERS)}
                )
            values["capacity_tier"] = tier
        if "lat" in body or "lng" in body:
            lat, lng = body.get("lat"), body.get("lng")
            if lat in (None, "") and lng in (None, ""):
                values["lat"] = values["lng"] = None
            else:
                coords = parse_coords(lat, lng)
                if coords is None:
                    raise ValidationError("lat / lng 必須同時提供且為合法座標")
                values["lat"], values["lng"] = coords
        return values

    def import_rows(
        self, actor: Principal, rows: list[dict[str, Any]], *, only_hospitals: bool = False
    ) -> dict[str, Any]:
        """批次匯入（新增或更新）。欄位名稱依 IMPORT_COLUMN_ALIASES 對應。

        `only_hospitals=True` 時略過診所，只留醫學中心 / 區域 / 地區醫院。
        回傳 {"created", "updated", "skipped", "errors": [{"row", "message"}]}。
        """
        if actor.role is not Role.SUPER_ADMIN:
            raise AuthorizationError("只有系統管理員可以匯入院所")
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        errors: list[dict[str, Any]] = []
        skipped = 0
        for index, raw in enumerate(rows, start=2):  # 第 1 列是標題
            mapped = self._map_columns(raw)
            kind = mapped.get("capacity_tier") or ""
            if any(k in kind for k in SKIP_KIND_KEYWORDS):
                skipped += 1
                continue
            tier = self._tier_from_text(kind)
            if only_hospitals and tier in (None, "clinic"):
                skipped += 1
                continue
            hospital_id = (mapped.get("id") or "").strip()
            if not HOSPITAL_ID_PATTERN.match(hospital_id):
                errors.append({"row": index, "message": f"院所代碼格式不正確：{hospital_id!r}"})
                continue
            if hospital_id in seen:
                errors.append({"row": index, "message": f"代碼重複：{hospital_id}"})
                continue
            try:
                values = self._clean(
                    {**mapped, "capacity_tier": tier, "region": self._region(mapped)},
                    require_name=True,
                )
            except ValidationError as exc:
                errors.append({"row": index, "message": exc.message})
                continue
            seen.add(hospital_id)
            prepared.append({"id": hospital_id, "created_by": actor.user_id, **values})
        created, updated = self._hospitals.upsert_many(prepared) if prepared else (0, 0)
        return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}

    @staticmethod
    def _map_columns(raw: dict[str, Any]) -> dict[str, str]:
        normalized = {str(k).strip().lstrip("\ufeff").lower(): v for k, v in raw.items() if k}
        mapped: dict[str, str] = {}
        for field, aliases in IMPORT_COLUMN_ALIASES.items():
            for alias in aliases:
                if alias.lower() in normalized and normalized[alias.lower()] not in (None, ""):
                    mapped[field] = str(normalized[alias.lower()]).strip()
                    break
        return mapped

    @staticmethod
    def _tier_from_text(text: str) -> str | None:
        if text in CAPACITY_TIERS:
            return text
        for keyword, tier in TIER_KEYWORDS:
            if keyword in text:
                return tier
        return None

    @staticmethod
    def _region(mapped: dict[str, str]) -> str | None:
        """沒有 region 欄位時，從地址前 3 個字（縣市）推得。"""
        if mapped.get("region"):
            return mapped["region"]
        address = mapped.get("address") or ""
        return address[:3] if len(address) >= 3 and address[2] in "市縣" else None
