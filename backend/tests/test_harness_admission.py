"""验证 M1 Sidecar 新 Run 准入的共享状态与 revision 乐观锁。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.routers.pixelflow_conversations import (
    _close_harness_admission_after_sidecar_failure,
)
from pixelflow.agent_harness.admission import (
    HarnessAdmissionClosedError,
    HarnessAdmissionConflictError,
    HarnessAdmissionService,
    SQLHarnessAdmissionRepository,
)
from pixelflow.platform.persistence import Base


@pytest.mark.asyncio
async def test_sql_admission_state_is_shared_and_revision_guarded(tmp_path) -> None:
    """两个 Repository 实例必须读同一状态，过期 revision 不得覆盖停流决定。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admission.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    first = SQLHarnessAdmissionRepository(factory)
    second = SQLHarnessAdmissionRepository(factory)
    try:
        initial = await first.initialize(initial_open=True, updated_by="gateway-a")
        assert (await second.require_open()).revision == initial.revision == 1

        closed = await second.update_state(
            open_for_new_runs=False,
            reason_code="sidecar_unavailable",
            expected_revision=initial.revision,
            updated_by="gateway-b",
        )
        assert closed.state == "closed"
        with pytest.raises(HarnessAdmissionClosedError):
            await first.require_open()
        with pytest.raises(HarnessAdmissionConflictError):
            await first.update_state(
                open_for_new_runs=True,
                reason_code="preflight_verified_open",
                expected_revision=initial.revision,
                updated_by="gateway-a",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sidecar_failure_closes_shared_admission(tmp_path) -> None:
    """创建 Run 失败后，后续 Gateway 必须读取到关闭状态而不是继续接流量。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLHarnessAdmissionRepository(factory)

    class _State:
        """模拟 FastAPI app.state，只提供本次闭环需要的 Repository。"""

        pixelflow_harness_admission_repository = repository

    class _App:
        """模拟最小 Gateway 应用对象。"""

        state = _State()

    class _Request:
        """模拟闭环函数所需的 Request.app。"""

        app = _App()

    try:
        initial = await repository.initialize(initial_open=True, updated_by="gateway-a")
        await _close_harness_admission_after_sidecar_failure(
            _Request(),
            expected_revision=initial.revision,
        )
        with pytest.raises(HarnessAdmissionClosedError):
            await repository.require_open()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_release_is_required_before_audited_admission_open(tmp_path) -> None:
    """预检失败不得开流；通过后必须经唯一 Service 和 revision 写入审计主体。"""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'preflight.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SQLHarnessAdmissionRepository(factory)
    service = HarnessAdmissionService(repository)
    try:
        initial = await repository.initialize(initial_open=False, updated_by="gateway-startup")

        async def rejected_preflight() -> None:
            raise RuntimeError("release_not_ready")

        with pytest.raises(RuntimeError, match="release_not_ready"):
            await service.open_after_verified_release(
                verify_release=rejected_preflight,
                updated_by="gateway-preflight:test",
            )
        assert (await repository.get()) == initial

        verified = False

        async def accepted_preflight() -> None:
            nonlocal verified
            verified = True

        opened = await service.open_after_verified_release(
            verify_release=accepted_preflight,
            updated_by="gateway-preflight:test",
        )
        assert verified is True
        assert opened.is_open is True
        assert opened.reason_code == "preflight_verified_open"
        assert opened.revision == initial.revision + 1
        assert opened.updated_by == "gateway-preflight:test"
    finally:
        await engine.dispose()
