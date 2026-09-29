"""急診動態分流的規則單一來源：地區代碼、壅塞燈號門檻、排序分數、鄰近縣市。

資料來源是健保署「急診即時訊息」API（重度級急救責任醫院，每 15 分鐘更新），
欄位可靠性依 Nhi-er-open-data 專案的整理：等住院 / 等 ICU / 119 滿床通報可信，
「推床數」各院填報標準不一，**不列入燈號判斷**。

燈號門檻依醫院層級不同：同樣「等住院 30 人」對醫學中心是日常、對地區醫院已是極限。
"""

from __future__ import annotations

from typing import Any

# 健保署地區代碼 → 縣市（「臺」統一為「台」，與 PharmacyService.parse_address 一致）
AREA_NAMES: dict[str, str] = {
    "01": "台北市",
    "03": "台中市",
    "05": "台南市",
    "07": "高雄市",
    "11": "基隆市",
    "12": "新竹市",
    "22": "嘉義市",
    "31": "新北市",
    "32": "桃園市",
    "33": "新竹縣",
    "34": "宜蘭縣",
    "35": "苗栗縣",
    "37": "彰化縣",
    "38": "南投縣",
    "39": "雲林縣",
    "40": "嘉義縣",
    "43": "屏東縣",
    "44": "澎湖縣",
    "45": "花蓮縣",
    "46": "台東縣",
    "90": "金門縣",
    "91": "連江縣",
}
AREA_CODES: dict[str, str] = {name: code for code, name in AREA_NAMES.items()}

# LINE 選縣市用的分組（每組一張 Flex bubble）
AREA_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("北部", ("01", "31", "11", "32", "12", "33", "34")),
    ("中部", ("35", "03", "37", "38", "39")),
    ("南部", ("22", "40", "05", "07", "43")),
    ("東部 / 離島", ("45", "46", "44", "90", "91")),
)

# conT_TYPE：1=醫學中心、2=區域醫院、3=地區醫院
TIER_LABELS: dict[str, str] = {"1": "醫學中心", "2": "區域醫院", "3": "地區醫院"}

LEVEL_NORMAL, LEVEL_BUSY, LEVEL_CRITICAL = "normal", "busy", "critical"
LEVELS: tuple[str, ...] = (LEVEL_NORMAL, LEVEL_BUSY, LEVEL_CRITICAL)
LEVEL_LABELS: dict[str, str] = {
    LEVEL_NORMAL: "尚有餘裕",
    LEVEL_BUSY: "偏忙",
    LEVEL_CRITICAL: "壅塞",
}
LEVEL_EMOJI: dict[str, str] = {LEVEL_NORMAL: "🟢", LEVEL_BUSY: "🟡", LEVEL_CRITICAL: "🔴"}
LEVEL_RANK: dict[str, int] = {LEVEL_NORMAL: 0, LEVEL_BUSY: 1, LEVEL_CRITICAL: 2}

# 等住院人數門檻 (偏忙, 壅塞)，依層級。參考 Nhi-er-open-data 的觀察：
# 大型醫學中心等住院 ≥ 70–100 才算「爆」、區域醫院 ≥ 20；
# 同樣 30 人對林口長庚是日常、對地區醫院已是極限。
WAIT_ADMIT_THRESHOLDS: dict[str, tuple[int, int]] = {
    "1": (35, 70),
    "2": (10, 20),
    "3": (5, 10),
}
WAIT_ICU_THRESHOLDS: tuple[int, int] = (3, 5)  # 等 ICU（不分層級）；ICU 壅塞是轉診困難的主因
WAIT_SEE_THRESHOLDS: tuple[int, int] = (20, 40)  # 等看診
# 壅塞倍數（目前等住院 ÷ 24 小時平均）≥ 這個值視為明顯惡化（只做提示，不影響燈號）
WORSENING_RATIO = 1.5

DATA_SOURCE_NOTE = "資料來源：健保署急診即時訊息（約每 15 分鐘更新）；推床數不列入判斷。"


def congestion_level(
    *, hosp_type: str, full_119: bool, wait_admit: int, wait_icu: int, wait_see: int
) -> str:
    """依層級門檻算出燈號。119 滿床通報一律壅塞。"""
    if full_119:
        return LEVEL_CRITICAL
    admit_busy, admit_crit = WAIT_ADMIT_THRESHOLDS.get(hosp_type, WAIT_ADMIT_THRESHOLDS["2"])
    icu_busy, icu_crit = WAIT_ICU_THRESHOLDS
    see_busy, see_crit = WAIT_SEE_THRESHOLDS
    if wait_admit >= admit_crit or wait_icu >= icu_crit or wait_see >= see_crit:
        return LEVEL_CRITICAL
    if wait_admit >= admit_busy or wait_icu >= icu_busy or wait_see >= see_busy:
        return LEVEL_BUSY
    return LEVEL_NORMAL


def congestion_score(*, wait_admit: int, wait_icu: int, wait_see: int) -> float:
    """同燈號內的排序分數（越低越有餘裕）：等 ICU 權重最高、等看診最低。"""
    return wait_admit + 3 * wait_icu + 0.5 * wait_see


def decorate(row: dict[str, Any], avg_24h: float | None = None) -> dict[str, Any]:
    """把一筆快照補上燈號 / 分數 / 層級中文 / 壅塞倍數，供 API、LINE、後台共用。"""
    level = congestion_level(
        hosp_type=row["hosp_type"],
        full_119=bool(row["full_119"]),
        wait_admit=row["wait_admit"],
        wait_icu=row["wait_icu"],
        wait_see=row["wait_see"],
    )
    ratio = round(row["wait_admit"] / avg_24h, 2) if avg_24h and avg_24h >= 5 else None
    return {
        **row,
        "area_name": AREA_NAMES.get(row["area_code"], row["area_code"]),
        "tier_label": TIER_LABELS.get(row["hosp_type"], "重度級急救責任醫院"),
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "score": congestion_score(
            wait_admit=row["wait_admit"], wait_icu=row["wait_icu"], wait_see=row["wait_see"]
        ),
        "avg_admit_24h": round(avg_24h, 1) if avg_24h is not None else None,
        "ratio_24h": ratio,
        "worsening": bool(ratio and ratio >= WORSENING_RATIO),
    }


def sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    return (LEVEL_RANK[row["level"]], row["score"], row["hospital_name"])


# 找附近診所的科別對應：衛福部「醫療機構與人員基本資料」的科別用詞 ← 模型 / 使用者可能的說法。
# 家醫科與內科的問題，掛「西醫一般科」的診所也能看，一併納入。
DEPARTMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "家庭醫學科": ("家醫科", "家醫", "家庭醫學"),
    "內科": (
        "一般內科",
        "心臟內科",
        "胸腔內科",
        "腸胃科",
        "胃腸科",
        "肝膽腸胃科",
        "新陳代謝科",
        "內分泌科",
        "腎臟科",
        "感染科",
        "風濕免疫科",
        "血液腫瘤科",
    ),
    "兒科": ("小兒科",),
    "神經科": ("神經內科",),
    "外科": ("一般外科", "大腸直腸外科", "胸腔外科", "心臟血管外科"),
    "耳鼻喉科": ("耳鼻喉",),
    "精神科": ("身心科", "精神醫學科", "身心醫學科"),
    "牙醫一般科": ("牙科", "牙醫"),
    "中醫一般科": ("中醫",),
    "急診醫學科": ("急診",),
    "西醫一般科": ("一般科",),
}
GENERAL_PRACTICE = ("西醫一般科",)
GENERAL_PRACTICE_FOR = ("家庭醫學科", "內科")


def department_terms(text: str | None) -> tuple[str, ...]:
    """AI 建議的科別 → 院所主檔可比對的科別名稱（可能多個）。認不得的原樣回傳。"""
    query = (text or "").strip().replace("臺", "台")
    if not query:
        return ()
    canonical = next(
        (
            name
            for name, aliases in DEPARTMENT_ALIASES.items()
            if query == name or query in aliases or any(a in query for a in aliases)
        ),
        None,
    )
    if canonical is None:
        if query.endswith("內科"):
            canonical = "內科"
        elif query.endswith("外科"):
            canonical = "外科"
        else:
            return (query,)
    terms = (canonical,)
    if canonical in GENERAL_PRACTICE_FOR:
        terms += GENERAL_PRACTICE
    return terms


def public_rules() -> dict[str, Any]:
    """給 LIFF 看板 / 後台顯示門檻說明。"""
    return {
        "areas": AREA_NAMES,
        "area_groups": [{"label": label, "codes": list(codes)} for label, codes in AREA_GROUPS],
        "tiers": TIER_LABELS,
        "levels": LEVEL_LABELS,
        "wait_admit_thresholds": {k: list(v) for k, v in WAIT_ADMIT_THRESHOLDS.items()},
        "wait_icu_thresholds": list(WAIT_ICU_THRESHOLDS),
        "wait_see_thresholds": list(WAIT_SEE_THRESHOLDS),
        "note": DATA_SOURCE_NOTE,
    }
