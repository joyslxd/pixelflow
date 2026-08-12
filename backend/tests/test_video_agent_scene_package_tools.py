"""批次 B：场景包 / 参考图 Tool 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext
from pixelflow.video_agent.tools.registry import VideoToolRegistry
from pixelflow.video_agent.tools.scene_packages import (
    GenerateSceneAssetsTool,
    PrepareScenePackagesTool,
    ScenePackageOperationJob,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class FakePackagePort:
    def __init__(self, job: ScenePackageOperationJob) -> None:
        self.job = job
        self.prepare_calls: list[dict[str, object]] = []
        self.assets_calls: list[dict[str, object]] = []

    async def start_prepare_scene_packages(self, context, **kwargs):
        self.prepare_calls.append({"context": context, **kwargs})
        return self.job

    async def start_generate_scene_assets(self, context, **kwargs):
        self.assets_calls.append({"context": context, **kwargs})
        return self.job


def _context(**payload) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-1",
        step_id="step-1",
        workspace=VideoWorkspace(
            workspace_id="ws-1",
            conversation_id="conversation-1",
            payload=payload,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


@pytest.mark.asyncio
async def test_prepare_scene_packages_tool_writes_workspace_assets() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-prepare-1",
            status="succeeded",
            result={
                "message": "已生成资产包",
                "global_assets": {
                    "characters": [{"name": "安然"}],
                    "scenes": [{"name": "酒店"}],
                    "props": [{"name": "面霜"}],
                },
                "scene_packages": [{"scene_id": "s1", "title": "开场"}],
                "creation_contract": {"image_model": "seeddream-5.0"},
            },
        )
    )
    tool = PrepareScenePackagesTool(operation_port=port)
    result = await tool.execute(
        _context(
            script={"status": "ready", "content": "# 脚本\n**时长**：60秒\n镜头1"},
            product_info={"name": "面霜"},
            script_pipeline={
                "characters": {
                    "content": "## 角色设定\n### 安然\n女主\n## 场景设定\n### 酒店\n暖光\n## 道具与产品设定\n### 面霜\n玻璃瓶",
                },
                "export": {"content": "# 终稿\n镜头1 00:00-00:10\n镜头2 00:10-00:20"},
            },
        ),
        {},
    )
    assert result.tool_name == "prepare_scene_packages"
    assert "安然" in str(result.workspace_patch.get("global_assets"))
    assert result.workspace_patch.get("script_plan_confirmed") is True
    assert result.workspace_patch.get("target_duration_ms") == 60_000
    assert len(port.prepare_calls) == 1
    assert port.prepare_calls[0]["target_duration_ms"] == 60_000
    assert "角色设定" in str(port.prepare_calls[0]["plan_markdown"])
    assert port.prepare_calls[0]["form_values"].get("video_duration_sec") == 60
    assert isinstance(result.workspace_patch.get("creation_contract"), dict)


@pytest.mark.asyncio
async def test_prepare_scene_packages_registry_accepts_success_patch_roots() -> None:
    """回归：成功 patch 含 scene_package_job 等根键时，不得再报「工具结果无效」。"""
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-prepare-registry-1",
            status="succeeded",
            result={
                "message": "已生成资产包",
                "global_assets": {"characters": [{"name": "安然"}]},
                "scene_packages": [{"scene_id": "s1"}],
                "creation_contract": {"image_model": "seeddream-5.0"},
            },
        )
    )
    registry = VideoToolRegistry([PrepareScenePackagesTool(operation_port=port)])
    result = await registry.execute(
        _context(script={"status": "ready", "content": "# 脚本\n镜头1"}),
        "prepare_scene_packages",
        {},
    )
    assert result.public_summary == "已生成资产包"
    assert set(result.workspace_patch).issubset(set(PrepareScenePackagesTool.spec.workspace_mutations))


@pytest.mark.asyncio
async def test_generate_scene_assets_tool_requires_confirmation_flag() -> None:
    assert GenerateSceneAssetsTool.spec.confirmation_required is True
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-assets-1",
            status="succeeded",
            result={
                "ok": True,
                "message": "参考图完成",
                "global_assets": {"characters": [{"name": "安然", "images": []}]},
                "scene_packages": [],
                "failed_assets": [],
            },
        )
    )
    tool = GenerateSceneAssetsTool(operation_port=port)
    result = await tool.execute(
        _context(global_assets={"characters": [{"name": "安然"}]}, scene_packages=[{"scene_id": "1"}]),
        {"image_model": "seeddream-5.0", "image_ratio": "9:16", "image_size": "2K"},
    )
    assert result.public_summary == "参考图完成"
    assert port.assets_calls[0]["image_model"] == "seeddream-5.0"


@pytest.mark.asyncio
async def test_generate_scene_assets_registry_accepts_success_patch_roots() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-assets-registry-1",
            status="succeeded",
            result={
                "message": "参考图完成",
                "global_assets": {"characters": [{"name": "安然"}]},
                "scene_packages": [],
                "failed_assets": [],
            },
        )
    )
    registry = VideoToolRegistry([GenerateSceneAssetsTool(operation_port=port)])
    result = await registry.execute(
        _context(
            global_assets={"characters": [{"name": "安然"}]},
            scene_packages=[{"scene_id": "1"}],
        ),
        "generate_scene_assets",
        {"image_model": "seeddream-5.0", "image_ratio": "9:16", "image_size": "2K"},
    )
    assert result.public_summary == "参考图完成"
    assert set(result.workspace_patch).issubset(set(GenerateSceneAssetsTool.spec.workspace_mutations))


@pytest.mark.asyncio
async def test_prepare_domain_job_service_rule_path() -> None:
    from pixelflow.agent_runtime.jobs import ProviderJobAdapter
    from pixelflow.video_agent.adapters.domain_jobs import PrepareScenePackageJobService

    service = PrepareScenePackageJobService(use_llm=False)
    adapter = ProviderJobAdapter(service)
    snapshot = await adapter.start(
        {
            "plan_markdown": (
                "## 角色设定\n### 安然\n女主\n## 场景设定\n### 酒店\n暖光\n"
                "## 道具与产品设定\n### 面霜\n玻璃瓶\n"
                "镜头1 00:00-00:05 特写\n镜头2 00:05-00:10 中景\n"
            ),
            "form_values": {"product_info": "面霜", "product_category": "护肤"},
            "selected_direction": {},
            "materials": [],
            "target_duration_ms": 10_000,
        },
        authorization="local",
        idempotency_key="idem-prepare-1",
    )
    assert snapshot.outcome.value == "succeeded"
    assert snapshot.result is not None
    assert "global_assets" in snapshot.result


@pytest.mark.asyncio
async def test_generate_scene_assets_runner_maps_provider_request() -> None:
    from pixelflow.video_agent.adapters.domain_jobs import make_generate_scene_assets_runner

    calls: list[dict[str, object]] = []

    class _FakeSkill:
        pass

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "global_assets": {"characters": [{"name": "安然", "images": ["https://cdn.example/a.png"]}]},
            "scene_packages": [],
            "failed_assets": [],
            "message": "ok",
        }

    import pixelflow.generate.scene_assets as scene_assets_mod

    original = scene_assets_mod.generate_scene_assets
    scene_assets_mod.generate_scene_assets = fake_generate  # type: ignore[assignment]
    try:
        runner = make_generate_scene_assets_runner(
            image_skill_factory=lambda: _FakeSkill(),
            quota_checker=lambda *_args, **_kwargs: False,
        )
        result = await runner(
            {
                "global_assets": {"characters": [{"name": "安然"}]},
                "scene_packages": [{"scene_id": "1"}],
                "image_model": "seeddream-5.0",
                "image_ratio": "9:16",
                "image_size": "2K",
                "reference_brief": "主图",
                "target_assets": [{"asset_type": "character", "name": "安然"}],
            }
        )
    finally:
        scene_assets_mod.generate_scene_assets = original  # type: ignore[assignment]

    assert result["ok"] is True
    assert calls[0]["model"] == "seeddream-5.0"
    assert calls[0]["reference_brief"] == "主图"
    assert calls[0]["target_assets"] == [{"asset_type": "character", "name": "安然"}]
