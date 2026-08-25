"""验证 M1 PixelFlow 自有 ORM Base 与数据库引擎生命周期。"""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from pixelflow.platform.persistence import Base, close_engine, ensure_schema, get_engine, get_session_factory, init_engine


class _PlatformPersistenceProbeRow(Base):
    """仅用于确认自有 Base metadata 与异步会话可正常配合。"""

    __tablename__ = "platform_persistence_probe"

    identifier: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


@pytest.mark.asyncio
async def test_platform_persistence_owns_sqlite_engine_and_metadata(tmp_path) -> None:
    """自有引擎必须能创建自有 Base 的表，并在关闭后清空全局引用。"""

    await init_engine(
        backend="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}",
        sqlite_dir=str(tmp_path),
    )
    engine = get_engine()
    factory = get_session_factory()
    assert engine is not None
    assert factory is not None
    try:
        await ensure_schema(engine)
        async with factory() as session:
            session.add(_PlatformPersistenceProbeRow(identifier=1, label="M1"))
            await session.commit()
        async with factory() as session:
            result = await session.execute(select(_PlatformPersistenceProbeRow))
            assert result.scalar_one().to_dict() == {"identifier": 1, "label": "M1"}
    finally:
        await close_engine()
    assert get_engine() is None
    assert get_session_factory() is None
