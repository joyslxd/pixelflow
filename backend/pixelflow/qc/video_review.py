"""视频 QC 质检服务。

该模块只负责把 PixelFlow 工作台的视频上下文归一成 QC 请求，并把 content-app
返回的 QAAgent 质检结果转换成前端稳定 DTO。历史本地检查、
ffmpeg/ffprobe 检查和二次视频拆解逻辑不再参与这个闭环。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from pixelflow.qc.models import QCItem

CONTENT_APP_QC_ENDPOINT = "/api/creative/video_quality_review"

DEFAULT_CHECKS = [
    "video_artifact",
    "product_visibility",
    "prompt_alignment",
    "subtitle_accuracy",
    "brief_alignment",
    "playback_stability",
    "constraint_compliance",
]

Category = Literal[
    "video_artifact",
    "product_visibility",
    "prompt_alignment",
    "subtitle_accuracy",
    "brief_alignment",
    "playback_stability",
    "constraint_compliance",
    "technical",
]
Severity = Literal["blocker", "major", "minor", "info"]

_CATEGORY_LABELS: dict[str, str] = {
    "video_artifact": "视频画面缺陷",
    "product_visibility": "商品清晰与露出",
    "prompt_alignment": "Prompt 跑偏",
    "subtitle_accuracy": "字幕正确性",
    "brief_alignment": "镜头 Brief 一致性",
    "playback_stability": "播放稳定性",
    "constraint_compliance": "约束合规",
    "technical": "技术检查",
}
_VALID_CATEGORIES = set(_CATEGORY_LABELS)
_VALID_SEVERITIES = {"blocker", "major", "minor", "info"}
_SEVERITY_ALIASES = {
    "high": "major",
    "medium": "minor",
    "low": "info",
    "严重": "major",
    "中等": "minor",
    "轻微": "info",
}


class VideoQCRequest(BaseModel):
    merged_video_url: str = ""
    scene_videos: list[dict[str, Any]] = Field(default_factory=list)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    original_scene_packages: list[dict[str, Any]] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    platform: str = ""
    ratio: str = "9:16"
    size: str = ""
    expected_duration_sec: float | None = None
    user_feedback: str = ""
    checks: list[str] = Field(default_factory=lambda: list(DEFAULT_CHECKS))


class VideoQCIssue(BaseModel):
    code: str
    category: Category = "video_artifact"
    severity: Severity = "major"
    scene_id: str | None = None
    scene_index: int | None = None
    message: str
    expected: str = ""
    observed: str = ""
    suggestion: str = ""


class VideoQCResponse(BaseModel):
    ok: bool
    passed: bool
    score: float
    endpoint: str = CONTENT_APP_QC_ENDPOINT
    task_id: str | None = None
    summary_markdown: str = ""
    quality_report_markdown: str = ""
    issues: list[VideoQCIssue] = Field(default_factory=list)
    affected_scene_ids: list[str] = Field(default_factory=list)
    target_scene_ids: list[str] = Field(default_factory=list)
    excluded_scene_ids: list[str] = Field(default_factory=list)
    revision_prompt: str = ""
    check_results: list[QCItem] = Field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class _VideoQualitySkill(Protocol):
    async def review_video_quality(self, **kwargs: Any) -> Any: ...


def brief_to_scene_packages(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """把固定流水线 Brief 的 shots 转成工作台 scene_packages 形态。"""
    packages: list[dict[str, Any]] = []
    for index, shot in enumerate(brief.get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        shot_id = str(shot.get("shot_id") or f"shot_{index:03d}")
        packages.append(
            {
                "scene_id": shot_id,
                "scene_index": index,
                "storyline": str(shot.get("visual_description") or ""),
                "prompt": str(shot.get("generation_prompt") or shot.get("visual_description") or ""),
                "narration": str(shot.get("narration_text") or ""),
                "onscreen_text": str(shot.get("onscreen_text") or ""),
                "shot_id": shot_id,
            }
        )
    return packages


def generated_assets_to_scene_videos(generated_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把生成成功的 segment 资产转成 QC 所需的 scene_videos。"""
    videos: list[dict[str, Any]] = []
    for asset in generated_assets:
        if not isinstance(asset, dict) or not (asset.get("ok") and asset.get("url")):
            continue
        segment_index = int(asset.get("segment_index") or 0)
        videos.append(
            {
                "scene_id": f"segment-{segment_index}",
                "scene_index": segment_index + 1,
                "video_url": str(asset["url"]),
                "segment_index": segment_index,
                "shot_indices": list(asset.get("shot_indices") or []),
            }
        )
    return videos


def build_video_qc_request_from_task_state(state: dict[str, Any]) -> VideoQCRequest:
    """从 TaskState-like dict 构建 QC 请求。"""
    brief = state.get("brief") or {}
    return VideoQCRequest(
        merged_video_url=str(state.get("final_video_url") or ""),
        scene_videos=generated_assets_to_scene_videos(state.get("generated_assets") or []),
        scene_packages=brief_to_scene_packages(brief),
        brief=brief,
        platform=str(brief.get("platform") or ""),
        ratio=str(brief.get("ratio") or "9:16"),
        size=str(brief.get("size") or ""),
        expected_duration_sec=brief.get("duration_sec"),
    )


def _normalize_category(value: Any) -> Category:
    category = str(value or "video_artifact")
    if category not in _VALID_CATEGORIES:
        category = "video_artifact"
    return category  # type: ignore[return-value]


def _normalize_severity(value: Any) -> Severity:
    severity = str(value or "major")
    severity = _SEVERITY_ALIASES.get(severity, severity)
    if severity not in _VALID_SEVERITIES:
        severity = "major"
    return severity  # type: ignore[return-value]


def _normalize_issue(raw: dict[str, Any]) -> VideoQCIssue:
    scene_index = raw.get("scene_index")
    message = str(
        raw.get("message")
        or raw.get("problem")
        or raw.get("description")
        or raw.get("current")
        or raw.get("observed")
        or "检测到视频 QC 质检问题"
    )
    return VideoQCIssue(
        code=str(raw.get("code") or raw.get("category") or "video_qc_issue"),
        category=_normalize_category(raw.get("category")),
        severity=_normalize_severity(raw.get("severity")),
        scene_id=str(raw.get("scene_id")) if raw.get("scene_id") else None,
        scene_index=int(scene_index) if isinstance(scene_index, int | float) else None,
        message=message,
        expected=str(raw.get("expected") or ""),
        observed=str(raw.get("observed") or raw.get("current") or ""),
        suggestion=str(raw.get("suggestion") or raw.get("revision_suggestion") or raw.get("fix") or ""),
    )


def _score(issues: list[VideoQCIssue], supplier_score: Any = None) -> float:
    if isinstance(supplier_score, int | float):
        return round(max(0.0, min(1.0, float(supplier_score))), 2)
    if not issues:
        return 1.0
    penalties = {"blocker": 0.45, "major": 0.22, "minor": 0.08, "info": 0.02}
    return round(max(0.0, 1.0 - sum(penalties[issue.severity] for issue in issues)), 2)


def _check_results(raw: dict[str, Any], issues: list[VideoQCIssue]) -> list[QCItem]:
    raw_results = raw.get("check_results") or raw.get("checkResults")
    if isinstance(raw_results, list):
        results: list[QCItem] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "warn")
            if status not in {"pass", "warn", "fail"}:
                status = "warn"
            results.append(
                QCItem(
                    item=str(item.get("item") or item.get("name") or "视频 QC 质检"),
                    status=status,  # type: ignore[arg-type]
                    message=str(item.get("message") or item.get("summary") or ""),
                )
            )
        if results:
            return results
    if not issues:
        return [QCItem(item="视频 QC 质检", status="pass", message="未发现明显质量问题")]

    grouped: dict[str, list[VideoQCIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.category, []).append(issue)
    return [
        QCItem(
            item=_CATEGORY_LABELS.get(category, category),
            status="fail" if any(issue.severity in {"blocker", "major"} for issue in items) else "warn",
            message="；".join(issue.message for issue in items[:3]),
        )
        for category, items in grouped.items()
    ]


def _affected_scene_ids(result: Any, issues: list[VideoQCIssue]) -> list[str]:
    raw_ids = [str(scene_id) for scene_id in getattr(result, "affected_scene_ids", []) if scene_id]
    if raw_ids:
        return raw_ids
    ids: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if not issue.scene_id or issue.scene_id in seen:
            continue
        seen.add(issue.scene_id)
        ids.append(issue.scene_id)
    return ids


async def review_video_quality(
    request: VideoQCRequest,
    *,
    skill: _VideoQualitySkill | None = None,
) -> VideoQCResponse:
    """调用 content-app QAAgent 视频 QC 质检能力并归一化报告。"""
    if skill is None:
        from pixelflow.skills import get_video_quality_review_skill

        skill = get_video_quality_review_skill()

    result = await skill.review_video_quality(
        merged_video_url=request.merged_video_url,
        scene_videos=request.scene_videos,
        scene_packages=request.scene_packages,
        brief=request.brief,
        materials=request.materials,
        user_feedback=request.user_feedback,
        checks=request.checks,
        platform=request.platform,
        ratio=request.ratio,
        size=request.size,
    )
    raw = getattr(result, "raw", {}) or {}
    endpoint = raw.get("endpoint") if isinstance(raw, dict) else ""
    if not getattr(result, "ok", False):
        error = getattr(result, "error", None) or "视频 QC 质检能力调用失败"
        return VideoQCResponse(
            ok=False,
            passed=False,
            score=0.0,
            endpoint=endpoint or CONTENT_APP_QC_ENDPOINT,
            task_id=getattr(result, "task_id", None),
            summary_markdown=error,
            quality_report_markdown=error,
            check_results=[QCItem(item="视频 QC 质检", status="fail", message=error)],
            error=error,
            raw=raw,
        )

    issues = [_normalize_issue(issue) for issue in getattr(result, "issues", []) if isinstance(issue, dict)]
    supplier_score = raw.get("score") if isinstance(raw, dict) else None
    score = _score(issues, supplier_score)
    passed_value = raw.get("passed") if isinstance(raw, dict) else None
    passed = bool(passed_value) if isinstance(passed_value, bool) else not any(issue.severity in {"blocker", "major"} for issue in issues)
    summary = str(getattr(result, "summary_markdown", "") or getattr(result, "quality_report_markdown", "") or "")
    quality_report = str(getattr(result, "quality_report_markdown", "") or summary)
    return VideoQCResponse(
        ok=True,
        passed=passed,
        score=score,
        endpoint=endpoint or CONTENT_APP_QC_ENDPOINT,
        task_id=getattr(result, "task_id", None),
        summary_markdown=summary,
        quality_report_markdown=quality_report,
        issues=issues,
        affected_scene_ids=_affected_scene_ids(result, issues),
        revision_prompt=str(getattr(result, "revision_prompt", "") or ""),
        check_results=_check_results(raw, issues),
        raw=raw,
    )
