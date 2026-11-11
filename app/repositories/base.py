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


def paginate(stmt, page: int, per_page: int) -> tuple[list[dict], int]:
    """後台表格分頁：回傳 (該頁列, 符合條件的總筆數)。stmt 需已含 where / order_by。"""
    from app.extensions import db

    total = db.session.execute(
        db.select(db.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.session.execute(stmt.offset((page - 1) * per_page).limit(per_page)).scalars()
    return [row_to_dict(r) for r in rows], int(total)
