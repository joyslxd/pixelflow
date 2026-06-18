"""Skill 能力接口定义。

这里的 ``Protocol`` 可以类比成 Java 的 interface。PixelFlow 图节点只依赖这些抽象，
不直接依赖 Borgrise、FFmpeg、剪映或它们的 HTTP/命令行细节。这样后续替换供应商、
把能力搬到 sandbox、或增加新的实现，都不需要改 ``nodes.py`` 的流程编排。

当前 MVP 以进程内实现为主；P1 可以把同一能力迁移到 sandbox 执行。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class GenerationResult:
    """单次生成调用的统一返回 DTO。

    不同供应商返回结构不一致，skill 实现负责把它们映射到这里。图节点只看稳定
    合同：成功时 ``ok=True`` 并有 ``url``；失败时 ``ok=False`` 并有 ``error``。
    """

    ok: bool
    url: str | None = None
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EditResult:
    """剪辑/装配调用的统一返回 DTO。

    ``output_path`` 指向产物路径；``kind`` 告诉图节点这是什么类型：``"draft"``
    表示剪映 skill 产出的可编辑草稿目录，最终渲染还依赖剪映；``"video"`` 表示
    FFmpeg skill 已经产出 mp4 成片。
    """

    ok: bool
    output_path: str | None = None
    error: str | None = None
    kind: str = "draft"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryboardResult:
    """参考视频拆解调用的统一返回 DTO。

    ``shots`` 是供应商 storyboard 列表。字段名仍可能是供应商风格；进入 Brief
    prompt 前会由纯逻辑 ``summarize_storyboards`` 再做一次归一化。
    """

    ok: bool
    shots: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VideoGenerationSkill(Protocol):
    """GENERATE 阶段依赖的视频生成能力接口。

    实现类负责供应商合同：鉴权、请求头、端点、轮询、错误归一化。生成参数按调用
    传入，这里不硬编码具体模型或供应商行为。
    """

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult: ...

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        model: str | None = None,
    ) -> GenerationResult: ...


class VideoEditSkill(Protocol):
    """EDIT 阶段依赖的视频剪辑/渲染能力接口。

    实现类负责具体编辑器合同，如剪映草稿格式、FFmpeg 命令、媒体下载和探测。
    图节点只传 Timeline 计划，不写任何具体编辑器细节。
    """

    async def render(self, timeline: dict, *, draft_name: str, output_root: str | None = None) -> EditResult: ...


class VideoDecomposeSkill(Protocol):
    """INTAKE 阶段依赖的参考视频拆解能力接口。

    实现类负责供应商合同。当前 Borgrise 只用博观的
    ``decompose_video_to_storyboard`` 视频理解端点，没有单独接 OCR/ASR。
    """

    async def decompose_video_to_storyboard(self, video_url: str) -> StoryboardResult: ...


def get_video_skill() -> VideoGenerationSkill:
    """返回当前配置的视频生成 skill。

    这是视频生成实现的唯一替换点。MVP 默认返回进程内 Borgrise 实现；
    ``PIXELFLOW_VIDEO_SKILL`` 预留给后续切换其它实现，例如 sandbox 执行版。
    """
    impl = os.environ.get("PIXELFLOW_VIDEO_SKILL", "borgrise")
    if impl == "borgrise":
        from pixelflow.skills.borgrise import BorgriseSkill

        return BorgriseSkill()
    raise ValueError(f"Unknown video skill implementation: {impl!r}")


def get_video_edit_skill() -> VideoEditSkill:
    """返回当前配置的视频剪辑 skill，也就是 EDIT 阶段替换点。

    默认是剪映草稿 skill（pyJianYingDraft）。设置 ``PIXELFLOW_EDIT_SKILL=ffmpeg``
    时会切到无界面的 FFmpeg 渲染器，直接产出 mp4。
    """
    impl = os.environ.get("PIXELFLOW_EDIT_SKILL", "jianying")
    if impl == "jianying":
        from pixelflow.skills.jianying import JianYingEditSkill

        return JianYingEditSkill()
    if impl == "ffmpeg":
        from pixelflow.skills.ffmpeg import FFmpegEditSkill

        return FFmpegEditSkill()
    raise ValueError(f"Unknown video edit skill implementation: {impl!r}")


def get_video_decompose_skill() -> VideoDecomposeSkill:
    """返回当前配置的参考视频拆解 skill，也就是 INTAKE 阶段替换点。"""
    impl = os.environ.get("PIXELFLOW_DECOMPOSE_SKILL", "borgrise")
    if impl == "borgrise":
        from pixelflow.skills.borgrise import BorgriseSkill

        return BorgriseSkill()
    raise ValueError(f"Unknown video decompose skill implementation: {impl!r}")
