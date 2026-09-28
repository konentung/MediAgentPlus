"""地理工具：經緯度距離（Haversine）。不依賴任何地圖服務。"""

from __future__ import annotations

import math
from typing import Any

EARTH_RADIUS_KM = 6371.0


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


def format_distance(km: float | None) -> str:
    if km is None:
        return ""
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"
