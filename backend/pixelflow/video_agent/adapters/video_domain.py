"""把既有创意与参考视频 Skill 收敛为 VideoAgent 稳定 DTO。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, runtime_checkable

from pydantic import Field, JsonValue

from pixelflow.creative.brief_generate import brief_generate
from pixelflow.creative.models import Brief
from pixelflow.creative.plan_markdown import build_plan_markdown
from pixelflow.skills import get_video_decompose_skill
from pixelflow.skills.base import StoryboardResult, VideoDecomposeSkill
from pixelflow.video_agent.contracts.plan import VideoAgentContract

logger = logging.getLogger(__name__)


class VideoDomainAdapterError(RuntimeError):
    """表示防腐层已隐藏供应商细节的可公开业务失败。"""


class ReferenceVideoAnalysis(VideoAgentContract):
    """参考视频拆解后允许进入工作区的安全快照。"""

    job_id: str = Field(min_length=1, max_length=64)
    storyboard: tuple[dict[str, JsonValue], ...]


@runtime_checkable
class VideoDomainAdapter(Protocol):
    """隔离 VideoAgent 与旧流程编排及供应商字段的领域 Client。"""

    async def brainstorm_script(
        self,
        *,
        product_info: Mapping[str, JsonValue],
        video_params: Mapping[str, JsonValue],
        creative_direction: str,
        reference_analysis: Mapping[str, JsonValue] | None = None,
    ) -> str: ...

    async def analyze_reference_video(self, video_url: str) -> ReferenceVideoAnalysis: ...


BriefBuilder = Callable[..., Awaitable[Brief]]


class PixelFlowVideoDomainAdapter:
    """复用稳定领域 Service/Skill，不导入旧 LangGraph 节点或路由处理器。"""

    def __init__(
        self,
        *,
        brief_builder: BriefBuilder = brief_generate,
        decompose_skill_factory: Callable[[], VideoDecomposeSkill] = get_video_decompose_skill,
    ) -> None:
        self._brief_builder = brief_builder
        self._decompose_skill_factory = decompose_skill_factory

    async def brainstorm_script(
        self,
        *,
        product_info: Mapping[str, JsonValue],
        video_params: Mapping[str, JsonValue],
        creative_direction: str,
        reference_analysis: Mapping[str, JsonValue] | None = None,
    ) -> str:
        normalized_params = _brief_video_params(video_params)
        try:
            brief = await self._brief_builder(
                product_info=dict(product_info),
                video_params=normalized_params,
                creative_direction=creative_direction,
                reference_analysis=(
                    None if reference_analysis is None else dict(reference_analysis)
                ),
                creative_mode="reference" if reference_analysis else "original",
            )
            return _render_brief_markdown(brief)
        except Exception as exc:  # noqa: BLE001
            # 用途：模型不可用时复用确定性 Plan Service 生成可编辑草稿；影响：只降级本次草稿，不暴露异常内容。
            logger.warning(
                "VideoAgent 创意脚本模型调用失败，改用确定性草稿；error_type=%s",
                type(exc).__name__,
            )
            return _fallback_script_markdown(
                product_info=product_info,
                video_params=video_params,
                creative_direction=creative_direction,
            )

    async def analyze_reference_video(self, video_url: str) -> ReferenceVideoAnalysis:
        skill = self._decompose_skill_factory()
        result = await skill.decompose_video_to_storyboard(video_url)
        return _normalize_reference_result(video_url, result)


def _brief_video_params(video_params: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    duration = video_params.get("duration_sec", video_params.get("video_duration_sec", 30))
    ratio = video_params.get("ratio", video_params.get("video_ratio", "9:16"))
    size = video_params.get("size", video_params.get("video_size", "1080x1920"))
    platform = video_params.get("platform", "douyin")
    return {
        "duration_sec": int(duration) if isinstance(duration, (int, float, str)) else 30,
        "ratio": str(ratio or "9:16"),
        "size": str(size or "1080x1920"),
        "platform": str(platform or "douyin"),
    }


def _render_brief_markdown(brief: Brief) -> str:
    scene_lines: list[str] = []
    for index, shot in enumerate(brief.shots, start=1):
        scene_lines.append(
            f"{index}. [{shot.time_range}] {shot.visual_description}\n"
            f"   - 景别/运镜：{shot.shot_type} / {shot.camera_movement}\n"
            f"   - 旁白：{shot.narration_text or '无'}\n"
            f"   - 屏幕文案：{shot.onscreen_text or '无'}"
        )
    return (
        f"# 视频创意脚本草稿\n\n"
        f"- 平台：{brief.platform}\n"
        f"- 时长：{brief.duration_sec} 秒\n"
        f"- 画幅：{brief.ratio}\n\n"
        f"## 全局视觉\n\n"
        f"{brief.global_visual.overall_style}；{brief.global_visual.environment}；"
        f"{brief.global_visual.lighting}\n\n"
        f"## 镜头脚本\n\n" + "\n\n".join(scene_lines)
    ).strip()


def _fallback_script_markdown(
    *,
    product_info: Mapping[str, JsonValue],
    video_params: Mapping[str, JsonValue],
    creative_direction: str,
) -> str:
    form_values = dict(video_params)
    form_values.setdefault("product_info", _product_name(product_info))
    form_values.setdefault("product_category", str(product_info.get("category") or "商品"))
    form_values.setdefault("target_audience", str(product_info.get("target_audience") or "目标用户"))
    form_values.setdefault("conversion_goal", "引导购买")
    if "duration_sec" in form_values:
        form_values.setdefault("video_duration_sec", form_values["duration_sec"])
    if "ratio" in form_values:
        form_values.setdefault("video_ratio", form_values["ratio"])
    result = build_plan_markdown(
        "video",
        form_values,
        {
            "title": creative_direction or "推荐创意方向",
            "description": creative_direction or "围绕商品卖点组织短视频脚本。",
        },
        intake_context={"product_subject": _product_name(product_info)},
    )
    return result.plan_markdown


def _product_name(product_info: Mapping[str, JsonValue]) -> str:
    return str(
        product_info.get("product_name")
        or product_info.get("name")
        or product_info.get("product_subject")
        or "商品"
    )


def _normalize_reference_result(
    video_url: str,
    result: StoryboardResult,
) -> ReferenceVideoAnalysis:
    if not result.ok or not result.shots:
        raise VideoDomainAdapterError("参考视频解析失败，请稍后重试")
    storyboard = tuple(_safe_shot(shot) for shot in result.shots if isinstance(shot, dict))
    if not storyboard:
        raise VideoDomainAdapterError("参考视频未解析出可用镜头")
    digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:24]
    return ReferenceVideoAnalysis(
        job_id=f"reference_job_{digest}",
        storyboard=storyboard,
    )


_SAFE_SHOT_FIELDS = (
    "description",
    "visual_description",
    "duration",
    "duration_sec",
    "shot_type",
    "camera_movement",
    "narration",
    "narration_text",
    "onscreen_text",
    "scene_type",
    "start_time",
    "end_time",
)


def _safe_shot(shot: Mapping[str, object]) -> dict[str, JsonValue]:
    safe: dict[str, JsonValue] = {}
    for key in _SAFE_SHOT_FIELDS:
        value = shot.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    description = str(
        safe.get("description") or safe.get("visual_description") or "参考镜头"
    ).strip()
    safe["description"] = description[:2_000]
    return safe
