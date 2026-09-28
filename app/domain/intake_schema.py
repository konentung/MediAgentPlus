"""病人自填表（Intake Form）欄位定義：LIFF 頁面渲染、伺服器端驗證、後台顯示共用同一份。

設計文件：docs/patient-form-design.md §2。
`storage` 決定欄位落到哪張表（見 `app.repositories.patient_repository`）：
    profile            patient_profiles 的一般欄位
    json               patient_profiles 的 JSON 欄位（多選）
    contacts           patient_emergency_contacts
    allergy:<kind>     patient_allergies（kind = drug / food）
    condition:<kind>   patient_conditions（kind = chronic / surgery / family）
    medication         patient_medications
    consent            patient_consents（每次變更新增一列）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.exceptions import ValidationError
from app.utils.clock import today_tw

Option = tuple[str, str]  # (value, label)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str  # text / textarea / date / number / select / multi / bool / list / hidden
    required: bool = False
    storage: str = "profile"
    options: tuple[Option, ...] = ()
    item_fields: tuple[Field, ...] = ()  # type=list 時每一筆的欄位
    min_value: float | None = None
    max_value: float | None = None
    min_items: int = 0
    max_items: int = 10
    max_length: int = 255
    pattern: str | None = None
    help: str | None = None
    show_if: tuple[str, str] | None = None  # (其他欄位 key, 值) 成立時才顯示 / 才驗證必填
    required_if: tuple[str, str] | None = None
    none_flag: str | None = None  # list 型別：勾選此 key 代表「無」，允許空清單
    must_be_true: bool = False  # bool 型別：必須勾選（同意條款）
    # select 型別：選項改由 API 動態提供（目前只有 "hospitals" → /api/v1/liff/hospitals），
    # 伺服器端只檢查格式，實際值是否存在由 Service 驗證
    options_source: str | None = None

    def option_label(self, value: Any) -> str:
        for val, label in self.options:
            if val == value:
                return label
        return str(value) if value not in (None, "") else ""

    def option_values(self) -> tuple[str, ...]:
        return tuple(val for val, _ in self.options)


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    description: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


PHONE_PATTERN = r"^09\d{8}$"
YES_NO_UNKNOWN: tuple[Option, ...] = (("no", "否"), ("yes", "是"), ("unknown", "不確定"))
RELATIONSHIPS: tuple[Option, ...] = (
    ("spouse", "配偶"),
    ("child", "子女"),
    ("parent", "父母"),
    ("sibling", "手足"),
    ("caregiver", "看護"),
    ("friend", "朋友"),
    ("other", "其他"),
)
SEVERITY: tuple[Option, ...] = (("mild", "輕微"), ("moderate", "中度"), ("severe", "嚴重"))
REACTIONS: tuple[Option, ...] = (
    ("rash", "皮膚紅疹 / 搔癢"),
    ("swelling", "腫脹"),
    ("breathing", "呼吸困難"),
    ("gi", "腸胃不適"),
    ("other", "其他"),
)
CHRONIC_CONDITIONS: tuple[Option, ...] = (
    ("hypertension", "高血壓"),
    ("diabetes", "糖尿病"),
    ("heart_disease", "心臟病"),
    ("kidney_disease", "腎臟病（含洗腎）"),
    ("liver_disease", "肝病"),
    ("asthma_copd", "氣喘或 COPD"),
    ("stroke", "中風"),
    ("cancer", "癌症"),
    ("epilepsy", "癲癇"),
    ("thyroid", "甲狀腺疾病"),
    ("mental_health", "精神或情緒相關"),
    ("autoimmune", "自體免疫疾病"),
    ("other", "其他"),
)
FAMILY_HISTORY: tuple[Option, ...] = (
    ("hypertension", "高血壓"),
    ("diabetes", "糖尿病"),
    ("heart_disease", "心臟病"),
    ("stroke", "中風"),
    ("cancer", "癌症"),
    ("other", "其他"),
    ("unknown", "不知道"),
)
MED_FREQUENCY: tuple[Option, ...] = (
    ("qd", "每日 1 次"),
    ("bid", "每日 2 次"),
    ("tid", "每日 3 次"),
    ("qid", "每日 4 次"),
    ("hs", "睡前"),
    ("prn", "需要時"),
    ("other", "其他"),
)

INTAKE_STEPS: tuple[Step, ...] = (
    Step(
        1,
        "填表人與病人身份",
        "請填寫病人的基本身份資料。若您是家屬代填，請一併留下您的聯絡方式。",
        (
            Field(
                "filler_relation",
                "我是",
                "select",
                required=True,
                options=(("self", "病人本人"), ("family", "家屬 / 照顧者代填")),
            ),
            Field(
                "filler_name",
                "代填者姓名",
                "text",
                required_if=("filler_relation", "family"),
                show_if=("filler_relation", "family"),
                max_length=50,
            ),
            Field(
                "filler_relationship",
                "代填者與病人的關係",
                "select",
                options=RELATIONSHIPS,
                required_if=("filler_relation", "family"),
                show_if=("filler_relation", "family"),
            ),
            Field(
                "filler_phone",
                "代填者手機",
                "text",
                pattern=PHONE_PATTERN,
                required_if=("filler_relation", "family"),
                show_if=("filler_relation", "family"),
                help="例：0912345678",
            ),
            Field("full_name", "病人姓名", "text", required=True, max_length=50),
            Field(
                "national_id",
                "身分證 / 居留證字號",
                "text",
                required=True,
                help="僅供院所核對身份，加密儲存、畫面上只顯示末四碼",
            ),
            Field("birth_date", "出生日期", "date", required=True),
            Field(
                "sex",
                "生理性別",
                "select",
                required=True,
                options=(("male", "男"), ("female", "女"), ("other", "其他")),
            ),
            Field(
                "preferred_language",
                "慣用語言",
                "select",
                options=(
                    ("mandarin", "國語"),
                    ("taiwanese", "台語"),
                    ("hakka", "客語"),
                    ("english", "英語"),
                    ("other", "其他"),
                ),
            ),
        ),
    ),
    Step(
        2,
        "聯絡方式與緊急聯絡人",
        "院所需要能夠聯絡到您，緊急時也需要聯絡您的家人。",
        (
            Field(
                "phone", "手機", "text", required=True, pattern=PHONE_PATTERN, help="例：0912345678"
            ),
            Field("email", "Email", "text", pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
            Field("address_city", "縣市", "text", required=True, max_length=20),
            Field("address_district", "鄉鎮市區", "text", required=True, max_length=20),
            Field("address_street", "地址", "text", required=True, max_length=200),
            Field(
                "emergency_contacts",
                "緊急聯絡人",
                "list",
                storage="contacts",
                max_items=3,
                help="選填，建議至少留一位，緊急時院所才能聯絡到家人",
                item_fields=(
                    Field("name", "姓名", "text", required=True, max_length=50),
                    Field("relationship", "關係", "select", required=True, options=RELATIONSHIPS),
                    Field("phone", "電話", "text", required=True, pattern=r"^0\d{8,9}$"),
                ),
            ),
        ),
    ),
    Step(
        3,
        "基本健康狀況",
        "身高體重用於劑量與照護建議計算，請務必填寫；其餘不確定的可以先跳過。",
        (
            Field(
                "height_cm", "身高（公分）", "number", required=True, min_value=50, max_value=250
            ),
            Field("weight_kg", "體重（公斤）", "number", required=True, min_value=2, max_value=300),
            Field(
                "blood_type",
                "血型",
                "select",
                options=(("A", "A"), ("B", "B"), ("AB", "AB"), ("O", "O"), ("unknown", "不知道")),
            ),
            Field(
                "rh",
                "Rh 血型",
                "select",
                options=(
                    ("positive", "Rh 陽性 (+)"),
                    ("negative", "Rh 陰性 (−)"),
                    ("unknown", "不知道"),
                ),
            ),
            Field(
                "pregnancy_status",
                "是否懷孕或哺乳中",
                "select",
                options=(
                    ("no", "否"),
                    ("pregnant", "懷孕中"),
                    ("breastfeeding", "哺乳中"),
                    ("unknown", "不確定"),
                ),
                show_if=("sex", "female"),
            ),
            Field(
                "smoking",
                "吸菸",
                "select",
                options=(("never", "從不"), ("former", "已戒"), ("current", "目前有")),
            ),
            Field(
                "smoking_per_day",
                "每天大約幾支",
                "number",
                min_value=1,
                max_value=200,
                show_if=("smoking", "current"),
            ),
            Field(
                "drinking",
                "飲酒",
                "select",
                options=(("never", "從不"), ("occasional", "偶爾"), ("frequent", "經常")),
            ),
            Field(
                "betel_nut",
                "嚼檳榔",
                "select",
                options=(("never", "從不"), ("former", "已戒"), ("current", "目前有")),
            ),
        ),
    ),
    Step(
        4,
        "過敏與病史",
        "這一頁最重要，請盡量填寫。若確定沒有，請勾選「無」。",
        (
            Field(
                "drug_allergies",
                "藥物過敏",
                "list",
                required=True,
                storage="allergy:drug",
                none_flag="drug_allergies_none",
                max_items=10,
                item_fields=(
                    Field("substance", "藥物名稱", "text", required=True, max_length=100),
                    Field("reaction", "反應", "select", options=REACTIONS),
                    Field("severity", "嚴重程度", "select", options=SEVERITY),
                ),
            ),
            Field("drug_allergies_none", "無藥物過敏", "bool"),
            Field(
                "food_allergies",
                "食物過敏",
                "list",
                required=True,
                storage="allergy:food",
                none_flag="food_allergies_none",
                max_items=10,
                item_fields=(
                    Field("substance", "食物名稱", "text", required=True, max_length=100),
                    Field("reaction", "反應", "select", options=REACTIONS),
                    Field("severity", "嚴重程度", "select", options=SEVERITY),
                ),
            ),
            Field("food_allergies_none", "無食物過敏", "bool"),
            Field(
                "other_allergies",
                "其他過敏",
                "multi",
                storage="json",
                options=(
                    ("contrast", "顯影劑"),
                    ("latex", "乳膠"),
                    ("anesthesia", "麻醉藥"),
                    ("other", "其他"),
                ),
            ),
            Field(
                "chronic_conditions",
                "慢性病 / 長期病史",
                "multi",
                required=True,
                storage="condition:chronic",
                options=CHRONIC_CONDITIONS,
                none_flag="chronic_conditions_none",
            ),
            Field("chronic_conditions_none", "無慢性病", "bool"),
            Field(
                "condition_notes",
                "病史補充",
                "textarea",
                max_length=500,
                help="例：癌症種類、確診年份、目前治療情況",
            ),
            Field(
                "surgery_history",
                "手術或住院紀錄",
                "list",
                storage="condition:surgery",
                max_items=10,
                item_fields=(
                    Field("year", "年份", "number", min_value=1900, max_value=2100),
                    Field("description", "手術 / 住院原因", "text", required=True, max_length=200),
                    Field("hospital_name", "醫院", "text", max_length=100),
                ),
            ),
            Field(
                "family_history",
                "家族病史",
                "multi",
                storage="condition:family",
                options=FAMILY_HISTORY,
            ),
        ),
    ),
    Step(
        5,
        "目前用藥",
        "包含長期服用的處方藥、中藥與保健食品，方便院所檢查藥物交互作用。",
        (
            Field(
                "has_current_medications",
                "目前是否有長期用藥",
                "select",
                required=True,
                options=(("no", "沒有"), ("yes", "有")),
            ),
            Field(
                "current_medications",
                "用藥清單",
                "list",
                storage="medication",
                required_if=("has_current_medications", "yes"),
                show_if=("has_current_medications", "yes"),
                min_items=1,
                max_items=20,
                item_fields=(
                    Field("drug_name", "藥名", "text", required=True, max_length=100),
                    Field("dose", "劑量", "text", max_length=50, help="例：1 顆、5 mg"),
                    Field("frequency", "頻率", "select", options=MED_FREQUENCY),
                    Field("purpose", "用途", "text", max_length=100),
                ),
            ),
            Field("supplements", "中藥 / 保健食品", "textarea", max_length=300),
        ),
    ),
    Step(
        6,
        "就醫資訊與同意事項",
        "最後確認就醫相關資訊，並閱讀個人資料使用說明。",
        (
            Field(
                "nhi_status",
                "健保身份",
                "select",
                options=(("insured", "一般健保"), ("uninsured", "無健保"), ("unknown", "不確定")),
            ),
            Field("catastrophic_illness_card", "持有重大傷病卡", "bool"),
            Field("disability_card", "持有身心障礙證明", "bool"),
            Field(
                "accessibility_needs",
                "需要的無障礙協助",
                "multi",
                storage="json",
                options=(
                    ("wheelchair", "輪椅"),
                    ("visual", "視障協助"),
                    ("hearing", "聽障協助"),
                    ("interpreter", "翻譯"),
                    ("other", "其他"),
                ),
            ),
            Field(
                "consent_data_collection",
                "我已閱讀並同意個人資料蒐集、處理及利用告知事項",
                "bool",
                required=True,
                storage="consent",
                must_be_true=True,
            ),
            Field(
                "consent_share_with_hospitals",
                "我同意將資料提供給我就診的合作院所",
                "bool",
                storage="consent",
            ),
            Field(
                "consent_push_notification",
                "我同意接收 LINE 衛教與服藥提醒推播",
                "bool",
                storage="consent",
            ),
        ),
    ),
)

CONSENT_VERSION = "2026-09-01"  # 告知事項文案版本，文案更新時一併更新

STEP_BY_NUMBER: dict[int, Step] = {step.number: step for step in INTAKE_STEPS}
ALL_FIELDS: dict[str, Field] = {f.key: f for step in INTAKE_STEPS for f in step.fields}
TOTAL_STEPS = len(INTAKE_STEPS)


def get_step(number: int) -> Step:
    step = STEP_BY_NUMBER.get(number)
    if step is None:
        raise ValidationError(f"步驟 {number} 不存在", details={"total_steps": TOTAL_STEPS})
    return step


_ID_LETTER_VALUES = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17, "I": 34,
    "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23, "Q": 24, "R": 25,
    "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
}  # fmt: skip
_WEIGHTS = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)


def is_valid_national_id(value: str) -> bool:
    """台灣身分證（A123456789）與新式居留證（A800000014）共用同一套檢核碼；舊式居留證（AB12345678）第二碼為字母。"""
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]\d{8}", value):
        return False
    first = _ID_LETTER_VALUES[value[0]]
    digits = [first // 10, first % 10]
    if value[1].isalpha():
        digits.append(_ID_LETTER_VALUES[value[1]] % 10)  # 舊式居留證：第二碼字母取個位數
    else:
        digits.append(int(value[1]))
    digits.extend(int(ch) for ch in value[2:])
    return sum(d * w for d, w in zip(digits, _WEIGHTS, strict=True)) % 10 == 0


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _condition_met(cond: tuple[str, str] | None, data: dict[str, Any]) -> bool:
    return cond is None or data.get(cond[0]) == cond[1]


def _clean_scalar(f: Field, value: Any, errors: dict[str, str]) -> Any:
    if f.type in ("text", "textarea", "hidden"):
        text = str(value).strip()
        if len(text) > f.max_length:
            errors[f.key] = f"最多 {f.max_length} 字"
        elif f.pattern and not re.fullmatch(f.pattern, text):
            errors[f.key] = "格式不正確"
        elif f.key == "national_id" and not is_valid_national_id(text):
            errors[f.key] = "身分證 / 居留證字號格式不正確"
        return text.upper() if f.key == "national_id" else text
    if f.type == "date":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            errors[f.key] = "日期格式須為 YYYY-MM-DD"
            return None
        if parsed > today_tw():
            errors[f.key] = "日期不能晚於今天"
        return parsed.isoformat()
    if f.type == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors[f.key] = "必須是數字"
            return None
        if (f.min_value is not None and number < f.min_value) or (
            f.max_value is not None and number > f.max_value
        ):
            errors[f.key] = f"必須介於 {f.min_value:g} 到 {f.max_value:g}"
        return int(number) if number.is_integer() else number
    if f.type == "select":
        if f.options_source:
            text = str(value).strip()
            if not text or len(text) > f.max_length:
                errors[f.key] = "選項不合法"
            return text
        if value not in f.option_values():
            errors[f.key] = "選項不合法"
        return value
    if f.type == "multi":
        if not isinstance(value, list) or any(v not in f.option_values() for v in value):
            errors[f.key] = "選項不合法"
            return []
        return list(dict.fromkeys(value))  # 去重、保留順序
    if f.type == "bool":
        if not isinstance(value, bool):
            errors[f.key] = "必須是 true / false"
        return bool(value)
    raise ValueError(f"unknown field type {f.type}")  # pragma: no cover


def _clean_list(f: Field, value: Any, errors: dict[str, str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors[f.key] = "必須是清單"
        return []
    if len(value) > f.max_items:
        errors[f.key] = f"最多 {f.max_items} 筆"
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors[f"{f.key}[{index}]"] = "格式不正確"
            continue
        row: dict[str, Any] = {}
        for sub in f.item_fields:
            raw = item.get(sub.key)
            if _is_blank(raw):
                if sub.required:
                    errors[f"{f.key}[{index}].{sub.key}"] = f"{sub.label}為必填"
                row[sub.key] = None
                continue
            sub_errors: dict[str, str] = {}
            row[sub.key] = _clean_scalar(sub, raw, sub_errors)
            for msg in sub_errors.values():
                errors[f"{f.key}[{index}].{sub.key}"] = msg
        cleaned.append(row)
    return cleaned


def validate_step(
    step: Step,
    payload: dict[str, Any],
    *,
    partial: bool = False,
    skip_required: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """驗證單一步驟。`partial=True`（存草稿）時不檢查必填，只檢查已填欄位格式。

    `skip_required`：這些欄位即使空白也不視為缺漏（例如身分證已加密儲存、前端只回傳遮罩）。
    回傳整理後的資料（只含此步驟的欄位）；有錯誤時 raise ValidationError，
    `details["fields"]` 為 {欄位 key: 錯誤訊息}。
    """
    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}
    merged = {**payload}
    for f in step.fields:
        raw = payload.get(f.key)
        visible = _condition_met(f.show_if, merged)
        required = (
            visible
            and f.key not in skip_required
            and (
                f.required or (f.required_if is not None and _condition_met(f.required_if, merged))
            )
        )

        if f.type == "multi" and f.none_flag and payload.get(f.none_flag) is True:
            cleaned[f.key] = []
            continue
        if _is_blank(raw):
            if required and not partial:
                if f.none_flag and payload.get(f.none_flag) is True:
                    cleaned[f.key] = []
                    continue
                errors[f.key] = f"{f.label}為必填"
            cleaned[f.key] = [] if f.type in ("list", "multi") else None
            continue

        if f.type == "list":
            # 勾了「無」就以「無」為準，忽略殘留的清單內容，避免存下矛盾資料
            if f.none_flag and payload.get(f.none_flag) is True:
                cleaned[f.key] = []
                continue
            cleaned[f.key] = _clean_list(f, raw, errors)
            if not partial and required and len(cleaned[f.key]) < max(f.min_items, 1):
                if not (f.none_flag and payload.get(f.none_flag) is True):
                    errors[f.key] = f"{f.label}至少需填 {max(f.min_items, 1)} 筆"
        else:
            cleaned[f.key] = _clean_scalar(f, raw, errors)
            if f.must_be_true and not partial and cleaned[f.key] is not True:
                errors[f.key] = "必須勾選同意才能送出"

    if errors:
        raise ValidationError(f"步驟 {step.number} 資料有誤", details={"fields": errors})
    return cleaned


def validate_all(data: dict[str, Any], *, skip_required: frozenset[str] = frozenset()) -> None:
    """送出前跨步驗證：每一步都必須通過完整（非 partial）驗證。"""
    missing: dict[str, str] = {}
    for step in INTAKE_STEPS:
        try:
            validate_step(step, data, skip_required=skip_required)
        except ValidationError as exc:
            missing.update(exc.details.get("fields", {}))
    if missing:
        raise ValidationError("表單尚未填寫完整", details={"fields": missing})


def public_schema() -> list[dict[str, Any]]:
    """給前端 / 後台的可序列化 schema（不含驗證細節）。"""

    def field_dict(f: Field) -> dict[str, Any]:
        return {
            "key": f.key,
            "label": f.label,
            "type": f.type,
            "required": f.required,
            "options": [{"value": v, "label": lbl} for v, lbl in f.options],
            "item_fields": [field_dict(sub) for sub in f.item_fields],
            "help": f.help,
            "show_if": list(f.show_if) if f.show_if else None,
            "none_flag": f.none_flag,
            "options_source": f.options_source,
        }

    return [
        {
            "number": s.number,
            "title": s.title,
            "description": s.description,
            "fields": [field_dict(f) for f in s.fields],
        }
        for s in INTAKE_STEPS
    ]
