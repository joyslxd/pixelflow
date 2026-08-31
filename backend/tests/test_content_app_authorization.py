"""验证 Content-App 浏览器授权仅以 Gateway 内存短期租约存在。"""

from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.platform.content_app_authorization import (
    TransientContentAppAuthorizationStore,
)


@pytest.mark.asyncio
async def test_job_authorization_precedes_user_authorization_and_user_lease_recovers_job() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    store = TransientContentAppAuthorizationStore(
        ttl=timedelta(hours=1),
        clock=lambda: now,
    )
    await store.put_user(user_id="user", authorization="Bearer browser-current")
    await store.put_job(provider_job_id="task-1", authorization="Bearer browser-start")

    assert await store.borrow(provider_job_id="task-1", user_id="user") == "Bearer browser-start"

    await store.discard_job(provider_job_id="task-1")

    assert await store.borrow(provider_job_id="task-1", user_id="user") == "Bearer browser-current"


@pytest.mark.asyncio
async def test_authorization_store_refuses_missing_or_expired_leases() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    store = TransientContentAppAuthorizationStore(
        ttl=timedelta(seconds=1),
        clock=lambda: now,
    )

    with pytest.raises(LookupError, match="content_app_authorization_unavailable"):
        await store.borrow(provider_job_id="task-absent", user_id="user")

    await store.put_user(user_id="user", authorization="Bearer browser-current")
    now += timedelta(seconds=2)

    with pytest.raises(LookupError, match="content_app_authorization_unavailable"):
        await store.borrow(provider_job_id="task-expired", user_id="user")
