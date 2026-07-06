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
from pixelflow.qc.revision_scope import resolve_revision_scope
from pixelflow.qc.scene_semantic import evaluate_scene_semantic_contracts

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
    target_scene_ids: list[str] = Field(default_factory=list)
    excluded_scene_ids: list[str] = Field(default_factory=list)
    revision_prompt: str = ""
    check_results: list[QCItem] = Field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class _VideoQualitySkill(Protocol):
    async def review_video_quality(self, **kwargs: Any) -> Any: ...


class _VideoDecomposeSkill(Protocol):
    async def decompose_video_to_storyboard(self, video_url: str) -> Any: ...


_PRODUCT_TERMS = (
    "电动牙刷",
    "牙刷",
    "保温杯",
    "蓝牙耳机",
    "耳机",
    "手机",
    "鼠标",
    "键盘",
    "电脑",
    "平板",
    "手表",
    "音箱",
    "背包",
    "书包",
    "口红",
    "面霜",
    "水杯",
    "杯子",
)
_PRODUCT_IDENTITY_KEYS = {
    "product_info",
    "product_name",
    "product_subject",
    "product",
}


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
        status = "fail" if any(issue.severity in {"blocker", "major"} for issue in grouped) else "warn"
        results.append(
            QCItem(
                item=_CATEGORY_LABELS.get(category, category),
                status=status,
                message="；".join(issue.message for issue in grouped[:3]),
            )
        )
    return results


def _deterministic_qc(request: VideoQCRequest) -> tuple[list[QCItem], list[VideoQCIssue]]:
    scene_videos = [
        scene
        for scene in request.scene_videos
        if isinstance(scene, dict) and (scene.get("video_url") or scene.get("url"))
    ]
    expected_scenes = [scene for scene in request.scene_packages if isinstance(scene, dict)] or scene_videos
    generated_assets = [{"ok": True, "url": scene.get("video_url") or scene.get("url") or "", "segment_index": index} for index, scene in enumerate(expected_scenes)]
    timeline = {
        "clips": [{} for _ in scene_videos],
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
    actual_scene_ids = {str(scene.get("scene_id")) for scene in scene_videos if scene.get("scene_id")}
    actual_scene_indices = {
        int(scene.get("scene_index"))
        for scene in scene_videos
        if isinstance(scene.get("scene_index"), int | float)
    }
    missing_scenes: list[dict[str, Any]] = []
    if request.scene_packages:
        for scene in expected_scenes:
            scene_id = str(scene.get("scene_id") or "")
            scene_index = scene.get("scene_index")
            has_video = (scene_id and scene_id in actual_scene_ids) or (
                isinstance(scene_index, int | float) and int(scene_index) in actual_scene_indices
            )
            if not has_video:
                missing_scenes.append(scene)
    if missing_scenes and all(item.item != "片段完整性" or item.status != "fail" for item in result.check_results):
        result.check_results[0] = QCItem(
            item="片段完整性",
            status="fail",
            message=f"缺少 {len(missing_scenes)} 个预期分镜视频片段",
        )
    for item in result.check_results:
        if item.status != "fail":
            continue
        if item.item == "片段完整性" and missing_scenes:
            for scene in missing_scenes:
                scene_index_value = scene.get("scene_index")
                scene_index = int(scene_index_value) if isinstance(scene_index_value, int | float) else None
                label = f"第{scene_index}个分镜" if scene_index is not None else str(scene.get("scene_id") or "缺失分镜")
                issues.append(
                    VideoQCIssue(
                        code="missing_scene_video",
                        category="storyboard_coverage",
                        severity="blocker",
                        scene_id=str(scene.get("scene_id")) if scene.get("scene_id") else None,
                        scene_index=scene_index,
                        message=f"{label}缺少生成成功的视频片段",
                        expected="每个分镜都应该有对应生成视频",
                        observed="未找到该分镜的视频 URL",
                        suggestion=f"请重新生成{label}后再次质检",
                    )
                )
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


def _affected_scene_ids(result: Any, issues: list[VideoQCIssue]) -> list[str]:
    issue_scene_ids: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if not issue.scene_id or issue.scene_id in seen:
            continue
        seen.add(issue.scene_id)
        issue_scene_ids.append(issue.scene_id)
    if issue_scene_ids:
        return issue_scene_ids
    return [str(scene_id) for scene_id in getattr(result, "affected_scene_ids", []) if scene_id]


def _filter_issues_by_scene_ids(issues: list[VideoQCIssue], scene_ids: list[str]) -> list[VideoQCIssue]:
    if not scene_ids:
        return issues
    allowed = set(scene_ids)
    unscoped = [issue for issue in issues if not issue.scene_id]
    scoped = [issue for issue in issues if issue.scene_id in allowed]
    return unscoped + scoped


def _exclude_issues_by_scene_ids(issues: list[VideoQCIssue], scene_ids: list[str]) -> list[VideoQCIssue]:
    if not scene_ids:
        return issues
    excluded = set(scene_ids)
    return [issue for issue in issues if not issue.scene_id or issue.scene_id not in excluded]


def _dedupe_issues(issues: list[VideoQCIssue]) -> list[VideoQCIssue]:
    deduped: list[VideoQCIssue] = []
    seen: set[tuple[str, str, str | None, int | None, str]] = set()
    for issue in issues:
        key = (issue.code, issue.category, issue.scene_id, issue.scene_index, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _revision_prompt_for_scene_scope(request: VideoQCRequest, scene_ids: list[str]) -> str:
    if not scene_ids:
        return ""
    labels: list[str] = []
    seen: set[str] = set()
    for scene_id in scene_ids:
        for scene in [*request.scene_packages, *request.scene_videos]:
            if not isinstance(scene, dict) or str(scene.get("scene_id") or "") != scene_id:
                continue
            scene_index = scene.get("scene_index")
            label = f"第{int(scene_index)}个分镜" if isinstance(scene_index, int | float) else scene_id
            if label not in seen:
                seen.add(label)
                labels.append(label)
            break
    target = "、".join(labels) or "指定分镜"
    return f"请只重生成{target}，恢复为原方案要求的产品一致性画面；其他分镜复用原视频，不要重新生成。"


def _scene_by_id(scenes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(scene.get("scene_id")): scene
        for scene in scenes
        if isinstance(scene, dict) and scene.get("scene_id")
    }


def _scene_contract_text(scene: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in ("title", "storyline", "prompt", "narration", "onscreen_text"):
        value = scene.get(key)
        if value:
            pieces.append(str(value))
    shot_description = scene.get("shot_description")
    if isinstance(shot_description, dict):
        pieces.append(str(shot_description.get("text") or shot_description.get("description") or ""))
    elif shot_description:
        pieces.append(str(shot_description))
    return "\n".join(piece for piece in pieces if piece).strip()


def _collect_text(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, int | float | bool) or value is None:
        return []
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            pieces.extend(_collect_text(item, depth=depth + 1))
        return pieces
    if isinstance(value, dict):
        pieces: list[str] = []
        priority_keys = (
            "product_info",
            "product_name",
            "product_subject",
            "video_goal",
            "image_goal",
            "creative_goal",
            "target",
            "title",
            "markdown",
            "plan_markdown",
            "summary",
            "description",
        )
        for key in priority_keys:
            if key in value:
                pieces.extend(_collect_text(value.get(key), depth=depth + 1))
        for key, item in value.items():
            if key in priority_keys:
                continue
            pieces.extend(_collect_text(item, depth=depth + 1))
        return pieces
    return []


def _collect_values_for_keys(value: Any, keys: set[str], *, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, dict):
        pieces: list[str] = []
        for key, item in value.items():
            if key in keys:
                pieces.extend(_collect_text(item, depth=depth + 1))
            elif isinstance(item, dict | list):
                pieces.extend(_collect_values_for_keys(item, keys, depth=depth + 1))
        return pieces
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            pieces.extend(_collect_values_for_keys(item, keys, depth=depth + 1))
        return pieces
    return []


def _global_contract_text(request: VideoQCRequest) -> str:
    explicit_product_text = "\n".join(_collect_values_for_keys(request.brief, _PRODUCT_IDENTITY_KEYS)).strip()
    explicit_terms = _dominant_product_terms(_product_terms(explicit_product_text))
    if explicit_terms:
        return "\n".join([f"原始产品主体：{'、'.join(sorted(explicit_terms))}", explicit_product_text]).strip()

    pieces: list[str] = []
    pieces.extend(_collect_text(request.brief))
    for scene in request.scene_packages:
        if isinstance(scene, dict):
            pieces.extend(_collect_text(scene.get("global_assets")))
    text = "\n".join(piece for piece in pieces if piece).strip()
    terms = _dominant_product_terms(_product_terms(text))
    if not terms:
        return text
    return "\n".join([f"原始产品主体：{'、'.join(sorted(terms))}", text]).strip()


def _storyboard_text(shots: list[Any]) -> str:
    pieces: list[str] = []
    for shot in shots:
        if isinstance(shot, str):
            pieces.append(shot)
            continue
        if not isinstance(shot, dict):
            continue
        for key in ("visual_description", "description", "visualContent", "content", "text", "prompt"):
            value = shot.get(key)
            if value:
                pieces.append(str(value))
    return "\n".join(pieces).strip()


def _product_terms(text: str) -> set[str]:
    return {term for term in _PRODUCT_TERMS if term in text}


def _dominant_product_terms(terms: set[str]) -> set[str]:
    # Longer terms carry the actual product identity, e.g. 蓝牙耳机 over 耳机.
    return {term for term in terms if not any(term != other and term in other for other in terms)}


def _semantic_product_mismatch_issue(
    *,
    scene: dict[str, Any],
    expected_text: str,
    observed_text: str,
) -> VideoQCIssue | None:
    expected_terms = _dominant_product_terms(_product_terms(expected_text))
    observed_terms = _dominant_product_terms(_product_terms(observed_text))
    if not expected_terms or not observed_terms:
        return None
    if expected_terms & observed_terms:
        return None
    scene_id = str(scene.get("scene_id") or "")
    scene_index_value = scene.get("scene_index")
    scene_index = int(scene_index_value) if isinstance(scene_index_value, int | float) else None
    label = f"第{scene_index}个分镜" if scene_index is not None else scene_id
    expected = "、".join(sorted(expected_terms))
    observed = "、".join(sorted(observed_terms))
    return VideoQCIssue(
        code="auto_scene_product_mismatch",
        category="product_consistency",
        severity="major",
        scene_id=scene_id or None,
        scene_index=scene_index,
        message=f"{label}实际画面主体与分镜合同不一致：期望展示{expected}，但视频理解结果显示为{observed}",
        expected=expected_text,
        observed=observed_text,
        suggestion=f"请只重生成{label}，恢复为原方案要求的{expected}卖点画面",
    )


async def _auto_scene_storyboard_issues(
    request: VideoQCRequest,
    decompose_skill: _VideoDecomposeSkill,
) -> list[VideoQCIssue]:
    if not any(check in request.checks for check in ("product_consistency", "plan_consistency", "storyboard_coverage")):
        return []
    package_by_id = _scene_by_id(request.scene_packages)
    contract_package_by_id = _scene_by_id(request.original_scene_packages or request.scene_packages)
    global_contract_text = _global_contract_text(request)
    has_global_product_contract = bool(_dominant_product_terms(_product_terms(global_contract_text)))
    semantic_items: list[dict[str, Any]] = []
    fallback_issues: list[VideoQCIssue] = []
    for scene_video in request.scene_videos:
        if not isinstance(scene_video, dict):
            continue
        scene_id = str(scene_video.get("scene_id") or "")
        video_url = str(scene_video.get("video_url") or scene_video.get("url") or "")
        scene_package = package_by_id.get(scene_id)
        contract_package = contract_package_by_id.get(scene_id) or scene_package
        if not scene_id or not video_url or not scene_package or not contract_package:
            continue
        scene_contract_text = _scene_contract_text(contract_package)
        expected_text = global_contract_text if has_global_product_contract else scene_contract_text
        if not expected_text:
            continue
        try:
            storyboard = await decompose_skill.decompose_video_to_storyboard(video_url)
        except Exception:
            continue
        if not getattr(storyboard, "ok", False):
            continue
        observed_text = _storyboard_text(getattr(storyboard, "shots", []) or [])
        if not observed_text:
            continue
        scene_index_value = scene_package.get("scene_index")
        scene_index = int(scene_index_value) if isinstance(scene_index_value, int | float) else None
        semantic_items.append(
            {
                "scene_id": scene_id,
                "scene_index": scene_index,
                "scene_contract_text": scene_contract_text,
                "observed_text": observed_text,
            }
        )
        fallback_issue = _semantic_product_mismatch_issue(scene=contract_package, expected_text=expected_text, observed_text=observed_text)
        if fallback_issue is not None:
            fallback_issues.append(fallback_issue)

    semantic_results = await evaluate_scene_semantic_contracts(
        global_contract_text=global_contract_text,
        items=semantic_items,
    )
    issues: list[VideoQCIssue] = []
    package_by_id = _scene_by_id(request.scene_packages)
    for result in semantic_results:
        if result.get("passed", True):
            continue
        scene_id = str(result.get("scene_id") or "")
        scene = package_by_id.get(scene_id, {})
        scene_index_value = scene.get("scene_index")
        scene_index = int(scene_index_value) if isinstance(scene_index_value, int | float) else None
        issues.append(
            VideoQCIssue(
                code="auto_scene_semantic_mismatch",
                category=result.get("category", "product_consistency"),  # type: ignore[arg-type]
                severity=result.get("severity", "major"),  # type: ignore[arg-type]
                scene_id=scene_id or None,
                scene_index=scene_index,
                message=str(result.get("message") or "分镜实际视频内容与原始方案不一致"),
                expected=str(result.get("expected") or global_contract_text),
                observed=str(result.get("observed") or ""),
                suggestion=str(result.get("suggestion") or ""),
            )
        )
    if issues:
        return issues
    return fallback_issues


async def review_video_quality(
    request: VideoQCRequest,
    *,
    skill: _VideoQualitySkill | None = None,
    decompose_skill: _VideoDecomposeSkill | None = None,
) -> VideoQCResponse:
    """调用供应商综合质检能力并归一化报告。"""
    if skill is None:
        from pixelflow.skills import get_video_quality_review_skill

        skill = get_video_quality_review_skill()
    if decompose_skill is None:
        from pixelflow.skills import get_video_decompose_skill

        decompose_skill = get_video_decompose_skill()

    deterministic_checks, deterministic_issues = _deterministic_qc(request)
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

    supplier_issues = [_normalize_issue(issue) for issue in getattr(result, "issues", []) if isinstance(issue, dict)]
    auto_storyboard_issues = await _auto_scene_storyboard_issues(request, decompose_skill)
    semantic_issues = _dedupe_issues(supplier_issues + auto_storyboard_issues)
    scope = await resolve_revision_scope(
        feedback=request.user_feedback,
        scenes=[*request.scene_packages, *request.scene_videos],
    )
    scoped_scene_ids = scope.target_scene_ids
    excluded_scene_ids = scope.excluded_scene_ids
    if scoped_scene_ids:
        semantic_issues = _filter_issues_by_scene_ids(semantic_issues, scoped_scene_ids)
    if excluded_scene_ids:
        semantic_issues = _exclude_issues_by_scene_ids(semantic_issues, excluded_scene_ids)
    issues = deterministic_issues + semantic_issues
    passed = not any(issue.severity in {"blocker", "major"} for issue in issues)
    summary = str(getattr(result, "summary_markdown", "") or getattr(result, "flaw_analysis_markdown", "") or "")
    check_results = _merge_check_results(deterministic_checks, _check_results(semantic_issues))
    revision_prompt = str(getattr(result, "revision_prompt", "") or "")
    if scoped_scene_ids:
        revision_prompt = _revision_prompt_for_scene_scope(request, scoped_scene_ids)
    if auto_storyboard_issues and not revision_prompt:
        revision_prompt = "；".join(issue.suggestion for issue in auto_storyboard_issues if issue.suggestion)
    return VideoQCResponse(
        ok=True,
        passed=passed,
        score=round(min(_status_score(check_results), _score(issues)), 2),
        endpoint=endpoint or "/api/creative/analyze_video_flaws",
        task_id=getattr(result, "task_id", None),
        summary_markdown=summary,
        flaw_analysis_markdown=summary,
        issues=issues,
        affected_scene_ids=scoped_scene_ids or _affected_scene_ids(result, issues),
        target_scene_ids=scoped_scene_ids,
        excluded_scene_ids=excluded_scene_ids,
        revision_prompt=revision_prompt,
        check_results=check_results,
        raw=raw,
    )
