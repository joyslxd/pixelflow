"""定义 PixelFlow ORM 的统一 Declarative Base。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """PixelFlow 领域表的 ORM 基类，提供稳定调试序列化而不依赖 DeerFlow。"""

    def to_dict(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        """返回当前 ORM 行的列值字典，调用方必须自行处理敏感字段。"""

        excluded = exclude or set()
        return {
            column.key: getattr(self, column.key)
            for column in sa_inspect(type(self)).mapper.column_attrs
            if column.key not in excluded
        }

    def __repr__(self) -> str:
        """返回只用于本地诊断的列值表示，生产日志不得输出敏感实体。"""

        columns = ", ".join(
            f"{column.key}={getattr(self, column.key)!r}"
            for column in sa_inspect(type(self)).mapper.column_attrs
        )
        return f"{type(self).__name__}({columns})"


__all__ = ["Base"]
