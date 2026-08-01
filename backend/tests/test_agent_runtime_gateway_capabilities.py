from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.gateway.pixelflow_agent_live_capabilities import (
    VIDEO_LIVE_HANDLER_NOT_READY,
    make_pixelflow_agent_live_capabilities,
)
from pixelflow.agent_runtime.contracts import ContextBudgetReport, ContextEnvelope
from pixelflow.agent_workflows.video.live_capabilities import DefaultVideoLiveCapabilities


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 27, 8, 0, tzinfo=UTC)


class _SceneAssetSkill:
    async def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}

    async def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}


class _EmptySceneAssetSkill:
    pass


class _SceneAssetSkillWithoutReferenceImage:
    async def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}


class _SceneAssetSkillWithoutTextToImage:
    async def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        return {}


class _SynchronousSceneAssetSkill:
    calls = 0

    def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}

    def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}


class _SceneAssetSkillWithSynchronousReferenceImage:
    calls = 0

    def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}

    async def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}


class _SceneAssetSkillWithSynchronousTextToImage:
    calls = 0

    async def reference_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}

    def text_to_image(self, **_kwargs: Any) -> dict[str, object]:
        type(self).calls += 1
        return {}


class _Model:
    def __init__(self, calls: list[tuple[tuple[str, str], ...]]) -> None:
        self._calls = calls

    async def ainvoke(self, messages: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> AIMessage:
        normalized = tuple(messages)
        self._calls.append(normalized)
        return AIMessage(content='{"action":"clarify"}')


class _PowerMemService:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.record_calls: list[dict[str, Any]] = []
        self.background_tasks: list[asyncio.Task[Any]] = []

    async def search(self, **kwargs: Any) -> list[Any]:
        await asyncio.sleep(0)
        self.search_calls.append(dict(kwargs))
        return []

    async def record(self, **kwargs: Any) -> bool:
        await asyncio.sleep(0)
        self.record_calls.append(dict(kwargs))
        return True

    def create_background_task(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self.background_tasks.append(task)
        return task


def _make_ready_bundle(
    *,
    service: _PowerMemService | None = None,
) -> tuple[Any, list[str], list[tuple[tuple[str, str], ...]], _PowerMemService]:
    model_factory_calls: list[str] = []
    model_calls: list[tuple[tuple[str, str], ...]] = []
    memory = service or _PowerMemService()

    def model_factory(model_name: str, *, attach_tracing: bool = False) -> _Model:
        assert attach_tracing is False
        model_factory_calls.append(model_name)
        return _Model(model_calls)

    bundle = make_pixelflow_agent_live_capabilities(
        model_factory=model_factory,
        scene_asset_skill_factory=_SceneAssetSkill,
        power_mem_service=memory,
        clock=_Clock(),
        model_name="deepseek-v4-pro",
    )
    return bundle, model_factory_calls, model_calls, memory


def _context() -> ContextEnvelope:
    return ContextEnvelope(
        current_input="请说明当前视频方案",
        validated_context_version=1,
        budget_report=ContextBudgetReport(
            estimated_input_tokens=1,
            effective_context_tokens=100,
            usable_input_tokens=80,
            max_output_tokens=10,
            safety_reserve_tokens=10,
            utilization=0.0125,
        ),
    )


def test_capability_factory_builds_real_ports_without_external_calls() -> None:
    bundle, model_factory_calls, model_calls, memory = _make_ready_bundle()

    assert bundle.ready is True
    assert bundle.reason_code is None
    assert isinstance(bundle.capabilities, DefaultVideoLiveCapabilities)
    assert bundle.decision_model is not None
    assert bundle.answer_port is not None
    assert bundle.memory_port is not None
    assert model_factory_calls == []
    assert model_calls == []
    assert memory.search_calls == []
    assert memory.record_calls == []


@pytest.mark.asyncio
async def test_model_ports_create_models_lazily_and_normalize_ai_message_content() -> None:
    bundle, model_factory_calls, model_calls, _memory = _make_ready_bundle()
    assert bundle.decision_model is not None
    assert bundle.answer_port is not None

    decision = await bundle.decision_model.ainvoke((("human", "分类"),))
    answer = await bundle.answer_port.answer(_context())

    assert decision == '{"action":"clarify"}'
    assert answer == '{"action":"clarify"}'
    assert model_factory_calls == ["deepseek-v4-pro", "deepseek-v4-pro"]
    assert len(model_calls) == 2


@pytest.mark.asyncio
async def test_scoped_handler_binds_powermem_to_each_authoritative_command_user() -> None:
    bundle, _model_factory_calls, _model_calls, memory = _make_ready_bundle()
    assert bundle.memory_port is not None

    class _Handler:
        async def dispatch(self, command: Any) -> str:
            await bundle.memory_port.search(
                query_values=[command.user_id, "视频"],
                categories=["preference"],
            )
            bundle.memory_port.record_background(
                summary="视频阶段完成",
                category="experience",
                metadata={"stage": "planning"},
            )
            return command.user_id

    handler = bundle.scope_handler(_Handler())
    users = await asyncio.gather(
        handler.dispatch(SimpleNamespace(user_id="user-a")),
        handler.dispatch(SimpleNamespace(user_id="user-b")),
    )
    await asyncio.gather(*memory.background_tasks)

    assert users == ["user-a", "user-b"]
    assert sorted(call["user_id"] for call in memory.search_calls) == ["user-a", "user-b"]
    assert sorted(call["user_id"] for call in memory.record_calls) == ["user-a", "user-b"]


@pytest.mark.asyncio
async def test_powermem_port_rejects_calls_without_authoritative_user_scope() -> None:
    bundle, _model_factory_calls, _model_calls, memory = _make_ready_bundle()
    assert bundle.memory_port is not None

    with pytest.raises(RuntimeError, match="video_live_user_scope_missing"):
        await bundle.memory_port.search(
            query_values=["视频"],
            categories=["preference"],
        )

    assert memory.search_calls == []


@pytest.mark.parametrize(
    "missing",
    ["model_factory", "scene_asset_skill_factory", "power_mem_service", "clock", "model_name"],
)
def test_capability_factory_fails_closed_when_dependency_is_missing(missing: str) -> None:
    dependencies: dict[str, Any] = {
        "model_factory": lambda *_args, **_kwargs: _Model([]),
        "scene_asset_skill_factory": _SceneAssetSkill,
        "power_mem_service": _PowerMemService(),
        "clock": _Clock(),
        "model_name": "deepseek-v4-pro",
    }
    dependencies[missing] = "" if missing == "model_name" else None

    bundle = make_pixelflow_agent_live_capabilities(**dependencies)

    assert bundle.ready is False
    assert bundle.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
    assert bundle.capabilities is None
    assert bundle.decision_model is None
    assert bundle.answer_port is None
    assert bundle.memory_port is None


def test_capability_factory_hides_scene_skill_construction_error() -> None:
    def failing_skill_factory() -> Any:
        raise RuntimeError("不得外泄的供应商配置")

    bundle = make_pixelflow_agent_live_capabilities(
        model_factory=lambda *_args, **_kwargs: _Model([]),
        scene_asset_skill_factory=failing_skill_factory,
        power_mem_service=_PowerMemService(),
        clock=_Clock(),
        model_name="deepseek-v4-pro",
    )

    assert bundle.ready is False
    assert bundle.reason_code == VIDEO_LIVE_HANDLER_NOT_READY


@pytest.mark.parametrize(
    "scene_asset_skill_factory",
    [
        _EmptySceneAssetSkill,
        _SceneAssetSkillWithoutReferenceImage,
        _SceneAssetSkillWithoutTextToImage,
    ],
)
def test_capability_factory_fails_closed_for_incomplete_scene_asset_skill(
    scene_asset_skill_factory: type[object],
) -> None:
    """场景素材 Skill 缺少任一实际调用方法时不得进入 ready。"""

    bundle = make_pixelflow_agent_live_capabilities(
        model_factory=lambda *_args, **_kwargs: _Model([]),
        scene_asset_skill_factory=scene_asset_skill_factory,
        power_mem_service=_PowerMemService(),
        clock=_Clock(),
        model_name="deepseek-v4-pro",
    )

    assert bundle.ready is False
    assert bundle.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
    assert bundle.capabilities is None


@pytest.mark.parametrize(
    "scene_asset_skill_factory",
    [
        _SynchronousSceneAssetSkill,
        _SceneAssetSkillWithSynchronousReferenceImage,
        _SceneAssetSkillWithSynchronousTextToImage,
    ],
)
def test_capability_factory_requires_both_scene_methods_to_be_async(
    scene_asset_skill_factory: type[object],
) -> None:
    """同步双方法或任一同步方法都不能满足异步 Scene Skill 合同。"""

    scene_asset_skill_factory.calls = 0  # type: ignore[attr-defined]
    bundle = make_pixelflow_agent_live_capabilities(
        model_factory=lambda *_args, **_kwargs: _Model([]),
        scene_asset_skill_factory=scene_asset_skill_factory,
        power_mem_service=_PowerMemService(),
        clock=_Clock(),
        model_name="deepseek-v4-pro",
    )

    assert bundle.ready is False
    assert bundle.reason_code == VIDEO_LIVE_HANDLER_NOT_READY
    assert bundle.capabilities is None
    assert scene_asset_skill_factory.calls == 0  # type: ignore[attr-defined]
