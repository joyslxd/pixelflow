"""Workspace digest 分镜视频状态 + Tool 口述强制补发。"""

from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import AIMessage

from pixelflow.video_agent.contracts import VideoWorkspace
from pixelflow.video_agent.middleware.tool_commitment import (
    VideoToolCommitmentMiddleware,
    narrated_forceable_tool,
)
from pixelflow.video_agent.workspace.digest import (
    build_workspace_digest,
    summarize_scene_asset_status,
    summarize_scene_video_status,
)


def _workspace(payload: dict) -> VideoWorkspace:
    now = datetime.now(UTC)
    return VideoWorkspace(
        workspace_id="ws-1",
        conversation_id="c-1",
        revision=1,
        payload=payload,
        created_at=now,
        updated_at=now,
    )


def test_summarize_scene_video_status_counts_ready_polling_failed() -> None:
    summary = summarize_scene_video_status(
        {
            "scene_video_progress": {"completed": 1, "total": 3},
            "scenes": [
                {
                    "scene_id": "scene-1",
                    "scene_index": 1,
                    "variants": [{"video_url": "https://cdn.example/a.mp4"}],
                },
                {
                    "scene_id": "scene-2",
                    "scene_index": 2,
                    "edit_status": "重新生成中",
                    "generation_jobs": [{"status": "polling"}],
                },
                {
                    "scene_id": "scene-3",
                    "scene_index": 3,
                    "generation_jobs": [{"status": "failed", "error": "x"}],
                },
            ],
        }
    )
    assert summary["scene_videos_ready_count"] == 1
    assert summary["scene_videos_polling_count"] == 1
    assert summary["scene_videos_failed_count"] == 1
    assert summary["scene_video_progress_completed"] == 1
    assert summary["scene_video_progress_total"] == 3
    assert [item["state"] for item in summary["scene_video_states"]] == [
        "ready",
        "polling",
        "failed",
    ]


def test_build_workspace_digest_includes_scene_video_fields() -> None:
    digest = build_workspace_digest(
        _workspace(
            {
                "script": {"content": "# 脚本", "version": 3},
                "script_plan_confirmed": True,
                "script_plan_confirmed_version": 3,
                "scenes": [
                    {
                        "scene_id": "scene-1",
                        "scene_index": 1,
                        "variants": [{"video_url": "https://cdn.example/a.mp4"}],
                    }
                ]
            }
        )
    )
    assert digest["scene_count"] == 1
    assert digest["scene_videos_ready_count"] == 1
    assert digest["scene_videos_polling_count"] == 0
    assert digest["script_version"] == 3
    assert digest["script_plan_confirmed"] is True
    assert digest["script_plan_confirmed_version"] == 3


def test_summarize_scene_asset_status_distinguishes_partial_from_ready() -> None:
    summary = summarize_scene_asset_status(
        {
            "global_assets": {
                "characters": [
                    {
                        "asset_id": "character-host",
                        "three_view_images": ["https://cdn.example/host.png"],
                    }
                ],
                "scenes": [
                    {"asset_id": "scene-room", "images": []},
                    {
                        "asset_id": "scene-desk",
                        "images": ["https://cdn.example/desk.png"],
                    },
                ],
                "props": [{"asset_id": "prop-product", "images": []}],
            },
            "scene_asset_failures": [
                {
                    "asset_id": "scene-room",
                    "asset_type": "scene_image",
                    "retry_pending": True,
                }
            ],
        }
    )

    assert summary["scene_asset_status"] == "partial"
    assert summary["scene_asset_required_count"] == 4
    assert summary["scene_asset_ready_count"] == 2
    assert summary["scene_asset_missing_count"] == 2
    assert summary["scene_asset_failed_count"] == 1
    assert summary["scene_assets_ready"] is False
    assert summary["scene_asset_missing_targets"] == [
        {"asset_id": "scene-room", "asset_type": "scene_image"},
        {"asset_id": "prop-product", "asset_type": "prop_image"},
    ]


def test_build_workspace_digest_marks_assets_ready_only_when_every_asset_has_an_image() -> None:
    digest = build_workspace_digest(
        _workspace(
            {
                "global_assets": {
                    "characters": [
                        {
                            "asset_id": "character-host",
                            "three_view_images": ["https://cdn.example/host.png"],
                        }
                    ],
                    "scenes": [
                        {
                            "asset_id": "scene-room",
                            "images": ["https://cdn.example/room.png"],
                        }
                    ],
                    "props": [],
                }
            }
        )
    )

    assert digest["has_scene_asset_images"] is True
    assert digest["scene_assets_ready"] is True
    assert digest["scene_asset_status"] == "ready"
    assert digest["scene_asset_missing_count"] == 0


def test_narrated_forceable_tool_detects_inspect_intent() -> None:
    message = AIMessage(
        content="",
        additional_kwargs={
            "reasoning_content": (
                "从 digest 看 scene_count 是 14，但没有 scene_videos 状态。"
                "让我调用 inspect_video_workspace 来查看具体哪些场景已经生成了视频。"
            )
        },
    )
    assert narrated_forceable_tool(message) == "inspect_video_workspace"
    assert narrated_forceable_tool(AIMessage(content="成片已就绪，无需再查。")) is None
    assert (
        narrated_forceable_tool(
            AIMessage(
                content="ok",
                tool_calls=[
                    {
                        "name": "inspect_video_workspace",
                        "args": {},
                        "id": "t1",
                        "type": "tool_call",
                    }
                ],
            )
        )
        is None
    )


def test_narrated_forceable_tool_maps_merge_videos_alias() -> None:
    message = AIMessage(
        content="好的，所有14个场景的视频都已生成完毕，现在合并。",
        additional_kwargs={
            "reasoning_content": (
                "需要调用merge_videos工具来合并这些视频。"
                "工具参数只需要指定workspace_id，目标格式为MP4。"
            )
        },
    )
    assert narrated_forceable_tool(message) == "compose_or_export_video"


def test_tool_commitment_middleware_injects_tool_call() -> None:
    middleware = VideoToolCommitmentMiddleware()
    message = AIMessage(
        content="先检查一下各场景视频的生成进度。",
        additional_kwargs={
            "reasoning_content": "让我调用 inspect_video_workspace 确认。"
        },
    )

    class _Resp:
        def __init__(self, result):
            self.result = result

        def override(self, *, result):
            return _Resp(result)

    out = middleware._maybe_force_tool(_Resp([message]))
    forced = out.result[0]
    assert isinstance(forced, AIMessage)
    assert forced.tool_calls
    assert forced.tool_calls[0]["name"] == "inspect_video_workspace"
    assert forced.content == ""


def test_tool_commitment_middleware_forces_compose_with_mp4_args() -> None:
    middleware = VideoToolCommitmentMiddleware()
    message = AIMessage(
        content="现在合并。",
        additional_kwargs={"reasoning_content": "直接调用 merge_videos 即可。"},
    )

    class _Resp:
        def __init__(self, result):
            self.result = result

        def override(self, *, result):
            return _Resp(result)

    out = middleware._maybe_force_tool(_Resp([message]))
    forced = out.result[0]
    assert forced.tool_calls[0]["name"] == "compose_or_export_video"
    assert forced.tool_calls[0]["args"] == {"output_type": "mp4"}


def test_tool_commitment_skips_force_when_compose_awaiting_confirmation() -> None:
    """确认单已返回后，口述「合并」不得再强制补发 compose。"""

    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage, ToolMessage

    middleware = VideoToolCommitmentMiddleware()
    message = AIMessage(
        content="请在界面确认后继续合并。",
        additional_kwargs={"reasoning_content": "需要再次调用 compose_or_export_video。"},
    )
    request = ModelRequest(
        messages=[
            HumanMessage(content="合并视频吧"),
            ToolMessage(
                content=(
                    '{"tool_name":"compose_or_export_video",'
                    '"requires_confirmation":true,'
                    '"public_summary":"请确认"}'
                ),
                tool_call_id="call-1",
                name="compose_or_export_video",
            ),
        ],
        model=None,  # type: ignore[arg-type]
    )

    class _Resp:
        def __init__(self, result):
            self.result = result

        def override(self, *, result):
            return _Resp(result)

    out = middleware._maybe_force_tool(_Resp([message]), request=request)
    assert out.result[0].tool_calls in (None, [])
