"""院所服務：清單、新增（SUPER_ADMIN）、修改（SUPER_ADMIN 任一院所 / HOSPITAL_ADMIN 自己院所）。

其他 Service 寫入 hospital_id 前呼叫 `require_active()` 確認院所存在且啟用。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.pagination import PAGE_SIZE, page_payload
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.repositories.hospital_repository import HospitalRepository
from app.services.pharmacy_service import parse_address
from app.utils.geo import parse_coords

HOSPITAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")
CAPACITY_TIERS = ("medical_center", "regional", "district", "clinic")
# 匯入時接受的欄位別名：自家範本欄位 + 健保署「特約醫事機構」開放資料欄位
# 支援自家範本、健保署「特約醫事機構」與衛福部「醫療機構與人員基本資料」（有「科別」）三種欄名
IMPORT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "code", "院所代碼", "醫事機構代碼", "機構代碼"),
    "name": ("name", "院所名稱", "名稱", "醫事機構名稱", "機構名稱"),
    "region": ("region", "區域", "縣市", "地址縣市別", "縣市區名"),
    "address": ("address", "地址"),
    "contact_phone": ("contact_phone", "phone", "電話", "聯絡電話"),
    "capacity_tier": ("capacity_tier", "層級", "醫事機構種類"),
    "departments": ("departments", "科別", "診療科別"),
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
IMPORT_TEMPLATE_HEADER = "id,name,region,address,contact_phone,capacity_tier,departments,lat,lng"
MAX_DEPARTMENTS_LENGTH = 1000

EDITABLE_FIELDS: dict[str, int] = {
    "name": 100,
    "region": 50,
    "address": 200,
    "contact_phone": 30,
}


class HospitalService:
    def __init__(self, hospitals: HospitalRepository) -> None:
        self._hospitals = hospitals

    def options(self, *, ensure_id: str | None = None) -> list[dict[str, Any]]:
        """後台下拉選單（指派人員 / 病人建檔 / 知識庫）用的院所：只有醫院層級，不含診所。

        `ensure_id`（通常是登入者自己的院所）即使是診所或已停用也一併帶回，
        否則後台側欄與「院所設定」會把該帳號當成尚未指派院所。
        """
        rows = self._hospitals.list(active_only=True, exclude_clinics=True)
        if ensure_id and all(h["id"] != ensure_id for h in rows):  # 新增
            own = self._hospitals.get(ensure_id)
            if own:
                rows.append(own)
        return [
            {
                "id": h["id"],
                "name": h["name"],
                "region": h["region"],
                "capacity_tier": h["capacity_tier"],
                "address": h["address"],
                "contact_phone": h["contact_phone"],
            }
            for h in rows
        ]

    def public_list(self) -> list[dict[str, Any]]:
        """給 LIFF 的院所選單：只回公開欄位、只有醫院層級。"""
        return [
            {"id": h["id"], "name": h["name"], "region": h["region"]}
            for h in self._hospitals.list(active_only=True, exclude_clinics=True)
        ]

    def paginate(
        self,
        *,
        query: str | None = None,
        region: str | None = None,
        district: str | None = None,
        tier: str | None = None,
        include_inactive: bool = False,
        page: int = 1,
    ) -> dict[str, Any]:
        """後台「所有院所」表格（每頁 15 筆）；每列多帶從地址解析出的 `city` / `district`。"""
        tier = (tier or "").strip() or None
        if tier is not None and tier not in CAPACITY_TIERS:
            raise ValidationError("capacity_tier 不合法", details={"allowed": list(CAPACITY_TIERS)})
        rows, total = self._hospitals.paginate(
            query=(query or "").strip() or None,
            region=(region or "").strip() or None,
            district=(district or "").strip() or None,
            tier=tier,
            active_only=not include_inactive,
            page=page,
            per_page=PAGE_SIZE,
        )
        items = []
        for row in rows:
            city, parsed_district = parse_address(row.get("address"))
            shown_city = (row.get("region") or city or "").replace("臺", "台") or None
            items.append({**row, "city": shown_city, "district": parsed_district})
        return page_payload(items, total, page)

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
        if "departments" in body:
            values["departments"] = HospitalService.clean_departments(body["departments"])
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
            # 沒有種類欄位（衛福部基本資料檔）時只能從名稱認出診所；醫院層級留空、不覆蓋既有值
            if not kind and "診所" in (mapped.get("name") or ""):
                kind = "診所"
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
    def clean_departments(value: Any) -> str | None:
        """科別：接受逗號 / 頓號分隔字串或清單，正規化成「A,B,C」（去空白、去重、去尾逗號）。"""
        if value is None:
            return None
        if isinstance(value, list):
            parts = value
        else:
            parts = str(value).replace("，", ",").replace("、", ",").split(",")
        cleaned = list(dict.fromkeys(str(x).strip() for x in parts if str(x).strip()))
        text = ",".join(cleaned)
        if len(text) > MAX_DEPARTMENTS_LENGTH:
            raise ValidationError(f"departments 不可超過 {MAX_DEPARTMENTS_LENGTH} 字")
        return text or None

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
        """沒有 region 時從地址前 3 個字（縣市）推得；「縣市區名」這類含鄉鎮市區的值只留縣市。"""
        text = mapped.get("region") or mapped.get("address") or ""
        if len(text) > 3 and text[2] in "市縣":
            return text[:3]
        return text or None
