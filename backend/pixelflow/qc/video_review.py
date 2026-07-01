"""综合视频质检服务。

这个模块把工作台分镜链路和固定流水线的输入归一成同一份质检合同，再调用
供应商的视频理解能力。这里不直接关心 Borgrise/content-app 的 HTTP 细节，只负责
结果归一化、严重程度判定和对旧穿帮分析字段的兼容。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from pixelflow.qc.check import qc_check
from pixelflow.qc.models import QCItem

DEFAULT_CHECKS = [
    "plan_consistency",
    "storyboard_coverage",
    "product_consistency",
    "playback_stability",
    "mobile_requirements",
]

Category = Literal[
    "plan_consistency",
    "storyboard_coverage",
    "product_consistency",
    "playback_stability",
    "mobile_requirements",
    "technical",
]
Severity = Literal["blocker", "major", "minor", "info"]

_CATEGORY_LABELS: dict[str, str] = {
    "plan_consistency": "方案一致性",
    "storyboard_coverage": "分镜覆盖",
    "product_consistency": "产品一致性/穿帮",
    "playback_stability": "播放稳定性",
    "mobile_requirements": "手机端需求",
    "technical": "技术检查",
}

_VALID_CATEGORIES = set(_CATEGORY_LABELS)
_VALID_SEVERITIES = {"blocker", "major", "minor", "info"}


class VideoQCRequest(BaseModel):
    merged_video_url: str = ""
    scene_videos: list[dict[str, Any]] = Field(default_factory=list)
    scene_packages: list[dict[str, Any]] = Field(default_factory=list)
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
    category: Category = "product_consistency"
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
    endpoint: str = "/api/creative/analyze_video_flaws"
    task_id: str | None = None
    summary_markdown: str = ""
    flaw_analysis_markdown: str = ""
    issues: list[VideoQCIssue] = Field(default_factory=list)
    affected_scene_ids: list[str] = Field(default_factory=list)
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
    """把生成成功的 segment 资产转成语义质检所需的 scene_videos。"""
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
    """从 TaskState-like dict 构建综合质检请求。"""
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


def _normalize_issue(raw: dict[str, Any]) -> VideoQCIssue:
    category = str(raw.get("category") or "product_consistency")
    if category not in _VALID_CATEGORIES:
        category = "product_consistency"
    severity = str(raw.get("severity") or ("blocker" if category == "playback_stability" and raw.get("code") == "black_screen" else "major"))
    if severity not in _VALID_SEVERITIES:
        severity = "major"
    message = str(raw.get("message") or raw.get("description") or raw.get("current") or "检测到视频质检问题")
    scene_index = raw.get("scene_index")
    return VideoQCIssue(
        code=str(raw.get("code") or category),
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        scene_id=str(raw.get("scene_id")) if raw.get("scene_id") else None,
        scene_index=int(scene_index) if isinstance(scene_index, int | float) else None,
        message=message,
        expected=str(raw.get("expected") or ""),
        observed=str(raw.get("observed") or raw.get("current") or ""),
        suggestion=str(raw.get("suggestion") or raw.get("fix") or ""),
    )


def _score(issues: list[VideoQCIssue]) -> float:
    if not issues:
        return 1.0
    penalties = {"blocker": 0.45, "major": 0.22, "minor": 0.08, "info": 0.02}
    score = 1.0 - sum(penalties[issue.severity] for issue in issues)
    return round(max(score, 0.0), 2)


def _status_score(check_results: list[QCItem]) -> float:
    if not check_results:
        return 1.0
    weights = {"pass": 1.0, "warn": 0.6, "fail": 0.0}
    return round(sum(weights[item.status] for item in check_results) / len(check_results), 2)


def _check_results(issues: list[VideoQCIssue]) -> list[QCItem]:
    if not issues:
        return [QCItem(item="综合语义质检", status="pass", message="未发现明显语义质检问题")]

    by_category: dict[str, list[VideoQCIssue]] = {}
    for issue in issues:
        by_category.setdefault(issue.category, []).append(issue)

    results: list[QCItem] = []
    for category, grouped in by_category.items():
        status = "fail" if any(issue.severity == "blocker" for issue in grouped) else "warn"
        results.append(
            QCItem(
                item=_CATEGORY_LABELS.get(category, category),
                status=status,
                message="；".join(issue.message for issue in grouped[:3]),
            )
        )
    return results


def _deterministic_qc(request: VideoQCRequest) -> tuple[list[QCItem], list[VideoQCIssue]]:
    generated_assets = [
        {"ok": True, "url": scene.get("video_url") or scene.get("url"), "segment_index": index}
        for index, scene in enumerate(request.scene_videos)
        if isinstance(scene, dict) and (scene.get("video_url") or scene.get("url"))
    ]
    timeline = {
        "clips": [{} for _ in generated_assets],
        "total_duration": request.expected_duration_sec or request.brief.get("duration_sec", 0) or 0,
    }
    brief = dict(request.brief)
    if request.ratio:
        brief["ratio"] = request.ratio
    if request.size:
        brief["size"] = request.size
    if request.expected_duration_sec is not None:
        brief["duration_sec"] = request.expected_duration_sec

    result = qc_check(brief, generated_assets, timeline, request.merged_video_url)
    issues: list[VideoQCIssue] = []
    for item in result.check_results:
        if item.status != "fail":
            continue
        category: Category = "technical"
        code = "deterministic_qc_failed"
        if item.item in {"黑屏/空帧检测", "卡顿/冻结检测"}:
            category = "playback_stability"
            code = "black_screen" if item.item == "黑屏/空帧检测" else "freeze_frame"
        elif item.item == "手机端画幅适配":
            category = "mobile_requirements"
            code = "ratio_mismatch"
        elif item.item == "画面清晰度/分辨率":
            category = "mobile_requirements"
            code = "resolution_too_low"
        elif item.item == "片段完整性":
            category = "storyboard_coverage"
            code = "missing_scene_video"
        issues.append(
            VideoQCIssue(
                code=code,
                category=category,
                severity="blocker",
                message=item.message or item.item,
                suggestion="请重新生成或调整对应视频片段后再次质检",
            )
        )
    return result.check_results, issues


def _merge_check_results(deterministic: list[QCItem], semantic: list[QCItem]) -> list[QCItem]:
    merged = list(deterministic)
    for item in semantic:
        if item.item == "综合语义质检" and item.status == "pass" and deterministic:
            continue
        merged.append(item)
    return merged


async def review_video_quality(request: VideoQCRequest, *, skill: _VideoQualitySkill | None = None) -> VideoQCResponse:
    """调用供应商综合质检能力并归一化报告。"""
    if skill is None:
        from pixelflow.skills import get_video_quality_review_skill

        skill = get_video_quality_review_skill()

    deterministic_checks, deterministic_issues = _deterministic_qc(request)
    result = await skill.review_video_quality(
        merged_video_url=request.merged_video_url,
        scene_videos=request.scene_videos,
        scene_packages=request.scene_packages,
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
        error = getattr(result, "error", None) or "视频质检能力调用失败"
        check_results = _merge_check_results(
            deterministic_checks,
            [QCItem(item="综合语义质检", status="fail", message=error)],
        )
        return VideoQCResponse(
            ok=False,
            passed=False,
            score=_status_score(check_results),
            endpoint=endpoint or "/api/creative/analyze_video_flaws",
            task_id=getattr(result, "task_id", None),
            summary_markdown=error,
            flaw_analysis_markdown=error,
            issues=deterministic_issues,
            check_results=check_results,
            error=error,
            raw=raw,
        )

    issues = deterministic_issues + [_normalize_issue(issue) for issue in getattr(result, "issues", []) if isinstance(issue, dict)]
    passed = not any(issue.severity == "blocker" for issue in issues)
    summary = str(getattr(result, "summary_markdown", "") or getattr(result, "flaw_analysis_markdown", "") or "")
    check_results = _merge_check_results(deterministic_checks, _check_results([issue for issue in issues if issue not in deterministic_issues]))
    return VideoQCResponse(
        ok=True,
        passed=passed,
        score=round(min(_status_score(check_results), _score(issues)), 2),
        endpoint=endpoint or "/api/creative/analyze_video_flaws",
        task_id=getattr(result, "task_id", None),
        summary_markdown=summary,
        flaw_analysis_markdown=summary,
        issues=issues,
        affected_scene_ids=[str(scene_id) for scene_id in getattr(result, "affected_scene_ids", []) if scene_id],
        revision_prompt=str(getattr(result, "revision_prompt", "") or ""),
        check_results=check_results,
        raw=raw,
    )
