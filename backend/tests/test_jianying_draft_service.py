import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from pixelflow.jianying_draft.models import (
    JianyingDraftRequest,
    JianyingDraftResult,
    JianyingDraftScene,
    JianyingDraftStatus,
    compute_storyboard_version_id,
)
from pixelflow.jianying_draft.service import JianyingDraftService
from pixelflow.jianying_draft.skill import (
    JianyingDraftCapability,
    UnavailableJianyingDraftSkill,
)


def _request(number: int = 1) -> JianyingDraftRequest:
    scenes = [
        JianyingDraftScene(
            scene_id=f"scene-{number}",
            scene_index=1,
            task_id=f"video-task-{number}",
            video_url=f"https://cdn.example.com/{number}.mp4",
        )
    ]
    return JianyingDraftRequest(
        conversation_id=f"conversation-{number}",
        storyboard_version_id=compute_storyboard_version_id(scenes),
        scenes=scenes,
    )


async def _wait_for_terminal(
    service: JianyingDraftService, job_id: str
) -> JianyingDraftResult:
    for _ in range(100):
        result = await service.get_job(job_id)
        assert result is not None
        if result.status in {
            JianyingDraftStatus.SUCCEEDED,
            JianyingDraftStatus.FAILED,
            JianyingDraftStatus.TIMEOUT,
            JianyingDraftStatus.NOT_CONFIGURED,
        }:
            return result
        await asyncio.sleep(0.001)
    raise AssertionError("job did not complete")


class BlockingFakeSkill:
    def __init__(self) -> None:
        self.call_count = 0
        self.release = asyncio.Event()

    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=True)

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        self.call_count += 1
        await self.release.wait()
        return JianyingDraftResult(
            status=JianyingDraftStatus.SUCCEEDED,
            provider_task_id="provider-task",
            download_url="https://cdn.example.com/draft.zip",
        )


class ResultFakeSkill:
    def __init__(self, result: JianyingDraftResult) -> None:
        self.result = result
        self.call_count = 0

    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=True)

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        self.call_count += 1
        return self.result


class RaisingFakeSkill:
    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=True)

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        raise RuntimeError("provider secret diagnostic")


class RaisingCapabilityFakeSkill:
    def __init__(self) -> None:
        self.generate_count = 0

    async def capability(self) -> JianyingDraftCapability:
        raise RuntimeError("capability secret diagnostic")

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        self.generate_count += 1
        return JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED)


class CompletionOrderFakeSkill:
    def __init__(self) -> None:
        self._release_events: dict[int, asyncio.Event] = {}

    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=True)

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        number = int(request.conversation_id.rsplit("-", maxsplit=1)[-1])
        if number != 1 and number != 100:
            await self._release_events.setdefault(number, asyncio.Event()).wait()
        return JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED)

    def release(self, number: int) -> None:
        self._release_events.setdefault(number, asyncio.Event()).set()


class ToggleCapabilityBlockingFakeSkill(BlockingFakeSkill):
    def __init__(self) -> None:
        super().__init__()
        self.capability_call_count = 0
        self.capability_mode = "available"
        self.reason = ""

    async def capability(self) -> JianyingDraftCapability:
        self.capability_call_count += 1
        if self.capability_mode == "raise":
            raise RuntimeError("capability secret diagnostic")
        return JianyingDraftCapability(
            available=self.capability_mode == "available",
            reason=self.reason,
        )


class ToggleCapabilityResultFakeSkill(ResultFakeSkill):
    def __init__(self, result: JianyingDraftResult) -> None:
        super().__init__(result)
        self.capability_call_count = 0
        self.capability_available = True
        self.reason = ""

    async def capability(self) -> JianyingDraftCapability:
        self.capability_call_count += 1
        return JianyingDraftCapability(
            available=self.capability_available,
            reason=self.reason,
        )


class FailThenBlockingFakeSkill:
    def __init__(self) -> None:
        self.call_count = 0
        self.release = asyncio.Event()

    async def capability(self) -> JianyingDraftCapability:
        return JianyingDraftCapability(available=True)

    async def generate(self, request: JianyingDraftRequest) -> JianyingDraftResult:
        self.call_count += 1
        if self.call_count == 1:
            return JianyingDraftResult(
                status=JianyingDraftStatus.FAILED,
                message="provider failure",
            )
        await self.release.wait()
        return JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED)


def test_default_timeout_is_thirty_minutes():
    service = JianyingDraftService(
        skill=ResultFakeSkill(JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED))
    )

    assert service._timeout_seconds == 1800.0


@pytest.mark.asyncio
async def test_unavailable_skill_does_not_create_job():
    service = JianyingDraftService(skill=UnavailableJianyingDraftSkill())

    result = await service.start(_request())

    assert result.status == JianyingDraftStatus.NOT_CONFIGURED
    assert result.message == "剪映草稿服务待接入"
    assert service.job_count == 0


@pytest.mark.asyncio
async def test_unavailable_capability_uses_fixed_public_message(caplog):
    skill = ToggleCapabilityResultFakeSkill(
        JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED)
    )
    skill.capability_available = False
    skill.reason = "https://provider.example.com/?token=secret-token"
    service = JianyingDraftService(skill=skill)

    result = await service.start(_request())

    assert result.status == JianyingDraftStatus.NOT_CONFIGURED
    assert result.message == "剪映草稿服务待接入"
    assert "secret-token" not in result.message
    assert "secret-token" not in caplog.text
    assert service.job_count == 0


@pytest.mark.asyncio
async def test_capability_exception_returns_public_not_configured_without_job(caplog):
    skill = RaisingCapabilityFakeSkill()
    service = JianyingDraftService(skill=skill)
    caplog.set_level(logging.ERROR, logger="pixelflow.jianying_draft.service")

    result = await service.start(_request())

    assert result.status == JianyingDraftStatus.NOT_CONFIGURED
    assert result.message == "剪映草稿服务暂不可用，请稍后重试"
    assert service.job_count == 0
    assert skill.generate_count == 0
    assert "RuntimeError" in caplog.text
    assert "capability secret diagnostic" not in caplog.text


@pytest.mark.asyncio
async def test_running_job_is_reused_for_same_version():
    skill = BlockingFakeSkill()
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    second = await service.start(_request())
    await asyncio.sleep(0)

    assert first.job_id is not None
    assert second.job_id == first.job_id
    assert skill.call_count == 1
    skill.release.set()
    await _wait_for_terminal(service, first.job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unavailable", "raise"])
async def test_running_job_reuse_does_not_probe_changed_capability(mode: str):
    skill = ToggleCapabilityBlockingFakeSkill()
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    assert first.job_id is not None
    await asyncio.sleep(0)
    skill.capability_mode = mode
    skill.reason = "https://provider.example.com/?token=secret-token"
    reused = await service.start(_request())

    assert reused.job_id == first.job_id
    assert skill.capability_call_count == 1
    skill.release.set()
    await _wait_for_terminal(service, first.job_id)


@pytest.mark.asyncio
async def test_succeeded_job_is_reused_for_same_version():
    skill = ResultFakeSkill(
        JianyingDraftResult(
            status=JianyingDraftStatus.SUCCEEDED,
            provider_task_id="provider-task",
            download_url="https://cdn.example.com/draft.zip",
        )
    )
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    assert first.job_id is not None
    completed = await _wait_for_terminal(service, first.job_id)
    second = await service.start(_request())

    assert completed.status == JianyingDraftStatus.SUCCEEDED
    assert second == completed
    assert skill.call_count == 1


@pytest.mark.asyncio
async def test_succeeded_job_reuse_does_not_probe_changed_capability():
    skill = ToggleCapabilityResultFakeSkill(
        JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED)
    )
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    assert first.job_id is not None
    completed = await _wait_for_terminal(service, first.job_id)
    skill.capability_available = False
    skill.reason = "https://provider.example.com/?token=secret-token"
    reused = await service.start(_request())

    assert reused == completed
    assert skill.capability_call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JianyingDraftStatus.FAILED, JianyingDraftStatus.TIMEOUT])
async def test_terminal_failure_requires_explicit_retry(
    status: JianyingDraftStatus,
):
    skill = ResultFakeSkill(JianyingDraftResult(status=status, message="公开失败原因"))
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    assert first.job_id is not None
    completed = await _wait_for_terminal(service, first.job_id)
    reused = await service.start(_request())
    retried = await service.start(_request(), retry_failed=True)

    assert completed.status == status
    assert reused.job_id == first.job_id
    assert retried.job_id is not None
    assert retried.job_id != first.job_id
    replaced = await service.get_job(first.job_id)
    assert replaced is not None
    assert "replaced_by_job_id" not in replaced.model_dump()
    assert await service._get_replaced_by_job_id(first.job_id) == retried.job_id
    await _wait_for_terminal(service, retried.job_id)
    assert skill.call_count == 2


@pytest.mark.asyncio
async def test_expired_succeeded_job_creates_a_new_job():
    skill = ResultFakeSkill(
        JianyingDraftResult(
            status=JianyingDraftStatus.SUCCEEDED,
            expire_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request())
    assert first.job_id is not None
    await _wait_for_terminal(service, first.job_id)
    replacement = await service.start(_request())

    assert replacement.job_id is not None
    assert replacement.job_id != first.job_id
    await _wait_for_terminal(service, replacement.job_id)
    assert skill.call_count == 2


@pytest.mark.asyncio
async def test_timeout_is_terminal():
    service = JianyingDraftService(skill=BlockingFakeSkill(), timeout_seconds=0.01)

    started = await service.start(_request())
    assert started.job_id is not None
    result = await _wait_for_terminal(service, started.job_id)

    assert result.status == JianyingDraftStatus.TIMEOUT


@pytest.mark.asyncio
async def test_background_exception_becomes_public_failure():
    service = JianyingDraftService(skill=RaisingFakeSkill())

    started = await service.start(_request())
    assert started.job_id is not None
    result = await _wait_for_terminal(service, started.job_id)

    assert result.status == JianyingDraftStatus.FAILED
    assert result.message == "剪映草稿生成失败，请稍后重试"
    assert "secret" not in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_message"),
    [
        (JianyingDraftStatus.FAILED, "剪映草稿生成失败，请稍后重试。"),
        (JianyingDraftStatus.TIMEOUT, "剪映草稿生成超时，请重试。"),
        (JianyingDraftStatus.NOT_CONFIGURED, "剪映草稿服务待接入"),
    ],
)
async def test_provider_terminal_messages_are_replaced_with_public_messages(
    status: JianyingDraftStatus,
    expected_message: str,
):
    skill = ResultFakeSkill(
        JianyingDraftResult(
            status=status,
            message="https://provider.example.com/?token=secret-token",
            provider_task_id="provider-task-secret-token",
            download_url="https://provider.example.com/draft.zip?token=secret-token",
            file_name="secret-token-draft.zip",
            expire_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = JianyingDraftService(skill=skill)

    started = await service.start(_request())
    assert started.job_id is not None
    result = await _wait_for_terminal(service, started.job_id)

    assert result.status == status
    assert result.message == expected_message
    assert result.provider_task_id is None
    assert result.download_url is None
    assert result.file_name is None
    assert result.expire_at is None
    assert "replaced_by_job_id" not in result.model_dump()
    assert "secret-token" not in result.model_dump_json()
    assert "provider.example.com" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_succeeded_provider_result_keeps_download_fields():
    expire_at = datetime.now(UTC) + timedelta(hours=1)
    skill = ResultFakeSkill(
        JianyingDraftResult(
            status=JianyingDraftStatus.SUCCEEDED,
            provider_task_id="provider-task",
            download_url="https://cdn.example.com/draft.zip",
            file_name="draft.zip",
            expire_at=expire_at,
        )
    )
    service = JianyingDraftService(skill=skill)

    started = await service.start(_request())
    assert started.job_id is not None
    result = await _wait_for_terminal(service, started.job_id)

    assert result.provider_task_id == "provider-task"
    assert str(result.download_url) == "https://cdn.example.com/draft.zip"
    assert result.file_name == "draft.zip"
    assert result.expire_at == expire_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [JianyingDraftStatus.QUEUED, JianyingDraftStatus.RUNNING],
)
async def test_provider_non_terminal_result_becomes_retryable_public_failure(
    status: JianyingDraftStatus,
):
    skill = ResultFakeSkill(
        JianyingDraftResult(
            status=status,
            message="https://provider.example.com/?token=secret-token",
            provider_task_id="provider-task-secret-token",
            download_url="https://provider.example.com/draft.zip?token=secret-token",
            file_name="secret-token-draft.zip",
            expire_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = JianyingDraftService(skill=skill)

    started = await service.start(_request())
    assert started.job_id is not None
    result = await _wait_for_terminal(service, started.job_id)
    retried = await service.start(_request(), retry_failed=True)

    assert result.status == JianyingDraftStatus.FAILED
    assert result.message == "剪映草稿生成失败，请稍后重试。"
    assert result.provider_task_id is None
    assert result.download_url is None
    assert result.file_name is None
    assert result.expire_at is None
    assert "secret-token" not in result.model_dump_json()
    assert service._jobs[started.job_id].completed_at is not None
    assert retried.job_id is not None
    assert retried.job_id != started.job_id


@pytest.mark.asyncio
async def test_terminal_jobs_are_pruned_to_one_hundred():
    skill = ResultFakeSkill(JianyingDraftResult(status=JianyingDraftStatus.SUCCEEDED))
    service = JianyingDraftService(skill=skill)

    job_ids = []
    for number in range(101):
        started = await service.start(_request(number))
        assert started.job_id is not None
        job_ids.append(started.job_id)
        await _wait_for_terminal(service, started.job_id)

    assert service.job_count == 100
    assert await service.get_job(job_ids[0]) is None
    assert await service.get_job(job_ids[-1]) is not None


@pytest.mark.asyncio
async def test_terminal_job_pruning_uses_completion_order_not_creation_order():
    skill = CompletionOrderFakeSkill()
    service = JianyingDraftService(skill=skill)

    first = await service.start(_request(0))
    second = await service.start(_request(1))
    running_jobs = [await service.start(_request(number)) for number in range(2, 100)]
    assert first.job_id is not None
    assert second.job_id is not None
    await _wait_for_terminal(service, second.job_id)
    skill.release(0)
    await _wait_for_terminal(service, first.job_id)

    replacement = await service.start(_request(100))
    assert replacement.job_id is not None
    await _wait_for_terminal(service, replacement.job_id)

    assert await service.get_job(second.job_id) is None
    assert await service.get_job(first.job_id) is not None
    for number in range(2, 100):
        skill.release(number)
    await asyncio.gather(
        *(
            _wait_for_terminal(service, job.job_id)
            for job in running_jobs
            if job.job_id is not None
        )
    )


@pytest.mark.asyncio
async def test_full_running_job_store_does_not_remove_running_jobs():
    skill = BlockingFakeSkill()
    service = JianyingDraftService(skill=skill)

    jobs = await asyncio.gather(*(service.start(_request(number)) for number in range(100)))
    await asyncio.sleep(0)
    refused = await service.start(_request(101))

    assert service.job_count == 100
    assert refused.status == JianyingDraftStatus.FAILED
    assert refused.job_id is None
    assert all(job.job_id is not None for job in jobs)
    stored_jobs = await asyncio.gather(
        *(service.get_job(job.job_id) for job in jobs if job.job_id is not None)
    )
    assert all(stored_job is not None for stored_job in stored_jobs)
    skill.release.set()
    await asyncio.gather(
        *(_wait_for_terminal(service, job.job_id) for job in jobs if job.job_id is not None)
    )


@pytest.mark.asyncio
async def test_retry_reuses_its_failed_job_slot_when_store_is_full():
    skill = FailThenBlockingFakeSkill()
    service = JianyingDraftService(skill=skill)

    failed = await service.start(_request(1))
    assert failed.job_id is not None
    failed_result = await _wait_for_terminal(service, failed.job_id)
    running_jobs = [await service.start(_request(number)) for number in range(2, 101)]
    await asyncio.sleep(0)
    retried = await service.start(_request(1), retry_failed=True)

    assert service.job_count == 100
    assert retried.job_id is not None
    assert retried.job_id != failed.job_id
    old_result = await service.get_job(failed.job_id)
    assert old_result == failed_result
    assert "replaced_by_job_id" not in old_result.model_dump()
    assert await service._get_replaced_by_job_id(failed.job_id) == retried.job_id
    skill.release.set()
    await _wait_for_terminal(service, retried.job_id)
    await asyncio.gather(
        *(
            _wait_for_terminal(service, job.job_id)
            for job in running_jobs
            if job.job_id is not None
        )
    )


@pytest.mark.asyncio
async def test_concurrent_start_does_not_create_duplicate_jobs():
    skill = BlockingFakeSkill()
    service = JianyingDraftService(skill=skill)

    results = await asyncio.gather(*(service.start(_request()) for _ in range(20)))
    await asyncio.sleep(0)

    assert service.job_count == 1
    assert len({result.job_id for result in results}) == 1
    assert skill.call_count == 1
    skill.release.set()
    job_id = results[0].job_id
    assert job_id is not None
    await _wait_for_terminal(service, job_id)
