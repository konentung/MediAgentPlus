"""地理工具：經緯度距離（Haversine）、座標 → 鄉鎮市區。不依賴任何地圖服務。"""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from importlib import resources
from typing import Any

EARTH_RADIUS_KM = 6371.0
# 座標離最近的鄉鎮市區中心點超過這個距離視為不在台灣（例如國外或海上）
DISTRICT_MAX_KM = 30.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """兩點大圓距離（公里）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def parse_coords(lat: Any, lng: Any) -> tuple[float, float] | None:
    """字串 / 數字 → (lat, lng)；不合法或超出台灣附近範圍回 None。"""
    try:
        lat_f, lng_f = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None
    return lat_f, lng_f


@lru_cache(maxsize=1)
def district_centroids() -> tuple[tuple[str, str, float, float], ...]:
    """全台 368 個鄉鎮市區的面積中心點 (city, district, lat, lng)，啟動後只讀一次。

    來源：內政部行政區界（經 g0v/twgeojson 轉檔）計算多邊形重心；縣市名一律「台」。
    """
    path = resources.files("app.resources").joinpath("tw_districts.csv")
    with path.open(encoding="utf-8") as fh:
        return tuple(
            (row["city"], row["district"], float(row["lat"]), float(row["lng"]))
            for row in csv.DictReader(fh)
        )


@lru_cache(maxsize=1)
def districts_by_city() -> dict[str, tuple[str, ...]]:
    """縣市 → 鄉鎮市區清單（後台篩選下拉選單用）；鄉鎮市區依名稱排序。"""
    grouped: dict[str, list[str]] = {}
    for city, district, _, _ in district_centroids():
        grouped.setdefault(city, []).append(district)
    return {city: tuple(sorted(names)) for city, names in grouped.items()}


def nearest_district(
    lat: float, lng: float, *, max_km: float = DISTRICT_MAX_KM
) -> tuple[str, str] | None:
    """座標 → 最近的 (縣市, 鄉鎮市區)。

    用中心點最近鄰近似，邊界附近可能落到隔壁區；呼叫端本來就會「同區沒有就退到同縣市」，
    可以容忍。超過 `max_km` 回 None。
    """
    best: tuple[float, str, str] | None = None
    for city, district, c_lat, c_lng in district_centroids():
        km = haversine_km(lat, lng, c_lat, c_lng)
        if best is None or km < best[0]:
            best = (km, city, district)
    if best is None or best[0] > max_km:
        return None
    return best[1], best[2]


def district_centroid(city: str | None, district: str | None) -> tuple[float, float] | None:
    """(縣市, 鄉鎮市區) → 該區中心點座標；清單沒有回 None。院所沒有座標時用來估距離。"""
    if not city or not district:
        return None
    city = city.replace("臺", "台")
    for c, d, lat, lng in district_centroids():
        if c == city and d == district:
            return lat, lng
    return None


def format_distance(km: float | None) -> str:
    if km is None:
        return ""
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"
