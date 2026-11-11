"""服藥提醒規則：處方頻率 / 飯前飯後 → 預設提醒時間點。

這裡是唯一來源：`ReminderService` 用它算預設時間、LIFF 頁面用 `public_rules()` 顯示選項，
處方辨識的結構化輸出也只允許這裡列出的代碼。
"""

from __future__ import annotations

from datetime import datetime, timedelta

# 預設用餐 / 睡前時間（v1 不做個人偏好，病人可在 LIFF 逐筆調整時間）
BREAKFAST = "08:00"
LUNCH = "12:00"
DINNER = "18:00"
BEDTIME = "22:00"
MEAL_OFFSET_MINUTES = 30  # 飯前提前 / 飯後延後

FREQUENCY_LABELS: dict[str, str] = {
    "qd": "每日 1 次",
    "bid": "每日 2 次",
    "tid": "每日 3 次",
    "qid": "每日 4 次",
    "q12h": "每 12 小時",
    "q8h": "每 8 小時",
    "q6h": "每 6 小時",
    "qw": "每週 1 次",
    "hs": "睡前",
    "prn": "需要時",
    "other": "其他",
}
TIMING_LABELS: dict[str, str] = {
    "ac": "飯前",
    "pc": "飯後",
    "hs": "睡前",
    "stat": "立即",
}
FREQUENCY_CODES = tuple(FREQUENCY_LABELS)
TIMING_CODES = tuple(TIMING_LABELS)

# 頻率 → 以哪幾餐為基準
_MEAL_SLOTS: dict[str, tuple[str, ...]] = {
    "qd": (BREAKFAST,),
    "bid": (BREAKFAST, DINNER),
    "tid": (BREAKFAST, LUNCH, DINNER),
    "qid": (BREAKFAST, LUNCH, DINNER, BEDTIME),
    "qw": (BREAKFAST,),
}
_INTERVAL_HOURS: dict[str, int] = {"q12h": 12, "q8h": 8, "q6h": 6}


def _shift(hhmm: str, minutes: int) -> str:
    base = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=minutes)
    return base.strftime("%H:%M")


def default_times(frequency_code: str, timing: str | None) -> list[str]:
    """回傳當日提醒時間點（HH:MM，台灣時間）。`prn` / `other` 回空清單，由病人自行設定。"""
    if frequency_code == "hs" or timing == "hs":
        return [BEDTIME]
    if frequency_code in _INTERVAL_HOURS:
        step = _INTERVAL_HOURS[frequency_code]
        return [_shift(BREAKFAST, i * step * 60) for i in range(24 // step)]
    slots = _MEAL_SLOTS.get(frequency_code)
    if not slots:
        return []
    if timing == "ac":
        return [_shift(t, -MEAL_OFFSET_MINUTES) for t in slots]
    if timing == "pc":
        return [_shift(t, MEAL_OFFSET_MINUTES) for t in slots]
    return list(slots)


def normalize_frequency(raw: str | None) -> str:
    """把辨識結果（BID / b.i.d. / 每日兩次…）正規化成代碼；認不出來回 `other`。"""
    if not raw:
        return "other"
    text = raw.strip().lower().replace(".", "").replace(" ", "")
    aliases = {
        "qd": ("qd", "od", "daily", "每日1次", "每日一次", "一天一次", "1次/日"),
        "bid": ("bid", "每日2次", "每日兩次", "一天兩次", "早晚", "2次/日"),
        "tid": ("tid", "每日3次", "每日三次", "一天三次", "三餐", "3次/日"),
        "qid": ("qid", "每日4次", "每日四次", "一天四次", "4次/日"),
        "q12h": ("q12h",),
        "q8h": ("q8h",),
        "q6h": ("q6h",),
        "qw": ("qw", "每週1次", "每週一次", "weekly"),
        "hs": ("hs", "qhs", "睡前"),
        "prn": ("prn", "需要時", "必要時"),
    }
    for code, names in aliases.items():
        if text in names:
            return code
    return "other"


def normalize_timing(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip().lower().replace(".", "")
    for code, names in {
        "ac": ("ac", "飯前", "before meals", "beforemeal"),
        "pc": ("pc", "飯後", "after meals", "aftermeal"),
        "hs": ("hs", "睡前", "bedtime"),
        "stat": ("stat", "立即"),
    }.items():
        if text in names:
            return code
    return None


def public_rules() -> dict:
    """給 LIFF 頁面：頻率 / 時機選項與預設時間。"""
    return {
        "frequencies": [{"code": c, "label": lbl} for c, lbl in FREQUENCY_LABELS.items()],
        "timings": [{"code": c, "label": lbl} for c, lbl in TIMING_LABELS.items()],
        "defaults": {"breakfast": BREAKFAST, "lunch": LUNCH, "dinner": DINNER, "bedtime": BEDTIME},
    }
