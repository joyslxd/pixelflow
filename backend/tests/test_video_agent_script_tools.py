from __future__ import annotations

import pytest

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.tools import VideoToolContext, VideoToolRegistry
from pixelflow.video_agent.tools.script import BrainstormScriptTool, ImportScriptTool

MATURE_SCRIPT = """# 夏日保温杯短视频

## 视频规格

- 时长：15 秒
- 画幅：9:16

## 镜头脚本

1. 前 3 秒展示冰块落入杯中，旁白介绍长效保冷。
2. 中段展示通勤携带和防漏测试。
3. 结尾展示商品并引导立即购买。
"""


def _context(payload: dict | None = None) -> VideoToolContext:
    return VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload=payload or {},
        ),
    )


def _patch_import_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_structure(**_kwargs):  # noqa: ANN003
        return {
            "characters": {
                "stage": "characters",
                "title": "角色/场景/道具设定 /characters",
                "content": "## 角色设定\n\n主角\n\n## 场景设定\n\n通勤\n\n## 道具与产品设定\n\n保温杯",
                "artifact_ref": "artifact:video-script-characters-test",
                "change_summary": "从用户成稿拆解角色/场景/道具设定",
            },
            "outline": {
                "stage": "outline",
                "title": "分镜大纲 /outline",
                "content": "## 分镜提示词\n\n1. 0-3秒 冰块入杯",
                "artifact_ref": "artifact:video-script-outline-test",
                "change_summary": "从用户成稿拆解分镜提示词",
            },
        }

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script_skill_pipeline.extract_imported_script_structure",
        _fake_structure,
    )


def test_split_import_structure_markdown_separates_shots() -> None:
    from pixelflow.video_agent.tools.script_skill_pipeline import (
        _split_import_structure_markdown,
    )

    settings, shots = _split_import_structure_markdown(
        "## 角色设定\nA\n\n## 场景设定\nB\n\n## 道具与产品设定\nC\n\n"
        "## 分镜提示词\n1. 0-3秒\n"
    )
    assert "角色设定" in settings
    assert "道具" in settings
    assert shots.startswith("## 分镜提示词")
    assert "0-3秒" in shots


@pytest.mark.asyncio
async def test_import_script_keeps_workspace_production_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """导入成稿时不得用正文分析覆盖用户已补的画幅/CTA。"""

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        assert "【本轮指令】" in text
        assert "9:16" in text or "9：16" in text
        return ProductionFieldsAnalysis(
            duration_sec=180,
            missing=("视频画幅", "结尾行动引导"),
            has_aspect_ratio=False,
            has_ending_cta=False,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    context = _context(
        payload={
            "form_values": {"video_ratio": "9:16", "ending_cta": "none"},
            "script": {
                "aspect_ratio": "9:16",
                "ending_cta": "none",
                "missing_requirements": [],
            },
            "latest_input": (
                f"{MATURE_SCRIPT}\n\n【本轮指令】9：16，不需要"
            ),
        }
    )
    result = await ImportScriptTool().execute(
        context,
        {"markdown": MATURE_SCRIPT},
    )
    script = result.workspace_patch["script"]
    assert script["missing_requirements"] == []
    assert script["aspect_ratio"] == "9:16"
    assert script["ending_cta"] == "none"
    assert "仍缺少" not in result.public_summary


@pytest.mark.asyncio
async def test_import_script_creates_ready_version_without_plan_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    result = await ImportScriptTool().execute(
        _context(),
        {"markdown": MATURE_SCRIPT},
    )

    script = result.workspace_patch["script"]
    assert script["source"] == "user_import"
    assert script["version"] == 1
    assert script["status"] == "ready"
    assert script["review_required"] is False
    assert script["missing_requirements"] == []
    assert script["duration_sec"] == 15
    assert result.requires_confirmation is False
    assert result.artifact_refs == (script["artifact_ref"],)
    assert result.workspace_patch["script_versions"] == [script]
    pipeline = result.workspace_patch["script_pipeline"]
    assert pipeline["episode"]["source"] == "user_complete_script"
    assert "角色设定" in pipeline["characters"]["content"]
    assert "分镜提示词" in pipeline["outline"]["content"]
    assert "已拆解角色/场景/道具与分镜提示词" in result.public_summary


@pytest.mark.asyncio
async def test_import_script_structured_patch_passes_registry_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)

    result = await VideoToolRegistry([ImportScriptTool()]).execute(
        _context(),
        "import_script",
        {"markdown": MATURE_SCRIPT},
    )

    assert set(result.workspace_patch) >= {
        "script",
        "script_versions",
        "script_pipeline",
        "script_entry_path",
    }
    assert result.workspace_patch["script_entry_path"] == "polish"
    assert result.workspace_patch.get("awaiting_production_fields") is False


@pytest.mark.asyncio
async def test_import_script_fills_markdown_from_workspace_when_args_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """思考流 steps 常给 arguments={}；服务端必须从 workspace 注入 markdown。"""

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        assert "夏日保温杯" in text
        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    result = await ImportScriptTool().execute(
        _context(
            payload={
                "script": {"content": MATURE_SCRIPT, "source": "intake_draft"},
                "latest_input": MATURE_SCRIPT,
            }
        ),
        {},
    )
    script = result.workspace_patch["script"]
    assert script["source"] == "user_import"
    assert "夏日保温杯" in str(script.get("content") or "")


@pytest.mark.asyncio
async def test_import_timecode_script_does_not_mark_duration_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """思考流已能认出 180s 时，导入结论不得再报「仍缺少：视频时长」。"""

    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=180,
            missing=("视频画幅", "结尾行动引导"),
            has_aspect_ratio=False,
            has_ending_cta=False,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    script_md = (
        "0—10秒｜开场\n【剧情/动作】涂防晒。\n"
        "10—20秒｜转折\n【剧情/动作】上底妆。\n"
        "170—180秒｜收束\n【剧情/动作】字幕结束。\n"
    )
    result = await ImportScriptTool().execute(
        _context(),
        {"markdown": script_md},
    )
    script = result.workspace_patch["script"]
    assert script["duration_sec"] == 180
    assert "视频时长" not in script["missing_requirements"]
    assert "视频画幅" in script["missing_requirements"]
    assert "结尾行动引导" in script["missing_requirements"]
    assert "已识别时长 180 秒" in result.public_summary
    assert "仍缺少：视频画幅、结尾行动引导" in result.public_summary
    assert "视频时长" not in result.public_summary.split("仍缺少：")[-1]


@pytest.mark.asyncio
async def test_import_script_replay_reuses_same_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    tool = ImportScriptTool()
    first = await tool.execute(_context(), {"markdown": MATURE_SCRIPT})
    replay = await tool.execute(
        _context(first.workspace_patch),
        {"markdown": MATURE_SCRIPT},
    )

    assert replay.workspace_patch == {}
    assert replay.artifact_refs == first.artifact_refs


class FakeVideoDomainAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def brainstorm_script(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "# 创意脚本草稿\n\n1. 用反差开场。\n2. 展示商品卖点。\n3. 引导购买。"


@pytest.mark.asyncio
async def test_brainstorm_script_appends_versioned_draft_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    _patch_import_structure(monkeypatch)
    imported = await ImportScriptTool().execute(
        _context(),
        {"markdown": MATURE_SCRIPT},
    )
    adapter = FakeVideoDomainAdapter()
    result = await BrainstormScriptTool(adapter=adapter).execute(
        _context(imported.workspace_patch),
        {
            "product_info": {"product_name": "保温杯"},
            "video_params": {"duration_sec": 15, "ratio": "9:16"},
            "creative_direction": "通勤反差",
        },
    )

    script = result.workspace_patch["script"]
    assert script["source"] == "agent_brainstorm"
    assert script["version"] == 2
    assert script["status"] == "draft"
    assert script["review_required"] is True
    assert len(result.workspace_patch["script_versions"]) == 2
    assert result.requires_confirmation is False
    assert adapter.calls[0]["creative_direction"] == "通勤反差"


@pytest.mark.asyncio
async def test_brainstorm_script_emits_public_progress_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_analysis(*, text: str, **_kwargs):  # noqa: ANN001, ARG001
        from pixelflow.video_agent.production_fields import ProductionFieldsAnalysis

        return ProductionFieldsAnalysis(
            duration_sec=15,
            missing=(),
            has_aspect_ratio=True,
            has_ending_cta=True,
        )

    monkeypatch.setattr(
        "pixelflow.video_agent.tools.script.analyze_production_fields_with_llm",
        _fake_analysis,
    )
    phases: list[tuple[str, str]] = []

    async def report_progress(message: str, *, phase: str) -> None:
        phases.append((phase, message))

    context = VideoToolContext(
        user_id="user-1",
        workspace=VideoWorkspace(
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            payload={},
        ),
        plan_id="plan-1",
        step_id="step-1",
        report_progress=report_progress,
    )
    await BrainstormScriptTool(adapter=FakeVideoDomainAdapter()).execute(
        context,
        {
            "product_info": {"product_name": "保温杯"},
            "video_params": {"duration_sec": 15, "ratio": "9:16"},
            "creative_direction": "通勤反差",
        },
    )

    assert [phase for phase, _ in phases] == [
        "prepare_inputs",
        "invoke_skill",
        "await_model",
        "format_draft",
    ]
    assert "brief_generate" in phases[1][1]
    assert "大模型" in phases[2][1]
