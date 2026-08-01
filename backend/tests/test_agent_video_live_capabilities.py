from __future__ import annotations

import asyncio
import copy
import gc
import json
import pickle
import subprocess
import sys
import warnings
import weakref
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, model_serializer

from pixelflow.agent_runtime.contracts import WorkflowStatus
from pixelflow.agent_workflows.video import (
    VideoPlanningWorkflowService,
    VideoScenePackageWorkflowService,
)
from pixelflow.agent_workflows.video.live_capabilities import (
    DefaultVideoLiveCapabilities,
    TransientTurnCredential,
)
from pixelflow.creative.asset_manifest import normalize_asset_manifest
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.intake.forms import draft_creative_directions, validate_form
from pixelflow.skills import ImageGenerationResult

VIDEO_FORM = {
    "product_info": "AuroraFit 智能健康戒指",
    "product_category": "数码3C",
    "target_audience": "25-35 岁健康管理人群",
    "conversion_goal": "引流直播间",
    "video_duration_sec": 30,
    "video_ratio": "9:16",
    "video_model_mode": "system_recommended",
    "video_model": "seedance-2.0",
    "video_model_capabilities": {
        "generation_types": ["text_to_video", "image_to_video"],
        "upload_file_types": ["image"],
        "aspect_ratios": ["9:16", "16:9"],
        "sizes": ["1080p"],
        "sound_options": ["on", "off"],
        "durations_sec": [4, 5, 6, 8, 10, 12, 15],
    },
    "video_size": "1080p",
    "video_sound": "on",
    "image_model": "gpt-image-2",
    "image_model_capabilities": {
        "aspect_ratios": ["1:1", "16:9", "9:16"],
        "sizes": ["1080p", "2K", "4K"],
    },
    "video_usage": "新品宣传",
    "visual_style": "电影写实风",
}
MATERIALS = [
    {
        "type": "image",
        "url": "https://materials.example.com/ring.png",
        "name": "智能戒指参考图.png",
    }
]


class _TimeoutModel:
    def invoke(self, _prompt: object) -> object:
        raise TimeoutError("测试模型按约定触发确定性降级")


class _MemorySearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query_values, categories):
        self.calls.append({"query_values": list(query_values), "categories": list(categories)})
        return []


class _MemoryRecord:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record_background(self, *, summary, category, metadata):
        self.records.append({"summary": summary, "category": category, "metadata": dict(metadata)})


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


class _FailingClock:
    def now(self) -> datetime:
        raise RuntimeError("Bearer turn-secret 时钟异常详情")


class _FailingMemorySearch:
    async def search(self, *, query_values, categories):
        raise RuntimeError("Bearer turn-secret 搜索异常详情")


class _FailingMemoryRecord:
    def record_background(self, *, summary, category, metadata):
        raise RuntimeError("Bearer turn-secret 记录异常详情")


class _SlowMemoryRecord:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.calls = 0

    def record_background(self, *, summary, category, metadata):
        self.calls += 1
        self.task = asyncio.create_task(self._wait_for_release())

    async def _wait_for_release(self) -> None:
        await self.release.wait()


class _AsyncMemoryRecord:
    def __init__(self) -> None:
        self.executed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def record_background(self, *, summary, category, metadata):
        self.executed = True
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.finished.set()


class _FailingAsyncMemoryRecord:
    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def record_background(self, *, summary, category, metadata):
        self.finished.set()
        raise RuntimeError("Bearer turn-secret 后台记录异常详情")


class _FutureMemoryRecord:
    def __init__(self) -> None:
        self.future = asyncio.get_running_loop().create_future()

    def record_background(self, *, summary, category, metadata):
        return self.future


class _CustomAwaitableMemoryRecord:
    def __init__(self) -> None:
        self.finished = asyncio.Event()
        self.close_calls = 0

    def record_background(self, *, summary, category, metadata):
        return self

    def __await__(self):
        async def run() -> None:
            self.finished.set()

        return run().__await__()

    async def close(self) -> None:
        self.close_calls += 1


class _InvalidMemoryRecord:
    def record_background(self, *, summary, category, metadata):
        return "invalid-schedule-result"


class _SceneAssetSkill:
    async def text_to_image(self, **kwargs):
        from app.gateway.content_app_auth_context import require_current_authorization

        assert require_current_authorization() == "Bearer turn-secret"
        prompt = str(kwargs["prompt"])
        suffix = "character" if "三视图" in prompt else "scene" if "场景" in prompt else "prop"
        return ImageGenerationResult(
            ok=True,
            images=[{"url": f"https://assets.example.com/{suffix}.png"}],
            raw={"provider": "fake"},
        )

    async def reference_image(self, **kwargs):
        return await self.text_to_image(**kwargs)


class _FailingSceneAssetSkill:
    async def text_to_image(self, **_kwargs):
        raise RuntimeError("Bearer turn-secret 供应商原始失败")

    async def reference_image(self, **_kwargs):
        raise RuntimeError("Bearer turn-secret 供应商原始失败")


class _RawFailureSceneAssetSkill:
    def __init__(self, raw: object) -> None:
        self._raw = raw

    async def text_to_image(self, **_kwargs):
        return ImageGenerationResult(
            ok=False,
            error="固定业务失败",
            raw=self._raw,  # type: ignore[arg-type]
        )

    async def reference_image(self, **_kwargs):
        return await self.text_to_image(**_kwargs)


@dataclass(frozen=True, slots=True)
class _SafeRawDto:
    delivery_url: str
    business_token_count: int


@dataclass(frozen=True, slots=True)
class _UnsafeRawDto:
    accessToken: str


class _BrokenRawDto(BaseModel):
    detail: str

    def model_dump(self, *args, **kwargs):
        raise RuntimeError(f"{self.detail} DTO 转换异常")


class _SideEffectRawDto(BaseModel):
    detail: str
    serializer_calls: ClassVar[int] = 0

    @model_serializer
    def serialize_model(self) -> dict[str, str]:
        type(self).serializer_calls += 1
        return {"detail": self.detail}


class _SideEffectMapping(Mapping[str, object]):
    def __init__(self) -> None:
        self.calls = 0

    def __getitem__(self, key: str) -> object:
        self.calls += 1
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        self.calls += 1
        return iter(())

    def __len__(self) -> int:
        return 0


class _SideEffectDict(dict[str, object]):
    def __init__(self) -> None:
        super().__init__({"safe": "value"})
        self.calls = 0

    def items(self):
        self.calls += 1
        return super().items()


class _SideEffectList(list[object]):
    def __init__(self) -> None:
        super().__init__(["safe"])
        self.calls = 0

    def __iter__(self):
        self.calls += 1
        return super().__iter__()


class _SideEffectStr(str):
    def __new__(cls):
        instance = super().__new__(cls, "safe")
        instance.calls = 0
        return instance

    def __contains__(self, item: object) -> bool:
        self.calls += 1
        return super().__contains__(item)

    def casefold(self) -> str:
        self.calls += 1
        return super().casefold()


def _capabilities(
    *,
    scene_asset_skill=None,
    memory_search=None,
    memory_record=None,
    clock=None,
) -> DefaultVideoLiveCapabilities:
    return DefaultVideoLiveCapabilities(
        model_factory=lambda *_args, **_kwargs: _TimeoutModel(),
        scene_asset_skill=scene_asset_skill or _SceneAssetSkill(),
        memory_search=memory_search or _MemorySearch(),
        memory_record=memory_record or _MemoryRecord(),
        clock=clock or _Clock(),
    )


async def _event_loop_checkpoint() -> None:
    checkpoint = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
    await checkpoint


def _concrete_plan():
    direction = draft_creative_directions("video", VIDEO_FORM)[0].to_dict()
    result = build_plan_markdown("video", VIDEO_FORM, direction)
    blueprints = copy.deepcopy(result.scene_blueprints)
    manifest = copy.deepcopy(result.asset_manifest)
    replacements = {
        "目标用户": "健康管理师林岚",
        "真实使用场景": "晨间公寓健康监测区",
    }
    for blueprint in blueprints:
        for collection in ("characters", "scenes", "props"):
            blueprint["asset_requirements"][collection] = [
                replacements.get(name, name)
                for name in blueprint["asset_requirements"][collection]
            ]
        for old_name, new_name in replacements.items():
            for field_name in ("shot_description", "storyline", "narration"):
                blueprint[field_name] = blueprint[field_name].replace(old_name, new_name)
    for collection in ("characters", "scenes", "props"):
        for item in manifest[collection]:
            old_name = item["name"]
            new_name = replacements.get(old_name, old_name)
            item["name"] = new_name
            for field_name in ("description", "three_view_prompt", "image_prompt"):
                if field_name in item:
                    item[field_name] = item[field_name].replace(old_name, new_name)
    manifest = normalize_asset_manifest(manifest, blueprints)
    history = copy.deepcopy(result.plan_history)
    history[-1]["scene_blueprints"] = copy.deepcopy(blueprints)
    history[-1]["asset_manifest"] = copy.deepcopy(manifest)
    return replace(
        result,
        scene_blueprints=blueprints,
        asset_manifest=manifest,
        plan_history=history,
    )


def _planning_state(*, approved: bool = False):
    service = VideoPlanningWorkflowService()
    now = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    state = service.start(
        workflow_id="wf-live-video",
        conversation_id="conv-live-video",
        intent="video",
        intake_context={"materials": MATERIALS},
        now=now,
    )
    state = service.confirm_intake(state, validate_form("video", VIDEO_FORM), now=now + timedelta(seconds=1))
    directions = draft_creative_directions("video", VIDEO_FORM)
    state = service.publish_directions(state, directions, now=now + timedelta(seconds=2))
    state = service.select_direction(state, "direction_1", now=now + timedelta(seconds=3))
    state = service.publish_initial_plan(state, _concrete_plan(), now=now + timedelta(seconds=4))
    if approved:
        state = service.approve_plan(state, now=now + timedelta(seconds=5))
    return state


@pytest.mark.asyncio
async def test_default_capabilities_feed_m11_planning_dtos() -> None:
    capabilities = _capabilities()

    validation = await capabilities.validate_intake(VIDEO_FORM, intake_rounds=0)
    directions = await capabilities.generate_directions(validation.values, {})
    plan = await capabilities.generate_initial_plan(
        form_values=validation.values,
        selected_direction=directions[0].to_dict(),
        intake_context={},
        materials=MATERIALS,
    )

    assert validation.is_complete
    assert len(directions) == 3
    assert plan.error is None
    assert sum(plan.scene_durations_sec) == validation.values["video_duration_sec"]


@pytest.mark.asyncio
async def test_memory_record_background_does_not_block_capability_return() -> None:
    slow_record = _SlowMemoryRecord()
    capabilities = _capabilities(memory_record=slow_record)

    directions = await asyncio.wait_for(
        capabilities.generate_directions(VIDEO_FORM, {}),
        timeout=1,
    )

    assert len(directions) == 3
    assert slow_record.calls == 1
    assert slow_record.task is not None
    assert not slow_record.task.done()
    slow_record.release.set()
    await slow_record.task


@pytest.mark.asyncio
async def test_memory_ports_fail_open_with_safe_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    capabilities = _capabilities(
        memory_search=_FailingMemorySearch(),
        memory_record=_FailingMemoryRecord(),
    )
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {"user_input": "敏感用户内容"})

    assert len(directions) == 3
    assert "operation=search" in caplog.text
    assert "operation=record_background" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "异常详情" not in caplog.text
    assert "敏感用户内容" not in caplog.text


@pytest.mark.asyncio
async def test_memory_record_clock_failure_is_safe_and_fail_open(caplog: pytest.LogCaptureFixture) -> None:
    memory_record = _MemoryRecord()
    capabilities = _capabilities(memory_record=memory_record, clock=_FailingClock())
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})

    assert len(directions) == 3
    assert memory_record.records == []
    assert "operation=record_background" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "时钟异常详情" not in caplog.text


@pytest.mark.asyncio
async def test_async_memory_recorder_runs_in_background_without_runtime_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory_record = _AsyncMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        directions = await capabilities.generate_directions(VIDEO_FORM, {})
        assert memory_record.executed is False
        await asyncio.wait_for(memory_record.started.wait(), timeout=1)
        assert memory_record.finished.is_set() is False
        memory_record.release.set()
        await asyncio.wait_for(memory_record.finished.wait(), timeout=1)
        await _event_loop_checkpoint()
        gc.collect()

    assert len(directions) == 3
    assert memory_record.executed is True
    assert not any("was never awaited" in str(item.message) for item in captured)
    assert "operation=record_background_async" not in caplog.text


@pytest.mark.asyncio
async def test_immediately_cancelled_coroutine_is_owned_without_runtime_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    started = False

    async def record() -> None:
        nonlocal started
        started = True

    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    coroutine = record()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        live_capabilities._schedule_memory_record_awaitable(coroutine)
        assert len(live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS) == 1
        scheduled = next(iter(live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS))
        scheduled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scheduled
        del coroutine
        await _event_loop_checkpoint()
        gc.collect()

    assert started is False
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert not any("was never awaited" in str(item.message) for item in captured)
    assert "operation=record_background_async" not in caplog.text


@pytest.mark.asyncio
async def test_non_awaitable_memory_recorder_result_is_safe_protocol_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    capabilities = _capabilities(memory_record=_InvalidMemoryRecord())
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})

    assert len(directions) == 3
    assert "operation=record_background" in caplog.text
    assert "exception_type=TypeError" in caplog.text
    assert "invalid-schedule-result" not in caplog.text


@pytest.mark.asyncio
async def test_failed_async_memory_recorder_logs_safe_background_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory_record = _FailingAsyncMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})
    await asyncio.wait_for(memory_record.finished.wait(), timeout=1)
    await _event_loop_checkpoint()

    assert len(directions) == 3
    assert "operation=record_background_async" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "后台记录异常详情" not in caplog.text


@pytest.mark.asyncio
async def test_future_memory_recorder_runs_in_background_without_extra_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    memory_record = _FutureMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})
    assert memory_record.future.done() is False
    memory_record.future.set_result(None)
    await _event_loop_checkpoint()

    assert len(directions) == 3
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert "operation=record_background_async" not in caplog.text


@pytest.mark.asyncio
async def test_failed_future_memory_recorder_is_consumed_with_safe_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    memory_record = _FutureMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})
    memory_record.future.set_exception(RuntimeError("Bearer turn-secret Future 异常详情"))
    await _event_loop_checkpoint()

    assert len(directions) == 3
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert "operation=record_background_async" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "Future 异常详情" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_future_memory_recorder_only_clears_tracking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    memory_record = _FutureMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    directions = await capabilities.generate_directions(VIDEO_FORM, {})
    memory_record.future.cancel()
    await _event_loop_checkpoint()

    assert len(directions) == 3
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert "operation=record_background_async" not in caplog.text


@pytest.mark.asyncio
async def test_custom_awaitable_runs_without_calling_user_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    memory_record = _CustomAwaitableMemoryRecord()
    capabilities = _capabilities(memory_record=memory_record)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        directions = await capabilities.generate_directions(VIDEO_FORM, {})
        await asyncio.wait_for(memory_record.finished.wait(), timeout=1)
        await _event_loop_checkpoint()
        gc.collect()

    assert len(directions) == 3
    assert memory_record.close_calls == 0
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert not any("was never awaited" in str(item.message) for item in captured)
    assert "operation=record_background_async" not in caplog.text


@pytest.mark.asyncio
async def test_custom_awaitable_schedule_failure_does_not_call_async_close(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    awaitable = _CustomAwaitableMemoryRecord()

    def reject_schedule(_value, *, loop=None):
        raise RuntimeError("Bearer turn-secret 自定义 awaitable 调度异常详情")

    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        monkeypatch.setattr(asyncio, "ensure_future", reject_schedule)
        live_capabilities._schedule_memory_record_awaitable(awaitable)
        gc.collect()

    assert awaitable.close_calls == 0
    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert not any("was never awaited" in str(item.message) for item in captured)
    assert "operation=record_background_schedule" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "调度异常详情" not in caplog.text


@pytest.mark.asyncio
async def test_done_callback_handles_exception_inspection_failure_without_escaping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    class InspectionFailureFuture(asyncio.Future[None]):
        def exception(self):
            raise RuntimeError("Bearer turn-secret 回调检查异常详情")

    future = InspectionFailureFuture()
    live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS.add(future)
    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")

    live_capabilities._consume_memory_record_result(future)
    future.cancel()

    assert not live_capabilities._BACKGROUND_MEMORY_RECORD_TASKS
    assert "operation=record_background_async" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text
    assert "回调检查异常详情" not in caplog.text


@pytest.mark.asyncio
async def test_schedule_failure_closes_only_exact_coroutine_without_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    executed = False

    async def record() -> None:
        nonlocal executed
        executed = True

    coroutine = record()
    loop_type = type(asyncio.get_running_loop())

    def reject_schedule(self, coro, *, name=None, context=None):
        raise RuntimeError("Bearer turn-secret 调度异常详情")

    caplog.set_level("WARNING", logger="pixelflow.agent_workflows.video.live_capabilities")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(loop_type, "create_task", reject_schedule)
            live_capabilities._schedule_memory_record_awaitable(coroutine)
        gc.collect()
        await _event_loop_checkpoint()

    assert executed is False
    assert not any("was never awaited" in str(item.message) for item in captured)
    assert "operation=record_background_schedule" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "turn-secret" not in caplog.text


@pytest.mark.asyncio
async def test_memory_and_clock_ports_receive_safe_stage_contract() -> None:
    memory_search = _MemorySearch()
    memory_record = _MemoryRecord()
    clock = _Clock()
    capabilities = _capabilities(
        memory_search=memory_search,
        memory_record=memory_record,
        clock=clock,
    )

    directions = await capabilities.generate_directions(VIDEO_FORM, {"source": "turn"})

    assert len(directions) == 3
    assert len(memory_search.calls) == 1
    assert memory_search.calls[0]["categories"] == [
        "preference",
        "brand",
        "skill",
        "experience",
    ]
    assert memory_record.records == [
        {
            "summary": "视频 live 能力已生成 3 个创意方向",
            "category": "experience",
            "metadata": {
                "stage": "direction_generation",
                "direction_count": 3,
                "recorded_at": "2026-07-31T12:00:00+00:00",
            },
        }
    ]
    assert clock.calls == 1


@pytest.mark.asyncio
async def test_default_capabilities_revise_and_restore_authoritative_plan() -> None:
    capabilities = _capabilities()
    state = _planning_state()

    revision = await capabilities.revise_plan(state, revision_feedback="把风格保持为电影写实风")
    restored = await capabilities.restore_plan(state, plan_version=1)

    assert revision.plan_version == 1
    assert revision.error
    assert restored.plan_version == 1
    assert restored.restored_from_version == 1
    assert restored.plan_history == state.active_plan.plan_history


@pytest.mark.asyncio
async def test_default_capabilities_generate_scene_assets_with_transient_credential_only() -> None:
    capabilities = _capabilities()
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )
    credential = TransientTurnCredential("Bearer turn-secret")

    assert scene_state.status is WorkflowStatus.RUNNING
    result = await capabilities.generate_scene_assets(scene_state, credential=credential)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["global_assets"]["characters"][0]["three_view_images"]
    assert "turn-secret" not in serialized
    assert "Bearer" not in serialized
    assert "turn-secret" not in repr(credential)
    assert not hasattr(credential, "to_dict")
    assert not hasattr(credential, "model_dump")
    with pytest.raises(TypeError, match="临时凭据"):
        asdict(credential)
    with pytest.raises(TypeError, match="临时凭据"):
        pickle.dumps(credential)
    with pytest.raises(TypeError, match="临时凭据"):
        copy.deepcopy(credential)
    with pytest.raises(TypeError) as json_error:
        json.dumps(credential)
    assert "turn-secret" not in str(json_error.value)
    with pytest.raises(RuntimeError, match="当前 Turn 临时凭据不可用"):
        await capabilities.generate_scene_assets(scene_state, credential=credential)


def test_transient_authorization_field_is_opaque_for_common_conversions() -> None:
    credential = TransientTurnCredential("Bearer turn-secret")
    authorization = credential.authorization

    assert not isinstance(authorization, str)
    assert str(authorization) == "[已脱敏临时凭据]"
    assert repr(authorization) == "[已脱敏临时凭据]"
    assert f"{authorization}" == "[已脱敏临时凭据]"
    assert format(authorization, ">20") == "[已脱敏临时凭据]"
    assert authorization != "Bearer turn-secret"
    with pytest.raises(TypeError):
        "header=" + authorization
    with pytest.raises(TypeError):
        authorization + "-suffix"
    with pytest.raises(TypeError):
        vars(authorization)
    with pytest.raises(TypeError, match="临时凭据"):
        copy.copy(authorization)
    with pytest.raises(TypeError, match="临时凭据"):
        copy.deepcopy(authorization)
    with pytest.raises(TypeError, match="临时凭据"):
        pickle.dumps(authorization)
    with pytest.raises(TypeError) as json_error:
        json.dumps(authorization)
    assert "turn-secret" not in str(json_error.value)
    assert not hasattr(credential, "reveal_for_skill_boundary")
    assert not hasattr(authorization, "_reveal_for_boundary")
    reflected_values: list[object] = []
    for owner in (credential, authorization):
        for owner_type in type(owner).__mro__:
            raw_slots = getattr(owner_type, "__slots__", ())
            slot_names = (raw_slots,) if isinstance(raw_slots, str) else raw_slots
            for slot_name in slot_names:
                if slot_name == "__weakref__":
                    continue
                reflected_values.append(getattr(owner, slot_name))
    assert "turn-secret" not in repr(reflected_values)
    assert "Bearer turn-secret" not in repr(reflected_values)


@pytest.mark.asyncio
async def test_discarded_transient_credential_cannot_enter_skill_boundary() -> None:
    capabilities = _capabilities()
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )
    credential = TransientTurnCredential("Bearer turn-secret")

    credential.discard()

    with pytest.raises(RuntimeError, match="当前 Turn 临时凭据不可用") as error_info:
        await capabilities.generate_scene_assets(scene_state, credential=credential)
    assert "turn-secret" not in str(error_info.value)


def test_unconsumed_transient_credential_is_removed_after_gc() -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    initial_count = len(live_capabilities._TRANSIENT_CREDENTIAL_SECRETS)
    credential = TransientTurnCredential("Bearer turn-secret")
    credential_reference = weakref.ref(credential)
    assert len(live_capabilities._TRANSIENT_CREDENTIAL_SECRETS) == initial_count + 1

    del credential
    gc.collect()

    assert credential_reference() is None
    assert len(live_capabilities._TRANSIENT_CREDENTIAL_SECRETS) <= initial_count


@pytest.mark.parametrize(
    "credential_key",
    [
        "authorizationToken",
        "bearerToken",
        "oauthAccessToken",
        "proxyAuthorization",
        "privateKey",
        "authorization_token",
        "authorization-token",
        "authorization token",
        "authorizationtoken",
        "AuthorizationTOKEN",
        "authToken",
        "Auth-Token",
        "AUTH TOKEN",
        "bearer_token",
        "Bearer-Token",
        "BEARER TOKEN",
        "bearertoken",
        "oauth_access_token",
        "OAuth-Access-Token",
        "OAUTH ACCESS TOKEN",
        "OAuthAccessTOKEN",
        "oauthaccesstoken",
        "oauth_refresh_token",
        "OAuthRefreshToken",
        "oauthrefreshtoken",
        "proxy_authorization",
        "Proxy-Authorization",
        "PROXY AUTHORIZATION",
        "proxyauthorization",
        "private_key",
        "Private-Key",
        "PRIVATE KEY",
        "privatekey",
        "publicKey",
        "public_key",
        "Public-Key",
        "PUBLIC KEY",
        "publickey",
        "apiKey",
        "API-KEY",
        "api key",
        "apikey",
        "clientSecret",
        "CLIENT_SECRET",
        "client-secret",
        "clientsecret",
        "IDToken",
        "id_token",
        "id-token",
        "idtoken",
        "Cookie",
        "sessionCookie",
        "credential",
        "providerCredential",
        "password",
        "accountPassword",
        "sessionId",
        "SESSION_ID",
        "sessionid",
        "accessKey",
        "accesskey",
        "client_keys",
        "subscription-key",
        "subscriptionkey",
    ],
)
def test_safe_projection_rejects_structured_credential_key_variants(
    credential_key: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含敏感字段"):
        live_capabilities._safe_json_projection(
            {"outer": {credential_key: "opaque-provider-value"}},
            authorization="Bearer turn-secret",
        )


@pytest.mark.parametrize(
    ("credential_key", "expected_message"),
    [
        ("ａｕｔｈｏｒｉｚａｔｉｏｎ", "场景资产结果包含敏感字段"),
        ("授权令牌", "场景资产结果包含非法字段"),
        ("t\u043eken", "场景资产结果包含非法字段"),
        ("t\u03bfken", "场景资产结果包含非法字段"),
        ("credentials", "场景资产结果包含敏感字段"),
        ("secrets", "场景资产结果包含敏感字段"),
        ("tokens", "场景资产结果包含敏感字段"),
    ],
    ids=[
        "fullwidth-authorization",
        "chinese-key",
        "cyrillic-homoglyph",
        "greek-homoglyph",
        "plural-credentials",
        "plural-secrets",
        "plural-tokens",
    ],
)
def test_safe_projection_rejects_reviewer_unicode_and_plural_keys(
    credential_key: str,
    expected_message: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match=expected_message) as error_info:
        live_capabilities._safe_json_projection(
            {"outer": {credential_key: "opaque-provider-value"}},
            authorization="Bearer turn-secret",
        )

    assert credential_key not in str(error_info.value)
    assert "opaque-provider-value" not in str(error_info.value)


@pytest.mark.parametrize(
    "credential_key",
    [
        "providerKeys",
        "provider_keys",
        "provider-keys",
        "providerkeys",
        "key",
        "keys",
        "Key",
        "Keys",
    ],
)
def test_safe_projection_rejects_provider_and_bare_key_variants_recursively(
    credential_key: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含敏感字段") as error_info:
        live_capabilities._safe_json_projection(
            {"outer": [{"nested": {credential_key: "opaque-provider-value"}}]},
            authorization="Bearer turn-secret",
        )

    assert credential_key not in str(error_info.value)
    assert "opaque-provider-value" not in str(error_info.value)


@pytest.mark.parametrize(
    "credential_key",
    [
        "key_value",
        "keyValue",
        "keyvalue",
        "key_header",
        "keyHeader",
        "keyheader",
        "key_id",
        "keyId",
        "keyID",
        "keyid",
        "key_hash",
        "keyHash",
        "keyhash",
        "key_material",
        "keyMaterial",
        "keymaterial",
    ],
)
def test_safe_projection_rejects_key_decoration_variants_recursively(
    credential_key: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含敏感字段") as error_info:
        live_capabilities._safe_json_projection(
            {"outer": [{"nested": {credential_key: "opaque-provider-value"}}]},
            authorization="Bearer turn-secret",
        )

    assert credential_key not in str(error_info.value)
    assert "opaque-provider-value" not in str(error_info.value)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "场景状态",
        "scene\u200dstatus",
        "scene\x00status",
        "scene\tstatus",
        "scene\nstatus",
        "scene\x7fstatus",
        "scene.status",
        "scene%status",
        "scene/status",
        "scene:status",
    ],
    ids=[
        "unicode",
        "zero-width-control",
        "nul-control",
        "tab-control",
        "newline-control",
        "delete-control",
        "dot-punctuation",
        "percent-punctuation",
        "slash-punctuation",
        "colon-punctuation",
    ],
)
def test_safe_projection_rejects_non_ascii_control_and_punctuation_keys(
    unsafe_key: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含非法字段") as error_info:
        live_capabilities._safe_json_projection(
            {unsafe_key: "普通业务值"},
            authorization="Bearer turn-secret",
        )

    assert unsafe_key not in str(error_info.value)
    assert "普通业务值" not in str(error_info.value)


@pytest.mark.parametrize(
    "credential_key",
    [
        "accessTokens",
        "clientSecrets",
        "providerCredentials",
        "providerCookies",
        "accountPasswords",
        "userSessions",
        "privateKeys",
        "access_tokens",
        "client-secrets",
        "provider credentials",
        "APIKeys",
        "accesstokens",
        "clientsecrets",
        "providercredentials",
        "providercookies",
        "accountpasswords",
        "usersessions",
        "privatekeys",
    ],
)
def test_safe_projection_rejects_plural_credential_words_and_compact_forms(
    credential_key: str,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含敏感字段"):
        live_capabilities._safe_json_projection(
            {credential_key: "opaque-provider-value"},
            authorization="Bearer turn-secret",
        )


def test_safe_projection_preserves_plural_metadata_and_unicode_business_values() -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    raw = {
        "business_tokens_count": 2,
        "providerCredentialsStatus": "已脱敏",
        "client-secrets-status": "已清理",
        "provider credentials enabled": True,
        "APIKeysUsage": 2,
        "privatekeysenabled": True,
        "authorization_expires_at": "稍后刷新",
        "tokenized_url": "https://assets.example.com/tokenized/image.png",
        "scene status": "处理完成",
    }

    assert live_capabilities._safe_json_projection(
        raw,
        authorization="Bearer turn-secret",
    ) == raw


@pytest.mark.parametrize(
    ("metadata_key", "metadata_value"),
    [
        ("business_keys_count", 2),
        ("provider_key_count", 1),
        ("provider_keys_count", 2),
        ("scene_key_usage", 3),
        ("provider_keys_limit", 4),
        ("provider_key_ttl", 3600),
        ("provider_keys_expires", "2026-08-01T12:00:00Z"),
        ("provider_key_expiry", "2026-08-01T12:00:00Z"),
        ("provider_keys_status", "active"),
        ("provider_key_enabled", True),
    ],
)
def test_safe_projection_preserves_provider_key_metadata_suffixes(
    metadata_key: str,
    metadata_value: object,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    raw = {"outer": [{"metadata": {metadata_key: metadata_value}}]}

    assert live_capabilities._safe_json_projection(
        raw,
        authorization="Bearer turn-secret",
    ) == raw


def test_safe_projection_preserves_nested_scene_key_fields_and_urls() -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    raw = {
        "key_frame": "https://assets.example.com/keyframe.png",
        "scene": {
            "keyFrame": {"url": "https://assets.example.com/frames/hero.png"},
            "assets": [
                {
                    "key_image": "https://assets.example.com/images/product.png",
                    "key_points": ["主体清晰", "构图稳定"],
                    "keyframe_url": "https://assets.example.com/video/keyframe.jpg",
                }
            ],
        },
    }

    assert live_capabilities._safe_json_projection(
        raw,
        authorization="Bearer turn-secret",
    ) == raw


@pytest.mark.parametrize(
    ("metadata_key", "metadata_value"),
    [
        ("business_token_count", 2),
        ("scene_token_count", 4),
        ("authorization_expires_at", "2026-08-01T12:00:00Z"),
        ("accessTokenUsage", 128),
        ("auth-limit", 20),
        ("bearer token ttl", 3600),
        ("oauthAccessTokenExpires", "2026-08-01T12:00:00Z"),
        ("oauthaccesstokenexpires", "2026-08-01T12:00:00Z"),
        ("refresh-token-expiry", "2026-08-01T12:00:00Z"),
        ("authorizationexpiresat", "2026-08-01T12:00:00Z"),
        ("authorizationStatus", "active"),
        ("api_key_enabled", True),
        ("apikeyenabled", True),
        ("publicKeyStatus", "active"),
        ("sessionIdEnabled", False),
        ("sessionidenabled", False),
    ],
)
def test_safe_projection_preserves_credential_metadata_suffixes(
    metadata_key: str,
    metadata_value: object,
) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    raw = {metadata_key: metadata_value}

    assert live_capabilities._safe_json_projection(
        raw,
        authorization="Bearer turn-secret",
    ) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_raw",
    [
        {"outer": [{"accessToken": "provider-value"}]},
        {"outer": {"ID-Token": "provider-value"}},
        {"outer": {"client secret": "provider-value"}},
        {"outer": {"apiKey": "provider-value"}},
        {"outer": {"X-Api-Key": "opaque-provider-token"}},
        {"outer": {"X_Access_Token": "opaque-provider-token"}},
        {"outer": {"X-Goog-Api-Key": "opaque-provider-token"}},
        {"outer": {"providerAccessToken": "opaque-provider-token"}},
        {"outer": {"Cookie": "opaque-provider-token"}},
        {"outer": {"Set-Cookie": "opaque-provider-token"}},
        {"outer": {"authorizationHeader": "opaque-provider-token"}},
        {"outer": {"secretValue": "opaque-provider-token"}},
        {"outer": {"cookieValue": "opaque-provider-token"}},
        {"outer": {"credentialValue": "opaque-provider-token"}},
        {"note": "turn-secret"},
        {"note": "响应包含 Bearer unrelated-secret"},
        {
            "note": (
                "eyJhbGciOiJIUzI1NiJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                "signaturevalue"
            )
        },
        _SafeRawDto(
            delivery_url="https://assets.example.com/image.png",
            business_token_count=3,
        ),
        _UnsafeRawDto(accessToken="provider-value"),
        _BrokenRawDto(detail="Bearer turn-secret"),
    ],
)
async def test_scene_asset_output_rejects_sensitive_payloads_fail_closed(unsafe_raw: object) -> None:
    capabilities = _capabilities(scene_asset_skill=_RawFailureSceneAssetSkill(unsafe_raw))
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(RuntimeError, match="场景资产结果未通过安全校验") as error_info:
        await capabilities.generate_scene_assets(
            scene_state,
            credential=TransientTurnCredential("Bearer turn-secret"),
        )

    assert "turn-secret" not in str(error_info.value)
    assert "provider-value" not in str(error_info.value)
    assert "opaque-provider-token" not in str(error_info.value)
    assert "unrelated-secret" not in str(error_info.value)


@pytest.mark.asyncio
async def test_scene_asset_output_projects_safe_mapping_and_keeps_business_values() -> None:
    raw = {
        "delivery_url": "https://assets.example.com/tokenized/image.png?version=business",
        "asset_count": 3,
        "business_token_count": 2,
        "scene_token_count": 4,
        "authorization_expires_at": "2026-08-01T12:00:00Z",
        "scene_metadata": [{"description": "电影写实风"}],
    }
    capabilities = _capabilities(scene_asset_skill=_RawFailureSceneAssetSkill(raw))
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )

    result = await capabilities.generate_scene_assets(
        scene_state,
        credential=TransientTurnCredential("Bearer turn-secret"),
    )

    projected = result["failed_assets"][0]["raw"]
    assert projected == raw
    json.dumps(result)


@pytest.mark.parametrize(
    "value",
    [_SideEffectMapping(), _SideEffectDict(), _SideEffectList(), _SideEffectStr()],
)
def test_safe_projection_rejects_container_subclasses_without_executing_them(value: object) -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    with pytest.raises(ValueError, match="场景资产结果包含不支持的对象"):
        live_capabilities._safe_json_projection(
            value,
            authorization="Bearer turn-secret",
        )

    assert value.calls == 0  # type: ignore[attr-defined]


def test_safe_projection_rejects_str_subclass_key_without_executing_it() -> None:
    from pixelflow.agent_workflows.video import live_capabilities

    key = _SideEffectStr()
    with pytest.raises(ValueError, match="场景资产结果包含非字符串字段"):
        live_capabilities._safe_json_projection(
            {key: "value"},
            authorization="Bearer turn-secret",
        )

    assert key.calls == 0


@pytest.mark.asyncio
async def test_scene_asset_projection_rejects_custom_serializer_without_executing_it() -> None:
    _SideEffectRawDto.serializer_calls = 0
    raw = _SideEffectRawDto(detail="安全外观但禁止执行 serializer")
    capabilities = _capabilities(scene_asset_skill=_RawFailureSceneAssetSkill(raw))
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(RuntimeError, match="场景资产结果未通过安全校验"):
        await capabilities.generate_scene_assets(
            scene_state,
            credential=TransientTurnCredential("Bearer turn-secret"),
        )

    assert _SideEffectRawDto.serializer_calls == 0


@pytest.mark.asyncio
async def test_scene_asset_failure_restores_previous_authorization_context() -> None:
    from app.gateway.content_app_auth_context import (
        require_current_authorization,
        reset_current_content_app_auth,
        set_current_content_app_auth,
    )

    capabilities = _capabilities(scene_asset_skill=_FailingSceneAssetSkill())
    approved = _planning_state(approved=True)
    scene_state = VideoScenePackageWorkflowService().prepare_from_approved_plan(
        approved,
        materials=MATERIALS,
        now=approved.updated_at + timedelta(seconds=1),
    )
    outer_token = set_current_content_app_auth("Bearer outer-session", username="outer-user")
    try:
        with pytest.raises(RuntimeError, match="场景资产生成失败") as error_info:
            await capabilities.generate_scene_assets(
                scene_state,
                credential=TransientTurnCredential("Bearer turn-secret"),
            )
        assert "turn-secret" not in str(error_info.value)
        assert "Bearer" not in str(error_info.value)
        assert require_current_authorization() == "Bearer outer-session"
    finally:
        reset_current_content_app_auth(outer_token)
    from app.gateway.content_app_auth_context import get_current_content_app_auth

    assert get_current_content_app_auth() is None


@pytest.mark.asyncio
async def test_default_capabilities_fail_closed_for_missing_or_mismatched_capabilities() -> None:
    capabilities = _capabilities()

    missing = await capabilities.validate_intake(
        {"product_info": "智能戒指"},
        intake_rounds=0,
    )
    mismatched_values = copy.deepcopy(VIDEO_FORM)
    mismatched_values["video_ratio"] = "1:1"
    mismatched_values["video_model_capabilities"]["aspect_ratios"] = ["9:16"]
    mismatched = await capabilities.validate_intake(
        mismatched_values,
        intake_rounds=0,
    )

    assert missing.is_complete is False
    assert "product_category" in missing.missing_fields
    assert mismatched.is_complete is False
    assert mismatched.missing_fields


def test_routers_and_live_port_share_application_functions_without_reverse_imports() -> None:
    from app.gateway.routers import pixelflow_intake, pixelflow_planning, pixelflow_video
    from pixelflow.agent_workflows.video import live_capabilities

    assert pixelflow_intake.validate_form is live_capabilities.validate_video_application_form
    assert pixelflow_intake.draft_creative_directions_with_llm is live_capabilities.generate_application_directions
    assert pixelflow_planning.build_plan_markdown_with_llm is live_capabilities.generate_application_plan
    assert pixelflow_planning.revise_plan_markdown_with_llm is live_capabilities.revise_application_plan
    assert pixelflow_planning.restore_plan_version is live_capabilities.restore_application_plan
    assert pixelflow_video.run_generate_scene_assets is live_capabilities.generate_application_scene_assets
    assert not hasattr(live_capabilities, "Request")
    assert not hasattr(live_capabilities, "GenerateSceneAssetsRequest")


def test_clean_process_imports_live_capability_without_runtime_preload() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pixelflow.agent_workflows.video.live_capabilities "
                "import DefaultVideoLiveCapabilities; "
                "print(DefaultVideoLiveCapabilities.__name__)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "DefaultVideoLiveCapabilities"


def test_clean_process_imports_scene_package_module_without_partial_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pixelflow.agent_workflows.video.scene_packages "
                "import VideoScenePackageAuthoritySnapshot; "
                "print(VideoScenePackageAuthoritySnapshot.__name__)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "VideoScenePackageAuthoritySnapshot"


def test_video_package_keeps_all_public_exports_in_clean_process() -> None:
    module_by_name = {
        "VideoLiveStateConflictError": "live_handler",
        "VideoLiveWorkflowHandler": "live_handler",
        "VideoDeliveryWorkflowService": "delivery",
        "VideoDeliveryWorkflowState": "delivery",
        "VideoPlanAuthoritySnapshot": "planning",
        "VideoPlanningStage": "planning",
        "VideoPlanningWorkflowService": "planning",
        "VideoPlanningWorkflowState": "planning",
        "VideoScenePackageAuthoritySnapshot": "scene_packages",
        "VideoScenePackageStage": "scene_packages",
        "VideoScenePackageWorkflowService": "scene_packages",
        "VideoScenePackageWorkflowState": "scene_packages",
        "VideoSceneGenerationStage": "video_generation",
        "VideoSceneGenerationWorkflowService": "video_generation",
        "VideoSceneGenerationWorkflowState": "video_generation",
        "VideoSceneAtomicOperationPort": "video_generation",
        "VideoSceneOperationTerminalClaim": "video_generation",
        "VideoSceneVideoStage": "video_generation",
        "VideoSceneVideoWorkflowService": "video_generation",
        "VideoSceneVideoWorkflowState": "video_generation",
        "VideoWorkflowState": "state_codec",
        "VideoWorkflowStateEnvelope": "state_codec",
        "VideoWorkflowStateKind": "state_codec",
        "WorkflowDispatchResult": "live_handler",
        "VideoMergeSkillPort": "postproduction",
        "VideoOperationStartClaim": "postproduction",
        "VideoOperationTerminalClaim": "postproduction",
        "VideoPostProductionAtomicOperationPort": "postproduction",
        "VideoPostProductionStage": "postproduction",
        "VideoPostProductionWorkflowService": "postproduction",
        "VideoPostProductionWorkflowState": "postproduction",
        "VideoQualityReviewSkillPort": "postproduction",
        "VideoQualityReviewWorkflowResult": "postproduction",
        "canonical_payload_sha256": "state_codec",
        "canonical_video_workflow_envelope_sha256": "state_codec",
        "decode_video_workflow_state": "state_codec",
        "encode_video_workflow_state": "state_codec",
        "project_video_workflow_state": "state_codec",
    }
    import_names = ", ".join(module_by_name)
    script = (
        "import importlib\n"
        f"from pixelflow.agent_workflows.video import ({import_names})\n"
        "from pixelflow.agent_workflows import video\n"
        f"expected = {module_by_name!r}\n"
        "assert video.__all__ == list(expected)\n"
        "for name, module_name in expected.items():\n"
        "    public = globals()[name]\n"
        "    defining = importlib.import_module(f'pixelflow.agent_workflows.video.{module_name}')\n"
        "    assert public is getattr(defining, name), name\n"
        "print(len(expected))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(len(module_by_name))


def test_clean_process_concurrent_first_imports_do_not_deadlock() -> None:
    script = """
import importlib.abc
import importlib.machinery
import threading
import pixelflow
import pixelflow.agent_workflows

module_gate = threading.Barrier(2)

class GateLoader(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        if hasattr(self.loader, "create_module"):
            return self.loader.create_module(spec)
        return None

    def exec_module(self, module):
        module_gate.wait()
        self.loader.exec_module(module)

class GateFinder(importlib.abc.MetaPathFinder):
    targets = {"pixelflow.agent_runtime", "pixelflow.agent_workflows.video"}

    def find_spec(self, fullname, path, target=None):
        if fullname not in self.targets:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        spec.loader = GateLoader(spec.loader)
        return spec

import sys
sys.meta_path.insert(0, GateFinder())
targets = tuple(range(16))
barrier = threading.Barrier(len(targets))
errors = []

def load(target):
    try:
        barrier.wait()
        if target % 4 == 0:
            from pixelflow.agent_runtime import SupervisorReplayRuntime
            assert SupervisorReplayRuntime.__name__ == "SupervisorReplayRuntime"
        elif target % 4 == 1:
            from pixelflow.agent_workflows.video import VideoPlanningWorkflowService
            assert VideoPlanningWorkflowService.__name__ == "VideoPlanningWorkflowService"
        elif target % 4 == 2:
            from pixelflow.agent_workflows.video.scene_packages import VideoScenePackageAuthoritySnapshot
            assert VideoScenePackageAuthoritySnapshot.__name__ == "VideoScenePackageAuthoritySnapshot"
        else:
            from pixelflow.agent_workflows.video.live_capabilities import DefaultVideoLiveCapabilities
            assert DefaultVideoLiveCapabilities.__name__ == "DefaultVideoLiveCapabilities"
    except BaseException as error:
        errors.append((type(error).__name__, str(error)))

threads = [threading.Thread(target=load, args=(target,)) for target in targets]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=20)
assert not any(thread.is_alive() for thread in threads), "首次并发导入未结束"
assert not errors, errors
print(len(threads))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "16"


def test_importing_video_parent_does_not_preload_runtime_or_warn() -> None:
    script = """
import sys
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import pixelflow
    import pixelflow.agent_workflows
with warnings.catch_warnings(record=True) as captured:
    warnings.simplefilter("always")
    import pixelflow.agent_workflows.video
assert "pixelflow.agent_runtime.replay" not in sys.modules
assert not captured, [str(item.message) for item in captured]
print("lightweight")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "lightweight"


def test_reviewer_concurrent_import_probe_returns_every_symbol() -> None:
    script = """
from concurrent.futures import ThreadPoolExecutor

targets = (
    ("pixelflow.agent_workflows.video", "VideoWorkflowStateEnvelope"),
    ("pixelflow.agent_workflows.video", "VideoDeliveryWorkflowService"),
    ("pixelflow.agent_workflows.video", "VideoPlanningWorkflowService"),
    ("pixelflow.agent_workflows.video.scene_packages", "VideoScenePackageWorkflowService"),
    ("pixelflow.agent_workflows.video.live_capabilities", "DefaultVideoLiveCapabilities"),
) * 8

def load(target):
    module = __import__(target[0], fromlist=[target[1]])
    return getattr(module, target[1]).__name__

with ThreadPoolExecutor(max_workers=16) as pool:
    results = list(pool.map(load, targets))
assert len(results) == 40
assert all(results)
print(len(results))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "40"


def test_agent_runtime_package_keeps_public_export_identity_and_errors() -> None:
    script = """
import pixelflow.agent_runtime as runtime
from pixelflow.agent_runtime import (
    SupervisorReplayDisposition,
    SupervisorReplayResult,
    SupervisorReplayRuntime,
    WorkflowCommandPreview,
)
from pixelflow.agent_runtime import replay

expected = [
    "SupervisorReplayDisposition",
    "SupervisorReplayResult",
    "SupervisorReplayRuntime",
    "WorkflowCommandPreview",
]
assert runtime.__all__ == expected
for name in expected:
    assert globals()[name] is getattr(replay, name)
try:
    runtime.not_a_public_symbol
except AttributeError:
    pass
else:
    raise AssertionError("未知属性没有抛出 AttributeError")
runtime._PUBLIC_MODULES["BrokenExport"] = "missing_runtime_module"
try:
    runtime.BrokenExport
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("真实 ImportError 被错误改写")
print(len(expected))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "4"
