"""Repository 共用基底：in-memory 泛型儲存，供尚未接上資料庫前使用。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class InMemoryStore(Generic[T]):
    """以 dict 模擬資料表，鍵值由 key_fn 決定。"""

    def __init__(self, key_fn: Callable[[T], str]) -> None:
        self._key_fn = key_fn
        self._rows: dict[str, T] = {}

    def get(self, key: str) -> T | None:
        return self._rows.get(key)

    def save(self, row: T) -> T:
        self._rows[self._key_fn(row)] = row
        return row

    def delete(self, key: str) -> None:
        self._rows.pop(key, None)

    def find_one(self, predicate: Callable[[T], bool]) -> T | None:
        return next((row for row in self._rows.values() if predicate(row)), None)

    def __iter__(self) -> Iterator[T]:
        return iter(list(self._rows.values()))

    def __len__(self) -> int:
        return len(self._rows)


def row_to_dict(row: object, *, exclude: tuple[str, ...] = ()) -> dict:
    """把 SQLAlchemy 模型實例轉成可 JSON 序列化的 dict（日期 → ISO 字串、Decimal → float）。"""
    from datetime import date, datetime
    from decimal import Decimal

    from sqlalchemy import inspect

    result: dict = {}
    for column in inspect(row).mapper.column_attrs:
        if column.key in exclude:
            continue
        value = getattr(row, column.key)
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        result[column.key] = value
    return result
