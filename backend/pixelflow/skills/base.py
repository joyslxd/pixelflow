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

MEDIA_SKILL_ENV = "PIXELFLOW_MEDIA_SKILL"
DEFAULT_MEDIA_SKILL = "borgrise"
QUOTA_INSUFFICIENT_STATUS_CODE = 402
QUOTA_INSUFFICIENT_KEYWORDS = (
    "额度不足",
    "余额不足",
    "没有有效的额度",
    "有效的额度",
    "扣费失败",
    "剩余额度",
    "充值",
    "quota insufficient",
    "insufficient quota",
    "insufficient balance",
    "payment required",
    "not enough quota",
)


def is_quota_insufficient(value: Any) -> bool:
    """判断供应商或 content-app 返回是否表示额度不足。

    content-app 通用拦截器会返回 HTTP 402 和 ``{"success":false,"message":"..."}``；
    一些业务接口也可能在 200 包装中返回类似文案。这里做宽松识别，供 router 和
    skill 统一把流程暂停在可恢复状态。
    """
    if value is None:
        return False
    if isinstance(value, dict):
        if value.get("quota_insufficient") is True:
            return True
        if value.get("status_code") == QUOTA_INSUFFICIENT_STATUS_CODE:
            return True
        haystack = " ".join(
            str(value.get(key, ""))
            for key in ("message", "msg", "error", "detail", "code", "status")
        ).lower()
        if any(keyword.lower() in haystack for keyword in QUOTA_INSUFFICIENT_KEYWORDS):
            return True
        return any(is_quota_insufficient(child) for child in value.values())
    if isinstance(value, list):
        return any(is_quota_insufficient(item) for item in value)
    text = str(value).lower()
    return any(keyword.lower() in text for keyword in QUOTA_INSUFFICIENT_KEYWORDS)


def quota_resume_message(message: str | None = None) -> str:
    detail = (message or "额度不足").strip()
    return f"{detail}。当前生成已暂停，请充值后回到本对话继续执行。"


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
class ImageGenerationResult:
    """图片生成调用的统一返回 DTO。"""

    ok: bool
    images: list[dict[str, Any]] = field(default_factory=list)
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


@dataclass
class BatchStoryboardResult:
    """批量参考视频拆解调用的统一返回 DTO。"""

    ok: bool
    storyboards: list[dict[str, Any]] = field(default_factory=list)
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaLinkExtractionResult:
    """媒体链接识别调用的统一返回 DTO。"""

    ok: bool
    links: list[str] = field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoFlawAnalysisResult:
    """视频穿帮分析调用的统一返回 DTO。"""

    ok: bool
    flaw_analysis_markdown: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    affected_scene_ids: list[str] = field(default_factory=list)
    revision_prompt: str = ""
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoQualityReviewResult:
    """视频综合质检调用的统一返回 DTO。"""

    ok: bool
    summary_markdown: str = ""
    flaw_analysis_markdown: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    affected_scene_ids: list[str] = field(default_factory=list)
    revision_prompt: str = ""
    task_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PptGenerationResult:
    """智能 PPT 生成调用的统一返回 DTO。"""

    ok: bool
    task_id: str | None = None
    smart_ppt_project_id: int | None = None
    summary: str = ""
    content_json: Any = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    image_url: str | None = None
    ppt_url: str | None = None
    filename: str | None = None
    slide_count: int | None = None
    error: str | None = None
    quota_insufficient: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class VideoGenerationSkill(Protocol):
    """GENERATE 阶段依赖的视频生成能力接口。

    实现类负责供应商合同：鉴权、请求头、端点、轮询、错误归一化。生成参数按调用
    传入，这里不硬编码具体模型或供应商行为。
    """

    async def text_to_video(
        self,
        prompt: str,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def image_to_video(
        self,
        image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def two_image_to_video(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def extend_video(
        self,
        video_url: str,
        prompt: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def reference_mode_video(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        video_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def edit_video(
        self,
        ref_video: str,
        prompt: str | None = None,
        ref_image: str | None = None,
        duration: int = 10,
        ratio: str = "9:16",
        size: str = "720p",
        model: str | None = None,
        sound: str = "on",
    ) -> GenerationResult: ...

    async def merge_videos(
        self,
        video_urls: list[str],
        duration: int = 30,
        size: str = "1080p",
        model: str | None = None,
    ) -> GenerationResult: ...


class ImageGenerationSkill(Protocol):
    """图片生成能力接口。

    实现类负责调用文生图、参考图生图、图片编辑等供应商接口，并把异步轮询后的
    图片 URL 归一成 ``ImageGenerationResult``。
    """

    async def text_to_image(
        self,
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        num_images: int = 1,
    ) -> ImageGenerationResult: ...

    async def reference_image(
        self,
        reference_images: list[str],
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        max_images: int = 1,
    ) -> ImageGenerationResult: ...

    async def image_edit(
        self,
        image_url: str,
        prompt: str,
        model: str | None = None,
        ratio: str = "1:1",
        size: str = "1080p",
        max_images: int = 1,
    ) -> ImageGenerationResult: ...

    async def multi_image_fusion(
        self,
        image_urls: list[str],
        prompt: str,
        ratio: str = "1:1",
        size: str = "1080p",
        model: str | None = None,
        num_images: int = 1,
    ) -> ImageGenerationResult: ...


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

    async def batch_decompose_video_to_storyboard(self, video_urls: list[str]) -> BatchStoryboardResult: ...


class MediaLinkExtractionSkill(Protocol):
    """媒体链接识别能力接口。"""

    async def extract_media_links(self, text: str, materials: list[dict[str, Any]] | None = None) -> MediaLinkExtractionResult: ...


class VideoFlawAnalysisSkill(Protocol):
    """视频穿帮分析能力接口。"""

    async def analyze_video_flaws(
        self,
        merged_video_url: str,
        scene_videos: list[dict[str, Any]],
        scene_packages: list[dict[str, Any]] | None = None,
        brief: dict[str, Any] | None = None,
        materials: list[dict[str, Any]] | None = None,
        user_feedback: str | None = None,
    ) -> VideoFlawAnalysisResult: ...


class VideoQualityReviewSkill(Protocol):
    """视频综合质检能力接口。"""

    async def review_video_quality(
        self,
        merged_video_url: str,
        scene_videos: list[dict[str, Any]],
        scene_packages: list[dict[str, Any]] | None = None,
        brief: dict[str, Any] | None = None,
        materials: list[dict[str, Any]] | None = None,
        user_feedback: str | None = None,
        checks: list[str] | None = None,
        platform: str | None = None,
        ratio: str | None = None,
        size: str | None = None,
    ) -> VideoQualityReviewResult: ...


class PptGenerationSkill(Protocol):
    """智能 PPT 生成能力接口。"""

    async def generate_ppt_summary(
        self,
        topic: str,
        ppt_style: str,
        file_urls: list[str],
        smart_ppt_project_id: int | None = None,
    ) -> PptGenerationResult: ...

    async def update_ppt_summary(
        self,
        original_outline: str,
        modification_opinion: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult: ...

    async def generate_ppt_content_json(
        self,
        original_outline: str,
        ppt_style: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult: ...

    async def generate_ppt_image(
        self,
        json_content: str,
        smart_ppt_project_id: int,
    ) -> PptGenerationResult: ...

    async def generate_ppt_file(
        self,
        file_urls: list[str],
        smart_ppt_project_id: int,
    ) -> PptGenerationResult: ...


def _get_media_skill_impl() -> str:
    """读取当前媒体生成/理解供应商配置。

    当前图片生成、视频生成、参考视频拆解都共用同一个供应商能力，配置名统一为
    ``PIXELFLOW_MEDIA_SKILL``。目前只支持 ``borgrise``；后续如果图片、视频、
    拆解需要独立切供应商，再拆成更细的配置项。
    """
    return (os.environ.get(MEDIA_SKILL_ENV, DEFAULT_MEDIA_SKILL).strip() or DEFAULT_MEDIA_SKILL).lower()


def _get_borgrise_media_skill():
    """延迟导入 Borgrise skill，避免未使用时提前加载供应商相关模块。"""
    from pixelflow.skills.borgrise import BorgriseSkill

    return BorgriseSkill()


def get_video_skill() -> VideoGenerationSkill:
    """返回当前配置的视频生成 skill。

    视频生成属于媒体生成能力，读取统一的 ``PIXELFLOW_MEDIA_SKILL``。当前仅支持
    ``borgrise``，它会调用 content-app/Borgrise 的图片转视频、续写视频等接口。
    """
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


def get_image_skill() -> ImageGenerationSkill:
    """返回当前配置的图片生成 skill。"""
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


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
    """返回当前配置的参考视频拆解 skill，也就是 INTAKE 阶段替换点。

    参考视频拆解也属于媒体理解/生成供应商能力，和视频生成共用
    ``PIXELFLOW_MEDIA_SKILL``。当前仅支持 ``borgrise``。
    """
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


def get_media_link_extraction_skill() -> MediaLinkExtractionSkill:
    """返回当前配置的媒体链接识别 skill。"""
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


def get_video_flaw_analysis_skill() -> VideoFlawAnalysisSkill:
    """返回当前配置的视频穿帮分析 skill。"""
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


def get_video_quality_review_skill() -> VideoQualityReviewSkill:
    """返回当前配置的视频综合质检 skill。"""

    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")


def get_ppt_skill() -> PptGenerationSkill:
    """返回当前配置的智能 PPT 生成 skill。"""
    impl = _get_media_skill_impl()
    if impl == "borgrise":
        return _get_borgrise_media_skill()
    raise ValueError(f"Unknown media skill implementation: {impl!r}")
