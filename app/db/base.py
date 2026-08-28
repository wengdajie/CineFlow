"""ORM 基类与公共字段。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """当前 UTC 时间（naive，便于跨库存储）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """声明式基类。"""


class TimestampMixin:
    """创建/更新时间。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class IdMixin:
    """自增主键。"""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
