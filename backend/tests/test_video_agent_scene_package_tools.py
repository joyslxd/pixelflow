"""批次 B：场景包 / 参考图 Tool 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext
from pixelflow.video_agent.tools.registry import VideoToolRegistry, VideoToolValidationError
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
    script_in = payload.get("script")
    default_script = {
        "version": 1,
        "status": "ready",
        "content": "# 脚本\n镜头1",
        "aspect_ratio": "9:16",
        "ending_cta": "none",
        "missing_requirements": [],
    }
    if isinstance(script_in, dict):
        script = {**default_script, **script_in}
    else:
        script = dict(default_script)
    base: dict = {
        "script_plan_confirmed": True,
        "script_plan_confirmed_version": 1,
        "script": script,
    }
    for key, value in payload.items():
        if key == "script":
            continue
        base[key] = value
    return VideoToolContext(
        user_id="user-1",
        plan_id="plan-1",
        step_id="step-1",
        workspace=VideoWorkspace(
            workspace_id="ws-1",
            conversation_id="conversation-1",
            payload=base,
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
                "outline": {
                    "content": "## 分镜提示词\n0—10秒｜开场\n女主推门进酒店",
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
    plan_md = str(port.prepare_calls[0]["plan_markdown"])
    assert "角色设定" in plan_md
    assert "分镜提示词" in plan_md
    assert "女主推门进酒店" in plan_md
    assert port.prepare_calls[0]["form_values"].get("video_duration_sec") == 60
    assert isinstance(result.workspace_patch.get("creation_contract"), dict)


@pytest.mark.asyncio
async def test_prepare_scene_packages_rejects_stale_script_confirmation() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-must-not-start",
            status="polling",
            result={},
        )
    )
    tool = PrepareScenePackagesTool(operation_port=port)

    with pytest.raises(VideoToolValidationError, match="当前版本"):
        await tool.execute(
            _context(
                script={"version": 2},
                script_plan_confirmed_version=1,
            ),
            {},
        )

    assert port.prepare_calls == []


@pytest.mark.asyncio
async def test_prepare_scene_packages_bumps_attempt_when_packages_exist() -> None:
    """已有场景包时自动抬高 attempt，避免幂等复用旧成功 Operation。"""

    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-prepare-2",
            status="succeeded",
            result={
                "message": "已重新生成",
                "global_assets": {"characters": []},
                "scene_packages": [{"scene_id": "s1"}],
                "creation_contract": {"image_model": "seeddream-5.0"},
            },
        )
    )
    tool = PrepareScenePackagesTool(operation_port=port)
    result = await tool.execute(
        _context(
            script={"status": "ready", "content": "# 脚本\n镜头1"},
            scene_packages=[{"scene_id": "s1", "title": "旧包"}],
            scene_package_job={"job_id": "job-old", "status": "succeeded", "attempt": 1},
        ),
        {"attempt": 1},
    )
    assert port.prepare_calls[0]["attempt"] == 2
    job = result.workspace_patch.get("scene_package_job")
    assert isinstance(job, dict)
    assert job.get("attempt") == 2


@pytest.mark.asyncio
async def test_prepare_scene_packages_requires_script_plan_confirmed() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(job_id="job-x", status="succeeded", result={})
    )
    tool = PrepareScenePackagesTool(operation_port=port)
    with pytest.raises(VideoToolValidationError, match="确认脚本方案"):
        await tool.execute(
            _context(
                script_plan_confirmed=False,
                script={"status": "ready", "content": "# 脚本\n镜头1"},
            ),
            {},
        )
    assert port.prepare_calls == []


@pytest.mark.asyncio
async def test_prepare_scene_packages_requires_production_fields() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(job_id="job-x", status="succeeded", result={})
    )
    tool = PrepareScenePackagesTool(operation_port=port)
    with pytest.raises(VideoToolValidationError, match="生产字段"):
        await tool.execute(
            _context(
                script_plan_confirmed=True,
                script={
                    "status": "ready",
                    "content": "# 脚本\n镜头1",
                    "aspect_ratio": "9:16",
                    "ending_cta": "",
                    "missing_requirements": ["结尾行动引导"],
                },
            ),
            {},
        )
    assert port.prepare_calls == []


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
async def test_generate_scene_assets_retries_when_completion_event_conflict() -> None:
    from pixelflow.video_agent.tools.registry import VideoToolExecutionError

    class FlakyPort(FakePackagePort):
        def __init__(self) -> None:
            super().__init__(
                ScenePackageOperationJob(
                    job_id="job-assets-retry",
                    status="succeeded",
                    result={
                        "ok": True,
                        "message": "参考图完成",
                        "global_assets": {"characters": [{"name": "安然", "images": ["https://cdn/a.png"]}]},
                        "scene_packages": [],
                        "failed_assets": [],
                    },
                )
            )

        async def start_generate_scene_assets(self, context, **kwargs):
            self.assets_calls.append({"context": context, **kwargs})
            if len(self.assets_calls) == 1:
                raise VideoToolExecutionError("场景包/参考图 Operation 完成事件不唯一")
            return self.job

    port = FlakyPort()
    tool = GenerateSceneAssetsTool(operation_port=port)
    result = await tool.execute(
        _context(
            global_assets={"characters": [{"name": "安然"}]},
            scene_packages=[{"scene_id": "1"}],
            scene_asset_job={"job_id": "old-job", "attempt": 1, "status": "succeeded"},
        ),
        {"image_model": "seeddream-5.0", "image_ratio": "9:16", "image_size": "2K"},
    )
    assert result.public_summary == "参考图完成"
    assert len(port.assets_calls) == 2
    assert port.assets_calls[0]["attempt"] == 2
    assert port.assets_calls[1]["attempt"] == 3


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
async def test_generate_scene_assets_tool_projects_partial_result_for_retry() -> None:
    port = FakePackagePort(
        ScenePackageOperationJob(
            job_id="job-assets-partial-1",
            status="succeeded",
            result={
                "ok": False,
                "message": "部分参考图已生成，剩余素材可继续重试",
                "global_assets": {
                    "scenes": [
                        {"asset_id": "scene-1", "images": ["https://cdn.example/1.png"]},
                        {"asset_id": "scene-2", "images": []},
                    ]
                },
                "scene_packages": [{"scene_id": "scene-1"}],
                "failed_assets": [
                    {
                        "asset_id": "scene-2",
                        "asset_type": "scene_image",
                        "retry_pending": True,
                    }
                ],
            },
        )
    )

    result = await GenerateSceneAssetsTool(operation_port=port).execute(
        _context(
            global_assets={
                "scenes": [
                    {"asset_id": "scene-1", "name": "场景1", "image_prompt": "场景1"},
                    {"asset_id": "scene-2", "name": "场景2", "image_prompt": "场景2"},
                ]
            },
            scene_packages=[{"scene_id": "scene-1"}],
        ),
        {"image_model": "seeddream-5.0"},
    )

    assert result.workspace_patch["scene_asset_job"]["status"] == "partial"
    assert result.workspace_patch["scene_asset_failures"] == [
        {
            "asset_id": "scene-2",
            "asset_type": "scene_image",
            "retry_pending": True,
        }
    ]


@pytest.mark.asyncio
async def test_generate_scene_assets_job_preserves_partial_business_result() -> None:
    from pixelflow.agent_runtime.jobs import ProviderJobAdapter
    from pixelflow.video_agent.adapters.domain_jobs import GenerateSceneAssetsJobService

    async def runner(_request):
        return {
            "ok": False,
            "message": "部分参考图已生成",
            "global_assets": {
                "scenes": [
                    {"asset_id": "scene-1", "images": ["https://cdn.example/1.png"]},
                    {"asset_id": "scene-2", "images": []},
                ]
            },
            "scene_packages": [{"scene_id": "scene-1"}],
            "failed_assets": [
                {
                    "asset_id": "scene-2",
                    "asset_type": "scene_image",
                    "retry_pending": True,
                }
            ],
        }

    snapshot = await ProviderJobAdapter(
        GenerateSceneAssetsJobService(runner=runner)
    ).start(
        {"global_assets": {}, "scene_packages": []},
        authorization="local",
        idempotency_key="idem-assets-partial-1",
    )

    assert snapshot.outcome.value == "succeeded"
    assert snapshot.result["ok"] is False
    assert snapshot.result["failed_assets"][0]["asset_id"] == "scene-2"


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


@pytest.mark.asyncio
async def test_generate_scene_assets_runner_wires_workspace_progress() -> None:
    """有 workspace_progress 时必须把 on_progress 传给领域生图。"""

    from pixelflow.video_agent.adapters.domain_jobs import make_generate_scene_assets_runner

    progress_calls: list[tuple[dict[str, object], dict[str, object]]] = []
    generate_kwargs: list[dict[str, object]] = []

    class _FakeSkill:
        pass

    async def fake_generate(**kwargs):
        generate_kwargs.append(kwargs)
        on_progress = kwargs.get("on_progress")
        assert callable(on_progress)
        await on_progress(
            {
                "completed": 1,
                "total": 1,
                "asset_name": "安然",
                "ok": True,
                "global_assets": {
                    "characters": [{"name": "安然", "three_view_images": ["https://cdn.example/a.png"]}],
                },
                "scene_packages": [],
            }
        )
        return {
            "ok": True,
            "global_assets": {"characters": [{"name": "安然", "three_view_images": ["https://cdn.example/a.png"]}]},
            "scene_packages": [],
            "failed_assets": [],
            "message": "ok",
        }

    async def workspace_progress(request, payload):
        progress_calls.append((dict(request), dict(payload)))

    import pixelflow.generate.scene_assets as scene_assets_mod

    original = scene_assets_mod.generate_scene_assets
    scene_assets_mod.generate_scene_assets = fake_generate  # type: ignore[assignment]
    try:
        runner = make_generate_scene_assets_runner(
            image_skill_factory=lambda: _FakeSkill(),
            quota_checker=lambda *_args, **_kwargs: False,
            workspace_progress=workspace_progress,
        )
        result = await runner(
            {
                "global_assets": {"characters": [{"name": "安然"}]},
                "scene_packages": [],
                "user_id": "u1",
                "workspace_id": "ws-1",
            }
        )
    finally:
        scene_assets_mod.generate_scene_assets = original  # type: ignore[assignment]

    assert result["ok"] is True
    assert len(progress_calls) == 1
    assert progress_calls[0][0]["workspace_id"] == "ws-1"
    assert progress_calls[0][1]["completed"] == 1
    assert generate_kwargs[0]["on_progress"] is not None
