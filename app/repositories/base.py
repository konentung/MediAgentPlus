"""Repository 共用工具。"""

from __future__ import annotations


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
