"""Plan template filling, LLM planning, versioning, and strict contracts."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pixelflow.creative.asset_manifest import (
    empty_asset_manifest,
    fallback_asset_manifest,
    normalize_asset_manifest,
    render_asset_manifest_markdown,
)
from pixelflow.creative.contract import VideoCreationContract, build_video_creation_contract, resolve_scene_image_spec
from pixelflow.creative.duration import scene_time_ranges
from pixelflow.creative.plan_llm import (
    PLAN_LLM_MODEL_NAME,
    ModelFactory,
    author_seedance_plan_payload,
    generate_plan_payload,
    repair_plan_asset_requirements,
    repair_plan_shot_descriptions,
    revise_plan_payload,
)
from pixelflow.creative.revision_contract import contract_form_values, merge_revision_contract, validate_revision_contract
from pixelflow.creative.scene_blueprint import (
    apply_asset_requirement_repairs,
    apply_shot_description_repairs,
    asset_requirement_entity_quality_issues,
    enrich_incomplete_shot_descriptions,
    fallback_scene_blueprints,
    normalize_scene_blueprints,
    rebuild_scene_shot_descriptions,
    render_scene_blueprints_markdown,
    repair_scene_blueprints_schedule,
    salvage_scene_blueprints,
    scene_asset_reference_budget_issues,
    scene_blueprint_durations,
    shot_description_quality_issues,
    validate_asset_requirement_quality,
    validate_shot_description_quality,
)
from pixelflow.creative.seedance_plan import apply_seedance_plan_authoring, bind_seedance_plan_assets

CreationIntent = Literal["video", "image"]

TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[2] / "skills" / "public" / "borgrise-creative-assistant-v2" / "templates"
VIDEO_PLAN_TEMPLATE_PATH = TEMPLATE_DIRECTORY / "plan_video.md"
IMAGE_PLAN_TEMPLATE_PATH = TEMPLATE_DIRECTORY / "plan_image.md"
PLAN_TEMPLATE_PATH = VIDEO_PLAN_TEMPLATE_PATH

_REQUIRED_TEMPLATE_SECTIONS = {
    "video": ("## 一、选题方向", "## 三、视频规格", "## 五、镜头列表"),
    "image": ("## 一、选题方向", "## 三、图片规格", "## 五、主图方案"),
}
_TEMPLATE_SAMPLE_ENTITIES = ("苹果PRO", "林晓", "赵总监", "周洋")


@dataclass(frozen=True)
class PlanMarkdownResult:
    output_type: CreationIntent
    plan_markdown: str
    template_path: Path
    consistency_issues: list[str] = field(default_factory=list)
    review_timeout_sec: int | None = None
    plan_version: int = 1
    plan_history: list[dict[str, Any]] = field(default_factory=list)
    creation_contract: dict[str, Any] = field(default_factory=dict)
    scene_durations_sec: list[int] = field(default_factory=list)
    scene_blueprints: list[dict[str, Any]] = field(default_factory=list)
    asset_manifest: dict[str, list[dict[str, str]]] = field(default_factory=empty_asset_manifest)
    llm_used: bool = False
    model_name: str = PLAN_LLM_MODEL_NAME
    error: str | None = None
    restored_from_version: int | None = None

    def __post_init__(self) -> None:
        if not self.plan_history:
            object.__setattr__(
                self,
                "plan_history",
                [
                    _history_entry(
                        self.plan_version,
                        self.plan_markdown,
                        self.restored_from_version,
                        creation_contract=self.creation_contract,
                        scene_durations_sec=self.scene_durations_sec,
                        scene_blueprints=self.scene_blueprints,
                        asset_manifest=self.asset_manifest,
                    )
                ],
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_type": self.output_type,
            "plan_markdown": self.plan_markdown,
            "template_path": self.template_path.as_posix(),
            "consistency_issues": self.consistency_issues,
            "review_timeout_sec": self.review_timeout_sec,
            "plan_version": self.plan_version,
            "plan_history": self.plan_history,
            "creation_contract": self.creation_contract,
            "scene_durations_sec": self.scene_durations_sec,
            "scene_blueprints": self.scene_blueprints,
            "asset_manifest": self.asset_manifest,
            "llm_used": self.llm_used,
            "model_name": self.model_name,
            "error": self.error,
            "restored_from_version": self.restored_from_version,
        }

    def next_version(
        self,
        *,
        plan_markdown: str,
        plan_history: list[dict[str, Any]] | None = None,
        current_version: int | None = None,
        restored_from_version: int | None = None,
        llm_used: bool | None = None,
        error: str | None = None,
        creation_contract: dict[str, Any] | None = None,
        change_source: str | None = None,
        scene_blueprints: list[dict[str, Any]] | None = None,
        asset_manifest: dict[str, list[dict[str, str]]] | None = None,
    ) -> PlanMarkdownResult:
        history = _normalized_history(plan_history or self.plan_history)
        history_max = max((int(item["version"]) for item in history), default=0)
        version = max(1, int(current_version or self.plan_version), history_max) + 1
        next_contract = copy.deepcopy(creation_contract if creation_contract is not None else self.creation_contract)
        next_durations = copy.deepcopy(self.scene_durations_sec)
        next_blueprints = copy.deepcopy(scene_blueprints if scene_blueprints is not None else self.scene_blueprints)
        next_manifest = copy.deepcopy(asset_manifest if asset_manifest is not None else self.asset_manifest)
        if next_blueprints:
            next_durations = scene_blueprint_durations(next_blueprints)
        history.append(
            _history_entry(
                version,
                plan_markdown,
                restored_from_version,
                creation_contract=next_contract,
                scene_durations_sec=next_durations,
                change_source=change_source,
                scene_blueprints=next_blueprints,
                asset_manifest=next_manifest,
            )
        )
        return replace(
            self,
            plan_markdown=plan_markdown,
            plan_version=version,
            plan_history=history,
            restored_from_version=restored_from_version,
            llm_used=self.llm_used if llm_used is None else llm_used,
            error=error,
            creation_contract=next_contract,
            scene_durations_sec=next_durations,
            scene_blueprints=next_blueprints,
            asset_manifest=next_manifest,
        )


def build_plan_markdown(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
) -> PlanMarkdownResult:
    """Build the deterministic fallback Plan with the same production contract."""
    template_path, _ = _load_template(intent)
    profile = _merged_profile(product_creative_profile or {}, intake_context or {})
    issues = _consistency_issues(intent, form_values, selected_direction)
    if intent == "video":
        contract, blueprints, corrections = _video_contract_and_blueprints(
            form_values,
            selected_direction,
        )
        durations = scene_blueprint_durations(blueprints)
        asset_manifest = fallback_asset_manifest(blueprints)
        markdown = _fallback_video_plan(
            form_values,
            selected_direction,
            profile,
            materials or [],
            intake_context or {},
            contract,
            durations,
            blueprints,
            asset_manifest,
        )
        return PlanMarkdownResult(
            output_type=intent,
            plan_markdown=markdown,
            template_path=template_path,
            consistency_issues=[*issues, *corrections],
            creation_contract=contract.model_dump(exclude_none=True),
            scene_durations_sec=durations,
            scene_blueprints=blueprints,
            asset_manifest=asset_manifest,
        )
    markdown = _fallback_image_plan(form_values, selected_direction, profile, materials or [], intake_context or {})
    return PlanMarkdownResult(
        output_type=intent,
        plan_markdown=markdown,
        template_path=template_path,
        consistency_issues=issues,
        creation_contract=_image_creation_contract(form_values, intake_context or {}),
    )


async def build_plan_markdown_with_llm(
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
    *,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> PlanMarkdownResult:
    template_path, template_markdown = _load_template(intent)
    profile = _merged_profile(product_creative_profile or {}, intake_context or {})
    context = intake_context or {}
    issues = _consistency_issues(intent, form_values, selected_direction)
    contract: VideoCreationContract | None = None
    durations: list[int] = []
    blueprints: list[dict[str, Any]] = []
    asset_manifest = empty_asset_manifest()
    creation_contract = _image_creation_contract(form_values, context)
    if intent == "video":
        contract = build_video_creation_contract(form_values)
        creation_contract = contract.model_dump(exclude_none=True)
    try:
        payload = await generate_plan_payload(
            intent=intent,
            template_markdown=template_markdown,
            form_values=form_values,
            selected_direction=selected_direction,
            product_creative_profile=profile,
            materials=materials or [],
            intake_context=context,
            creation_contract=creation_contract,
            model_name=model_name,
            model_factory=model_factory,
        )
        payload = _redact_semantic_memory_payload(payload, profile, context)
        markdown = _validated_llm_markdown(intent, payload, form_values, selected_direction, context)
        corrections: list[str] = []
        if contract is not None:
            try:
                blueprints = normalize_scene_blueprints(
                    payload.get("scene_blueprints"),
                    total_duration_sec=contract.video_duration_sec,
                )
            except ValueError as exc:
                try:
                    blueprints = repair_scene_blueprints_schedule(
                        payload.get("scene_blueprints"),
                        total_duration_sec=contract.video_duration_sec,
                    )
                    corrections.append(f"Plan LLM 分镜时长已按创作合同重新调度：{exc}")
                except ValueError as repair_exc:
                    validation_feedback = (
                        "首次返回的 scene_blueprints 结构无效，且确定性时间表修复失败。"
                        f"原始校验错误：{exc}；时间表修复错误：{repair_exc}。"
                        "请返回完整、连续且总时长精确匹配创作合同的 scene_blueprints；"
                        "同时重新核对用户明确命名的人物、服装造型、物理场景、商品和道具，"
                        "逐项写入 asset_requirements 与 asset_manifest，禁止使用泛化占位名称。"
                    )
                    retry_payload: dict[str, Any] | None = None
                    try:
                        retry_payload = await generate_plan_payload(
                            intent=intent,
                            template_markdown=template_markdown,
                            form_values=form_values,
                            selected_direction=selected_direction,
                            product_creative_profile=profile,
                            materials=materials or [],
                            intake_context=context,
                            creation_contract=creation_contract,
                            validation_feedback=validation_feedback,
                            model_name=model_name,
                            model_factory=model_factory,
                        )
                        retry_payload = _redact_semantic_memory_payload(retry_payload, profile, context)
                        retry_markdown = _validated_llm_markdown(
                            intent,
                            retry_payload,
                            form_values,
                            selected_direction,
                            context,
                        )
                        try:
                            retry_blueprints = normalize_scene_blueprints(
                                retry_payload.get("scene_blueprints"),
                                total_duration_sec=contract.video_duration_sec,
                            )
                        except ValueError:
                            retry_blueprints = repair_scene_blueprints_schedule(
                                retry_payload.get("scene_blueprints"),
                                total_duration_sec=contract.video_duration_sec,
                            )
                        payload = retry_payload
                        markdown = retry_markdown
                        blueprints = retry_blueprints
                        corrections.append("Plan LLM 分镜蓝图已根据结构反馈重新生成")
                    except Exception as retry_exc:  # noqa: BLE001 - 仅恢复已有具体语义，不生成泛化替代内容
                        salvage_errors: list[str] = []
                        salvage_candidates = []
                        if isinstance(retry_payload, dict):
                            salvage_candidates.append(("重试结果", retry_payload.get("scene_blueprints")))
                        salvage_candidates.append(("首次结果", payload.get("scene_blueprints")))
                        for label, candidate in salvage_candidates:
                            try:
                                blueprints = salvage_scene_blueprints(
                                    candidate,
                                    total_duration_sec=contract.video_duration_sec,
                                    visual_style=_text(form_values.get("visual_style"), "真实广告风格"),
                                )
                                corrections.append(f"Plan LLM {label}的具体故事线和资产已保留，非法时间线与镜头秒段已按规则重建")
                                break
                            except ValueError as salvage_exc:
                                salvage_errors.append(f"{label}恢复失败：{salvage_exc}")
                        else:
                            raise ValueError(
                                "Plan LLM 分镜蓝图重试后仍不可恢复，已拒绝生成泛化资产 Plan："
                                f"{retry_exc}；{'；'.join(salvage_errors)}"
                            ) from retry_exc

            entity_issues = asset_requirement_entity_quality_issues(blueprints)
            if entity_issues:
                repair_payload = await repair_plan_asset_requirements(
                    scene_blueprints=blueprints,
                    quality_issues=entity_issues,
                    selected_direction=selected_direction,
                    creation_contract=creation_contract,
                    model_name=model_name,
                    model_factory=model_factory,
                )
                blueprints = apply_asset_requirement_repairs(
                    blueprints,
                    repair_payload.get("scene_blueprints"),
                    total_duration_sec=contract.video_duration_sec,
                )
                remaining_entity_issues = asset_requirement_entity_quality_issues(blueprints)
                if remaining_entity_issues:
                    raise ValueError("；".join(remaining_entity_issues))

            budget_issues = scene_asset_reference_budget_issues(blueprints)
            if budget_issues:
                payload, markdown, blueprints = await _replan_initial_asset_budget(
                    blueprints=blueprints,
                    budget_issues=budget_issues,
                    intent=intent,
                    template_markdown=template_markdown,
                    form_values=form_values,
                    selected_direction=selected_direction,
                    product_creative_profile=profile,
                    materials=materials or [],
                    intake_context=context,
                    creation_contract=creation_contract,
                    total_duration_sec=contract.video_duration_sec,
                    model_name=model_name,
                    model_factory=model_factory,
                )
                corrections.append("超出 9 张参考图预算的分镜已在 Plan 阶段重新规划并重新分配内容与资产")
            quality_issues = shot_description_quality_issues(blueprints)
            if quality_issues:
                try:
                    repair_payload = await repair_plan_shot_descriptions(
                        scene_blueprints=blueprints,
                        quality_issues=quality_issues,
                        selected_direction=selected_direction,
                        creation_contract=creation_contract,
                        visual_style=_text(form_values.get("visual_style")),
                        model_name=model_name,
                        model_factory=model_factory,
                    )
                    repaired_blueprints = apply_shot_description_repairs(
                        blueprints,
                        repair_payload.get("scene_blueprints"),
                        total_duration_sec=contract.video_duration_sec,
                    )
                    validate_shot_description_quality(repaired_blueprints)
                    blueprints = repaired_blueprints
                except Exception as exc:  # noqa: BLE001 - 一次修正仍失败时使用确定性丰富模板
                    blueprints = enrich_incomplete_shot_descriptions(
                        blueprints,
                        visual_style=_text(form_values.get("visual_style"), "真实广告风格"),
                    )
                    validate_shot_description_quality(blueprints)
                    corrections.append(f"Plan LLM 镜头描述已使用规则增强：{exc}")
            validate_asset_requirement_quality(blueprints)
            durations = scene_blueprint_durations(blueprints)
            contract, image_corrections = resolve_scene_image_spec(
                contract,
                _text(payload.get("scene_image_ratio")),
                _text(payload.get("scene_image_size")),
            )
            corrections.extend(image_corrections)
            creation_contract = contract.model_dump(exclude_none=True)
            try:
                asset_manifest = normalize_asset_manifest(payload.get("asset_manifest"), blueprints)
            except ValueError as exc:
                asset_manifest = fallback_asset_manifest(blueprints)
                corrections.append(f"Plan LLM 全局资产清单已按分镜资产需求补全：{exc}")
            authoring_plan = _with_execution_contract(
                intent,
                markdown,
                creation_contract,
                durations,
                scene_blueprints=blueprints,
                asset_manifest=asset_manifest,
            )
            blueprints, authoring_corrections = await _author_seedance_plan_blueprints(
                plan_markdown=authoring_plan,
                scene_blueprints=blueprints,
                asset_manifest=asset_manifest,
                creation_contract=creation_contract,
                form_values=form_values,
                selected_direction=selected_direction,
                intake_context=context,
                materials=materials or [],
                redaction_contexts=(profile, context),
                model_name=model_name,
                model_factory=model_factory,
            )
            corrections.extend(authoring_corrections)
            durations = scene_blueprint_durations(blueprints)
        markdown = _with_execution_contract(
            intent,
            markdown,
            creation_contract,
            durations,
            scene_blueprints=blueprints,
            asset_manifest=asset_manifest,
        )
        return PlanMarkdownResult(
            output_type=intent,
            plan_markdown=markdown,
            template_path=template_path,
            consistency_issues=[*issues, *corrections],
            creation_contract=creation_contract,
            scene_durations_sec=durations,
            scene_blueprints=blueprints,
            asset_manifest=asset_manifest,
            llm_used=True,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - Plan generation must degrade to a valid contract
        fallback = build_plan_markdown(intent, form_values, selected_direction, profile, materials, context)
        return replace(fallback, error=str(exc), model_name=model_name)


async def revise_plan_markdown_with_llm(
    *,
    intent: CreationIntent,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    current_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    revision_feedback: str,
    creation_contract: dict[str, Any] | None = None,
    current_scene_blueprints: list[dict[str, Any]] | None = None,
    current_asset_manifest: dict[str, list[dict[str, str]]] | None = None,
    product_creative_profile: dict[str, Any] | None = None,
    materials: list[dict[str, Any]] | None = None,
    intake_context: dict[str, Any] | None = None,
    change_source: str | None = None,
    model_name: str = PLAN_LLM_MODEL_NAME,
    model_factory: ModelFactory | None = None,
) -> PlanMarkdownResult:
    template_path, template_markdown = _load_template(intent)
    context = intake_context or {}
    profile = _merged_profile(product_creative_profile or {}, context)
    base = build_plan_markdown(intent, form_values, selected_direction, profile, materials, context)
    original_contract = copy.deepcopy(creation_contract or base.creation_contract)
    original_blueprints = copy.deepcopy(current_scene_blueprints or base.scene_blueprints)
    original_manifest = copy.deepcopy(current_asset_manifest or base.asset_manifest)
    original_durations = scene_blueprint_durations(original_blueprints) if original_blueprints else copy.deepcopy(base.scene_durations_sec)
    try:
        authoritative_contract = merge_revision_contract(
            intent,
            original_contract,
            revision_feedback,
        )
        authoritative_contract = validate_revision_contract(intent, authoritative_contract)
    except Exception as exc:  # noqa: BLE001 - 非法显式参数不能污染历史版本
        return _failed_revision_result(
            base=base,
            template_path=template_path,
            current_plan_markdown=current_plan_markdown,
            current_plan_version=current_plan_version,
            plan_history=plan_history,
            creation_contract=original_contract,
            scene_durations_sec=original_durations,
            scene_blueprints=original_blueprints,
            asset_manifest=original_manifest,
            model_name=model_name,
            error=exc,
        )

    validation_feedback = ""
    last_error: Exception | None = None
    budget_replan_required_assets: dict[str, set[str]] | None = None
    for attempt in range(2):
        effective_form_values = {**form_values, **contract_form_values(authoritative_contract)}
        try:
            payload = await revise_plan_payload(
                intent=intent,
                template_markdown=template_markdown,
                current_plan_markdown=current_plan_markdown,
                revision_feedback=revision_feedback,
                form_values=effective_form_values,
                selected_direction=selected_direction,
                creation_contract=authoritative_contract,
                current_scene_blueprints=original_blueprints,
                current_asset_manifest=original_manifest,
                product_creative_profile=profile,
                materials=materials or [],
                intake_context=context,
                validation_feedback=validation_feedback,
                model_name=model_name,
                model_factory=model_factory,
            )
            payload = _redact_semantic_memory_payload(payload, profile, context)
            candidate_contract = merge_revision_contract(
                intent,
                authoritative_contract,
                revision_feedback,
                payload.get("creation_contract_patch") if isinstance(payload.get("creation_contract_patch"), dict) else None,
            )
            candidate_contract = validate_revision_contract(intent, candidate_contract)
            candidate_form_values = {**form_values, **contract_form_values(candidate_contract)}
            markdown = _validated_llm_markdown(intent, payload, candidate_form_values, selected_direction, context)
            corrections: list[str] = []
            blueprints: list[dict[str, Any]] = []
            durations: list[int] = []
            asset_manifest = empty_asset_manifest()
            if intent == "video":
                contract = VideoCreationContract.model_validate(candidate_contract)
                try:
                    blueprints = normalize_scene_blueprints(
                        payload.get("scene_blueprints"),
                        total_duration_sec=contract.video_duration_sec,
                    )
                except ValueError as exc:
                    raise ValueError(f"分镜蓝图校验失败：{exc}") from exc
                quality_issues = shot_description_quality_issues(blueprints)
                if quality_issues:
                    try:
                        repair_payload = await repair_plan_shot_descriptions(
                            scene_blueprints=blueprints,
                            quality_issues=quality_issues,
                            selected_direction=selected_direction,
                            creation_contract=candidate_contract,
                            visual_style=_text(candidate_form_values.get("visual_style")),
                            model_name=model_name,
                            model_factory=model_factory,
                        )
                        blueprints = apply_shot_description_repairs(
                            blueprints,
                            repair_payload.get("scene_blueprints"),
                            total_duration_sec=contract.video_duration_sec,
                        )
                        validate_shot_description_quality(blueprints)
                    except Exception as exc:  # noqa: BLE001 - 定向修正失败时不能发布新版本
                        return _failed_revision_result(
                            base=base,
                            template_path=template_path,
                            current_plan_markdown=current_plan_markdown,
                            current_plan_version=current_plan_version,
                            plan_history=plan_history,
                            creation_contract=original_contract,
                            scene_durations_sec=original_durations,
                            scene_blueprints=original_blueprints,
                            asset_manifest=original_manifest,
                            model_name=model_name,
                            error=ValueError(f"分镜镜头描述完整度校验失败：{exc}"),
                        )
                entity_issues = asset_requirement_entity_quality_issues(blueprints)
                if entity_issues:
                    try:
                        repair_payload = await repair_plan_asset_requirements(
                            scene_blueprints=blueprints,
                            quality_issues=entity_issues,
                            selected_direction=selected_direction,
                            creation_contract=candidate_contract,
                            model_name=model_name,
                            model_factory=model_factory,
                        )
                        blueprints = apply_asset_requirement_repairs(
                            blueprints,
                            repair_payload.get("scene_blueprints"),
                            total_duration_sec=contract.video_duration_sec,
                        )
                        remaining_entity_issues = asset_requirement_entity_quality_issues(blueprints)
                        if remaining_entity_issues:
                            raise ValueError("；".join(remaining_entity_issues))
                    except Exception as exc:  # noqa: BLE001 - 定向修正失败时不能发布污染版本
                        return _failed_revision_result(
                            base=base,
                            template_path=template_path,
                            current_plan_markdown=current_plan_markdown,
                            current_plan_version=current_plan_version,
                            plan_history=plan_history,
                            creation_contract=original_contract,
                            scene_durations_sec=original_durations,
                            scene_blueprints=original_blueprints,
                            asset_manifest=original_manifest,
                            model_name=model_name,
                            error=ValueError(f"分镜资产合同校验失败：{exc}"),
                        )
                budget_issues = scene_asset_reference_budget_issues(blueprints)
                if budget_issues:
                    if budget_replan_required_assets is None:
                        budget_replan_required_assets = _asset_name_sets(blueprints)
                    raise ValueError(
                        "分镜九图预算校验失败："
                        f"{'；'.join(budget_issues)}。请重新规划整份 scene_blueprints，可拆分分镜或重新分配时长与动作，"
                        "保持总时长精确、每镜 4-15 个整数秒、故事连续；不得通过截断或删除全局资产来通过校验，"
                        f"必须保留的分类资产并集：{budget_replan_required_assets}"
                    )
                if budget_replan_required_assets is not None:
                    actual_assets = _asset_name_sets(blueprints)
                    if actual_assets != budget_replan_required_assets:
                        raise ValueError(
                            "分镜九图预算重排不得删除、增加或改名全局资产；"
                            f"重排前={budget_replan_required_assets}，重排后={actual_assets}"
                        )
                validate_asset_requirement_quality(blueprints)
                durations = scene_blueprint_durations(blueprints)
                contract, image_corrections = resolve_scene_image_spec(
                    contract,
                    _text(payload.get("scene_image_ratio")) or contract.scene_image_ratio,
                    _text(payload.get("scene_image_size")) or contract.scene_image_size,
                )
                corrections.extend(image_corrections)
                candidate_contract = contract.model_dump(exclude_none=True)
                try:
                    asset_manifest = normalize_asset_manifest(payload.get("asset_manifest"), blueprints)
                except ValueError as exc:
                    try:
                        asset_manifest = normalize_asset_manifest(original_manifest, blueprints)
                        corrections.append(f"Plan LLM 全局资产清单沿用当前版本：{exc}")
                    except ValueError:
                        asset_manifest = fallback_asset_manifest(blueprints)
                        corrections.append(f"Plan LLM 全局资产清单已按修订后分镜补全：{exc}")
                authoring_candidate_plan = _with_execution_contract(
                    intent,
                    markdown,
                    candidate_contract,
                    durations,
                    scene_blueprints=blueprints,
                    asset_manifest=asset_manifest,
                )
                authoring_context = (
                    "# 当前已同意版本\n\n"
                    f"{current_plan_markdown.strip()}\n\n"
                    "# 本次结构化修订候选\n\n"
                    f"{authoring_candidate_plan.strip()}"
                )
                blueprints, authoring_corrections = await _author_seedance_plan_blueprints(
                    plan_markdown=authoring_context,
                    scene_blueprints=blueprints,
                    asset_manifest=asset_manifest,
                    creation_contract=candidate_contract,
                    form_values=candidate_form_values,
                    selected_direction=selected_direction,
                    intake_context=context,
                    materials=materials or [],
                    revision_feedback=revision_feedback,
                    redaction_contexts=(profile, context),
                    model_name=model_name,
                    model_factory=model_factory,
                )
                corrections.extend(authoring_corrections)
                durations = scene_blueprint_durations(blueprints)
            markdown = _with_execution_contract(
                intent,
                markdown,
                candidate_contract,
                durations,
                scene_blueprints=blueprints,
                asset_manifest=asset_manifest,
            )
            revised = replace(
                base,
                template_path=template_path,
                consistency_issues=corrections,
                creation_contract=candidate_contract,
                scene_durations_sec=durations,
                scene_blueprints=blueprints,
                asset_manifest=asset_manifest,
                llm_used=True,
                model_name=model_name,
            )
            return revised.next_version(
                plan_markdown=markdown,
                plan_history=plan_history,
                current_version=current_plan_version,
                llm_used=True,
                creation_contract=candidate_contract,
                change_source=change_source,
                scene_blueprints=blueprints,
                asset_manifest=asset_manifest,
            )
        except Exception as exc:  # noqa: BLE001 - 第一次反馈给 LLM 修正，第二次保持原版本
            last_error = exc
            if attempt == 0:
                validation_feedback = str(exc)
                continue

    return _failed_revision_result(
        base=base,
        template_path=template_path,
        current_plan_markdown=current_plan_markdown,
        current_plan_version=current_plan_version,
        plan_history=plan_history,
        creation_contract=original_contract,
        scene_durations_sec=original_durations,
        scene_blueprints=original_blueprints,
        asset_manifest=original_manifest,
        model_name=model_name,
        error=last_error or ValueError("Plan 修订失败"),
    )


def _failed_revision_result(
    *,
    base: PlanMarkdownResult,
    template_path: Path,
    current_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    creation_contract: dict[str, Any],
    scene_durations_sec: list[int],
    scene_blueprints: list[dict[str, Any]],
    asset_manifest: dict[str, list[dict[str, str]]],
    model_name: str,
    error: Exception,
) -> PlanMarkdownResult:
    """修订失败时保留当前权威版本，不创建带非法合同的新历史。"""

    return replace(
        base,
        template_path=template_path,
        plan_markdown=current_plan_markdown,
        plan_version=current_plan_version,
        plan_history=copy.deepcopy(plan_history),
        creation_contract=copy.deepcopy(creation_contract),
        scene_durations_sec=copy.deepcopy(scene_durations_sec),
        scene_blueprints=copy.deepcopy(scene_blueprints),
        asset_manifest=copy.deepcopy(asset_manifest),
        llm_used=False,
        model_name=model_name,
        error=str(error),
    )


def _redact_semantic_memory_payload(payload: dict[str, Any], *contexts: dict[str, Any]) -> dict[str, Any]:
    """允许记忆影响 LLM 决策，但删除模型照抄到用户产物中的记忆原文。"""

    fragments = _semantic_memory_fragments(*contexts)
    if not fragments:
        return copy.deepcopy(payload)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if not isinstance(value, str):
            return copy.deepcopy(value)
        cleaned = value
        for fragment in fragments:
            cleaned = _remove_semantic_memory_fragment(cleaned, fragment)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return redact(payload)


def _remove_semantic_memory_fragment(text: str, fragment: str) -> str:
    """删除记忆原文及仅增加 Markdown 强调/空白的变体。"""

    cleaned = text.replace(fragment, "")
    normalized_fragment, _ = _memory_match_view(fragment)
    if not normalized_fragment:
        return cleaned
    while True:
        normalized_text, positions = _memory_match_view(cleaned)
        match_start = normalized_text.find(normalized_fragment)
        if match_start < 0:
            return cleaned
        original_start = positions[match_start]
        original_end = positions[match_start + len(normalized_fragment) - 1] + 1
        cleaned = cleaned[:original_start] + cleaned[original_end:]


def _memory_match_view(value: str) -> tuple[str, list[int]]:
    """构造忽略 Markdown 强调符和空白的匹配视图，并保留原文索引。"""

    characters: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(value):
        if character.isspace() or character in "*_`~\\":
            continue
        characters.append(character)
        positions.append(index)
    return "".join(characters), positions


def _semantic_memory_fragments(*contexts: dict[str, Any]) -> list[str]:
    fragments: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            semantic_memory = value.get("semantic_memory")
            if isinstance(semantic_memory, dict):
                items = semantic_memory.get("items")
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            content = str(item.get("content") or "").strip()
                            if content and content not in fragments:
                                fragments.append(content)
            for item in value.values():
                if isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for context in contexts:
        collect(context)
    return sorted(fragments, key=len, reverse=True)


def restore_plan_version(
    *,
    intent: CreationIntent,
    current_plan_markdown: str,
    current_plan_version: int,
    plan_history: list[dict[str, Any]],
    restore_version: int,
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
    scene_blueprints: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, list[dict[str, str]]] | None = None,
) -> PlanMarkdownResult:
    history = _normalized_history(plan_history)
    source = next((item for item in history if int(item.get("version") or 0) == restore_version), None)
    if source is None:
        raise ValueError(f"plan.md v{restore_version} 不存在，无法回退")
    source_contract = source.get("creation_contract")
    source_durations = source.get("scene_durations_sec")
    source_blueprints = source.get("scene_blueprints")
    source_manifest = source.get("asset_manifest")
    resolved_contract = copy.deepcopy(source_contract) if "creation_contract" in source and isinstance(source_contract, dict) else copy.deepcopy(creation_contract or {})
    resolved_durations = _resolve_history_scene_durations(
        intent,
        source,
        source_durations,
        resolved_contract,
        scene_durations_sec,
    )
    resolved_blueprints = _resolve_history_scene_blueprints(
        intent,
        source,
        source_blueprints,
        resolved_contract,
        scene_blueprints,
    )
    if resolved_blueprints:
        resolved_durations = scene_blueprint_durations(resolved_blueprints)
    resolved_manifest = _resolve_history_asset_manifest(
        intent,
        source,
        source_manifest,
        resolved_blueprints,
        asset_manifest,
    )
    return PlanMarkdownResult(
        output_type=intent,
        plan_markdown=_sanitize_user_facing_plan_markdown(str(source.get("plan_markdown") or "")),
        template_path=_template_path(intent),
        plan_version=restore_version,
        plan_history=history,
        creation_contract=resolved_contract,
        scene_durations_sec=resolved_durations,
        scene_blueprints=resolved_blueprints,
        asset_manifest=resolved_manifest,
        restored_from_version=restore_version,
    )


async def _replan_initial_asset_budget(
    *,
    blueprints: list[dict[str, Any]],
    budget_issues: list[str],
    intent: CreationIntent,
    template_markdown: str,
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    product_creative_profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
    creation_contract: dict[str, Any],
    total_duration_sec: int,
    model_name: str,
    model_factory: ModelFactory | None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """整份重排超预算 Plan；不允许通过删除资产制造“通过”。"""

    required_assets = _asset_name_sets(blueprints)
    feedback = (
        "以下分镜超过 Seedance 每镜最多 9 张不同图片参考的硬上限："
        f"{'；'.join(budget_issues)}。必须重新规划整份 scene_blueprints，可拆分分镜或重新分配时长与叙事动作，"
        "但总时长必须精确不变、每镜仍为 4-15 个整数秒、故事因果和镜间衔接必须连续。"
        "不得简单截断或删除角色、场景、商品和道具；重排前全部具体资产名称的分类并集必须完整保留，"
        "只把与这些资产关联的动作、对白和镜头内容一起移动到合适分镜。每镜三个资产数组去重后的总数必须不超过 9。"
        f"必须保留的分类资产并集：{json.dumps(required_assets, ensure_ascii=False, default=list)}"
    )
    replanned_payload = await generate_plan_payload(
        intent=intent,
        template_markdown=template_markdown,
        form_values=form_values,
        selected_direction=selected_direction,
        product_creative_profile=product_creative_profile,
        materials=materials,
        intake_context=intake_context,
        creation_contract=creation_contract,
        validation_feedback=feedback,
        model_name=model_name,
        model_factory=model_factory,
    )
    replanned_payload = _redact_semantic_memory_payload(
        replanned_payload,
        product_creative_profile,
        intake_context,
    )
    replanned_markdown = _validated_llm_markdown(
        intent,
        replanned_payload,
        form_values,
        selected_direction,
        intake_context,
    )
    raw_blueprints = replanned_payload.get("scene_blueprints")
    try:
        replanned_blueprints = normalize_scene_blueprints(
            raw_blueprints,
            total_duration_sec=total_duration_sec,
        )
    except ValueError:
        try:
            replanned_blueprints = repair_scene_blueprints_schedule(
                raw_blueprints,
                total_duration_sec=total_duration_sec,
            )
        except ValueError:
            replanned_blueprints = salvage_scene_blueprints(
                raw_blueprints,
                total_duration_sec=total_duration_sec,
                visual_style=_text(form_values.get("visual_style"), "真实广告风格"),
            )
    validate_asset_requirement_quality(replanned_blueprints)
    actual_assets = _asset_name_sets(replanned_blueprints)
    if actual_assets != required_assets:
        raise ValueError(
            "分镜九图预算重排不得删除、增加或改名全局资产；"
            f"重排前={required_assets}，重排后={actual_assets}"
        )
    return replanned_payload, replanned_markdown, replanned_blueprints


def _asset_name_sets(blueprints: list[dict[str, Any]]) -> dict[str, set[str]]:
    result = {"characters": set(), "scenes": set(), "props": set()}
    for blueprint in blueprints:
        requirements = blueprint.get("asset_requirements")
        if not isinstance(requirements, dict):
            continue
        for collection in result:
            values = requirements.get(collection)
            if isinstance(values, list):
                result[collection].update(str(value).strip() for value in values if str(value).strip())
    return result


async def _author_seedance_plan_blueprints(
    *,
    plan_markdown: str,
    scene_blueprints: list[dict[str, Any]],
    asset_manifest: dict[str, list[dict[str, str]]],
    creation_contract: dict[str, Any],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any],
    materials: list[dict[str, Any]],
    revision_feedback: str = "",
    redaction_contexts: tuple[dict[str, Any], ...] = (),
    model_name: str,
    model_factory: ModelFactory | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """在稳定资产 ID 生成后执行专用写作；非法响应整批拒绝并重试一次。"""
    validation_feedback = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            payload = await author_seedance_plan_payload(
                plan_markdown=plan_markdown,
                scene_blueprints=scene_blueprints,
                asset_manifest=asset_manifest,
                creation_contract=creation_contract,
                form_values=form_values,
                selected_direction=selected_direction,
                intake_context=intake_context,
                materials=materials,
                revision_feedback=revision_feedback,
                validation_feedback=validation_feedback,
                model_name=model_name,
                model_factory=model_factory,
            )
            if redaction_contexts:
                payload = _redact_semantic_memory_payload(payload, *redaction_contexts)
            authored = apply_seedance_plan_authoring(
                scene_blueprints,
                payload.get("scene_blueprints"),
                asset_manifest=asset_manifest,
                total_duration_sec=int(creation_contract.get("video_duration_sec") or 0),
            )
            corrections = ["Seedance 分镜已根据专用校验反馈重新生成"] if attempt else []
            return authored, corrections
        except Exception as exc:  # noqa: BLE001 - 专用写作失败时不能污染权威 Plan 合同
            last_error = exc
            validation_feedback = str(exc)

    rebuilt = rebuild_scene_shot_descriptions(
        scene_blueprints,
        visual_style=_text(form_values.get("visual_style"), "真实广告风格"),
        total_duration_sec=int(creation_contract.get("video_duration_sec") or 0),
    )
    bound = bind_seedance_plan_assets(
        rebuilt,
        asset_manifest=asset_manifest,
        total_duration_sec=int(creation_contract.get("video_duration_sec") or 0),
    )
    return bound, [
        "Seedance 专用分镜写作连续两次未通过，已在保留故事线、对白和资产合同的前提下重建连续秒段并按规则绑定稳定资产："
        f"{last_error or '未知错误'}"
    ]


def _video_contract_and_blueprints(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
) -> tuple[VideoCreationContract, list[dict[str, Any]], list[str]]:
    contract = build_video_creation_contract(form_values)
    contract, corrections = resolve_scene_image_spec(
        contract,
        _text(form_values.get("scene_image_ratio")) or contract.video_ratio,
        _text(form_values.get("scene_image_size")) or "4K",
    )
    return contract, _fallback_blueprints(form_values, selected_direction, contract), corrections


def _fallback_blueprints(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    contract: VideoCreationContract,
) -> list[dict[str, Any]]:
    return fallback_scene_blueprints(
        total_duration_sec=contract.video_duration_sec,
        product_name=_text(form_values.get("product_info"), "产品"),
        direction_description=_text(
            selected_direction.get("description"),
            _text(selected_direction.get("title"), "围绕产品完成卖点证明"),
        ),
        visual_style=contract.visual_style or "真实广告风格",
        conversion_goal=_text(form_values.get("conversion_goal"), "完成转化"),
    )


def _fallback_video_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
    contract: VideoCreationContract,
    durations: list[int],
    blueprints: list[dict[str, Any]],
    asset_manifest: dict[str, list[dict[str, str]]],
) -> str:
    product = _context_text_value(intake_context, "product_subject") or _text(form_values.get("product_info"), "未命名产品")
    category = _text(form_values.get("product_category"), "未分类")
    audience = _text(form_values.get("target_audience"), "目标用户")
    conversion_goal = _text(form_values.get("conversion_goal"), "完成转化")
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕产品卖点组织完整方案。")
    visual_style = contract.visual_style or "真实广告风格"
    anchor = _visual_anchor(selected_direction, profile)
    scene_lines = []
    for blueprint in blueprints:
        index = int(blueprint["scene_index"])
        scene_lines.append(
            f"- 镜头{index}-「{_timecode(int(blueprint['start_sec']))}-{_timecode(int(blueprint['end_sec']))}」"
            f"（{blueprint['structure_role']}，{blueprint['duration_sec']}秒）\n"
            f"  - 故事线：{blueprint['storyline']}\n"
            f"  - 镜头描述：{blueprint['shot_description']}\n"
            f"  - 旁白：{blueprint['narration']}\n"
            f"  - 转场：{blueprint['transition']}"
        )
    markdown = f"""# {product} — {direction_title}

## 一、选题方向

{direction_description}

产品定位：{product} = 面向 {audience} 的 {category} 内容主角。
产品剧情角色：作为解决问题、证明卖点和推动转化的关键要素。
系列记忆句：看见需求，想到 {product}。

## 二、选题优势

- **爆点机制**：开场建立冲突，中段完成产品证明，结尾收口到 {conversion_goal}
- **人群**：{audience}
- **依据**：{anchor}；素材：{_material_summary(materials)}
- **转化逻辑链**：痛点 -> 产品介入 -> 效果证明 -> {conversion_goal}

## 三、视频规格

- 任务类型：{contract.video_usage}
- 画幅：{contract.video_ratio}
- 时长：{contract.video_duration_sec} 秒
- 时间轴：00:00-{_timecode(contract.video_duration_sec)}
- 视频模型：{contract.video_model}
- 图片模型：{contract.image_model}
- 风格：{visual_style}
- 转化目标：{conversion_goal}

## 四、角色列表

- 主角：{audience} 中具有代表性的人物，只生成真实人物三视图。
- 产品/商品：{product}，作为道具资产，不放入人物角色栏目。
- 场景与道具：围绕 {anchor} 规划，保持全片视觉一致。

## 五、镜头列表

{chr(10).join(scene_lines)}

## 背景音乐

- 前段：建立注意力和冲突。
- 中段：推动产品证明。
- 后段：完成 {conversion_goal} 收口。

## 前3秒钩子

用 {audience} 的高频痛点和明确动作建立悬念，并在 3 秒内让用户理解观看理由。
"""
    return _with_execution_contract(
        "video",
        markdown,
        contract.model_dump(exclude_none=True),
        durations,
        scene_blueprints=blueprints,
        asset_manifest=asset_manifest,
    )


def _fallback_image_plan(
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    profile: dict[str, Any],
    materials: list[dict[str, Any]],
    intake_context: dict[str, Any],
) -> str:
    goal = _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal"), "图片创作目标")
    subject = _context_text_value(intake_context, "product_subject") or goal
    direction_title = _text(selected_direction.get("title"), "推荐创意方向")
    direction_description = _text(selected_direction.get("description"), "围绕主体建立清晰主视觉。")
    usage = _text(form_values.get("image_usage"), "内容发布")
    style = _text(form_values.get("image_style"), "真实摄影")
    size = _text(form_values.get("image_size"), "自动适配")
    image_type = _text(form_values.get("image_type"), "图片")
    anchor = _visual_anchor(selected_direction, profile)
    count = _context_int_value(intake_context, "requested_output_count") or _positive_int(form_values.get("image_count"), 1)
    original_prompt = _context_text_value(intake_context, "source_prompt")
    industry_type = _context_text_value(intake_context, "industry_type") or "general"
    markdown = f"""# {goal}｜{direction_title}

## 一、选题方向

{direction_description}

原始需求：{original_prompt or "未提供"}
产品主体：{subject}
创作目标：{goal}
行业类型：{industry_type}
核心表达：围绕 {subject}，用 {anchor} 建立可直接用于 {usage} 的成品视觉。
产品定位：{subject} = 当前画面的唯一核心主体。
记忆句：一眼看到重点，一张图完成表达。

## 二、选题优势

- **爆点机制**：主体聚焦、卖点清楚、风格统一
- **人群与用途**：{usage}
- **素材依据**：{_material_summary(materials)}

## 三、图片规格

- 任务类型：{image_type}
- 尺寸：{size}
- 生成数量：{count} 张
- 用途：{usage}
- 风格：{style}

## 四、画面元素

- 主视觉主体：{subject}
- 场景环境：围绕 {anchor} 组织
- 信息元素：标题、辅助文案或视觉标签按用途保留
- 道具关系：只保留能帮助表达卖点的元素

## 五、主图方案

### 方案A：{direction_title}

**画面**：{direction_description}，主体清晰，构图稳定，符合 {style}。
**主标题**：围绕 {goal} 提炼 18 字以内标题。
**CTA**：根据 {usage} 给出明确行动提示。

## 六、视觉重点

- 主体和核心卖点必须可识别。
- 避免无关文字、水印和虚假承诺。
- 所有生成结果严格继承本方案的用途、风格、尺寸和数量。

## 七、图片钩子

通过 {anchor} 在第一眼建立主题和记忆点。
"""
    return _with_execution_contract("image", markdown, _image_creation_contract(form_values, intake_context), [])


def _with_execution_contract(
    intent: CreationIntent,
    markdown: str,
    creation_contract: dict[str, Any],
    durations: list[int],
    *,
    scene_blueprints: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    base = _sanitize_user_facing_plan_markdown(markdown).split("\n## 制作执行合同", 1)[0].rstrip()
    if intent == "video":
        if asset_manifest is not None:
            base = _replace_video_asset_manifest_section(base, asset_manifest)
        if scene_blueprints:
            base = _replace_video_scene_section(base, scene_blueprints)
        ranges = scene_time_ranges(durations)
        timeline = "\n".join(f"- 分镜{index}：{_timecode(start)}-{_timecode(end)}，{duration} 秒" for index, ((start, end), duration) in enumerate(zip(ranges, durations, strict=True), start=1))
        contract_block = f"""## 制作执行合同

- 视频总时长：{creation_contract.get("video_duration_sec")} 秒
- 视频画幅：{creation_contract.get("video_ratio")}
- 视频模型：{creation_contract.get("video_model")}
- 视频清晰度：{creation_contract.get("video_size")}
- 图片模型：{creation_contract.get("image_model")}
- 图片比例：{creation_contract.get("scene_image_ratio")}
- 图片清晰度：{creation_contract.get("scene_image_size")}
- 视频用途：{creation_contract.get("video_usage")}
- 视觉风格：{creation_contract.get("visual_style") or "由当前 Plan 统一约束"}
- 执行规则：角色、场景、道具图片及全部分镜视频必须继承本合同；不得改用其他模型或比例。

### 精确分镜时间线

{timeline}
"""
    else:
        contract_block = f"""## 制作执行合同

- 图片目标：{creation_contract.get("image_goal")}
- 图片类型：{creation_contract.get("image_type")}
- 图片用途：{creation_contract.get("image_usage")}
- 图片风格：{creation_contract.get("image_style")}
- 图片尺寸：{creation_contract.get("image_size")}
- 生成数量：{creation_contract.get("image_count")} 张
- 执行规则：后续图片生成必须严格继承当前 plan.md 和本合同。
"""
    return f"{base}\n\n{contract_block.strip()}\n"


def _replace_video_scene_section(markdown: str, scene_blueprints: list[dict[str, Any]]) -> str:
    """用通过合同校验的蓝图替换 LLM 正文镜头，保证用户只审核一条时间线。"""

    pattern = re.compile(r"(?ms)^##\s*五、镜头列表\s*$.*?(?=^##\s|\Z)")
    replacement = f"## 五、镜头列表\n\n{render_scene_blueprints_markdown(scene_blueprints)}\n\n"
    return pattern.sub(replacement, markdown, count=1).rstrip()


def _replace_video_asset_manifest_section(
    markdown: str,
    asset_manifest: dict[str, list[dict[str, str]]],
) -> str:
    """第四章始终由结构化清单渲染，名称和生图约束不依赖自由文本。"""

    replacement = f"{render_asset_manifest_markdown(asset_manifest)}\n\n"
    pattern = re.compile(r"(?ms)^##\s*四、(?:角色列表|全局资产清单)\s*$.*?(?=^##\s|\Z)")
    if pattern.search(markdown):
        return pattern.sub(replacement, markdown, count=1).rstrip()
    scene_heading = re.search(r"(?m)^##\s*五、镜头列表\s*$", markdown)
    if scene_heading:
        return f"{markdown[:scene_heading.start()].rstrip()}\n\n{replacement}{markdown[scene_heading.start():]}".rstrip()
    return f"{markdown.rstrip()}\n\n{replacement}".rstrip()


def _validated_llm_markdown(
    intent: CreationIntent,
    payload: dict[str, Any],
    form_values: dict[str, Any],
    selected_direction: dict[str, Any],
    intake_context: dict[str, Any],
) -> str:
    markdown = _sanitize_user_facing_plan_markdown(_text(payload.get("plan_markdown")))
    if not markdown:
        raise ValueError("Plan LLM response is missing plan_markdown")
    missing = [section for section in _REQUIRED_TEMPLATE_SECTIONS[intent] if section not in markdown]
    if missing:
        raise ValueError(f"Plan LLM response is missing sections: {', '.join(missing)}")
    allowed_text = json.dumps([form_values, selected_direction, intake_context], ensure_ascii=False, default=str)
    leaked = [entity for entity in _TEMPLATE_SAMPLE_ENTITIES if entity in markdown and entity not in allowed_text]
    if leaked:
        raise ValueError(f"Plan LLM copied template sample entities: {', '.join(leaked)}")
    return markdown


def _load_template(intent: CreationIntent) -> tuple[Path, str]:
    path = _template_path(intent)
    text = path.read_text(encoding="utf-8")
    missing = [section for section in _REQUIRED_TEMPLATE_SECTIONS[intent] if section not in text]
    if missing:
        raise ValueError(f"{path.name} 模板缺少固定章节：{', '.join(missing)}")
    return path, text


def _template_path(intent: CreationIntent) -> Path:
    return VIDEO_PLAN_TEMPLATE_PATH if intent == "video" else IMAGE_PLAN_TEMPLATE_PATH


def _consistency_issues(intent: CreationIntent, form_values: dict[str, Any], selected_direction: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not selected_direction.get("direction_id"):
        issues.append("缺少 selected_direction.direction_id")
    if not selected_direction.get("title"):
        issues.append("缺少 selected_direction.title")
    required = ("product_info", "product_category", "target_audience", "conversion_goal") if intent == "video" else ("image_goal", "image_type", "image_usage", "image_style", "image_size")
    for field_name in required:
        if not _text(form_values.get(field_name)):
            issues.append(f"{intent} 表单缺少 {field_name}")
    return issues


def _image_creation_contract(form_values: dict[str, Any], intake_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": "image",
        "image_goal": _context_text_value(intake_context, "creation_goal") or _text(form_values.get("image_goal")),
        "image_type": _text(form_values.get("image_type")),
        "image_usage": _text(form_values.get("image_usage")),
        "image_style": _text(form_values.get("image_style")),
        "image_size": _text(form_values.get("image_size"), "自动适配"),
        "image_count": _context_int_value(intake_context, "requested_output_count") or _positive_int(form_values.get("image_count"), 1),
    }


def _scene_timeline(durations: list[int]) -> list[dict[str, int]]:
    return [{"scene_index": index, "start_sec": start, "end_sec": end, "duration_sec": duration} for index, ((start, end), duration) in enumerate(zip(scene_time_ranges(durations), durations, strict=True), start=1)]


def _scene_stage(index: int, count: int) -> str:
    ratio = index / max(1, count)
    if index == 1:
        return "以强动作和明确痛点开场"
    if ratio <= 0.3:
        return "补充人物目标与使用场景，逐步升级问题"
    if ratio <= 0.65:
        return "让产品自然介入并展示关键使用过程"
    if ratio <= 0.85:
        return "用细节、对比或反馈证明产品价值"
    return "收束结果并给出清晰行动提示"


def _history_entry(
    version: int,
    plan_markdown: str,
    restored_from_version: int | None = None,
    *,
    creation_contract: dict[str, Any] | None = None,
    scene_durations_sec: list[int] | None = None,
    change_source: str | None = None,
    scene_blueprints: list[dict[str, Any]] | None = None,
    asset_manifest: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "version": version,
        "plan_markdown": plan_markdown,
        "creation_contract": copy.deepcopy(creation_contract or {}),
        "scene_durations_sec": copy.deepcopy(scene_durations_sec or []),
        "scene_blueprints": copy.deepcopy(scene_blueprints or []),
        "asset_manifest": copy.deepcopy(asset_manifest or empty_asset_manifest()),
    }
    if restored_from_version is not None:
        item["restored_from_version"] = restored_from_version
    if change_source:
        item["change_source"] = change_source
    return item


def _normalized_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in history:
        if not isinstance(item, dict):
            continue
        version = _positive_int(item.get("version"), 0)
        markdown = _text(item.get("plan_markdown"))
        if version <= 0 or not markdown or version in seen:
            continue
        seen.add(version)
        result.append(copy.deepcopy(item))
    return sorted(result, key=lambda item: int(item["version"]))


def _resolve_history_scene_durations(
    intent: CreationIntent,
    source: dict[str, Any],
    source_durations: Any,
    resolved_contract: dict[str, Any],
    fallback_durations: list[int] | None,
) -> list[int]:
    validated_fallback = _validated_history_duration_fallback(
        intent,
        fallback_durations,
        resolved_contract,
    )
    if "scene_durations_sec" not in source or not isinstance(source_durations, list):
        return validated_fallback
    if intent == "video":
        expected_duration = resolved_contract.get("video_duration_sec")
        is_valid_duration = isinstance(expected_duration, int) and not isinstance(expected_duration, bool)
        is_valid_scenes = all(isinstance(value, int) and not isinstance(value, bool) and 4 <= value <= 15 for value in source_durations)
        if not is_valid_duration or not is_valid_scenes or sum(source_durations) != expected_duration:
            return validated_fallback
    try:
        return [int(value) for value in source_durations]
    except (TypeError, ValueError):
        return validated_fallback


def _validated_history_duration_fallback(
    intent: CreationIntent,
    fallback_durations: list[int] | None,
    resolved_contract: dict[str, Any],
) -> list[int]:
    values = copy.deepcopy(fallback_durations or [])
    if intent != "video":
        return values
    expected_duration = resolved_contract.get("video_duration_sec")
    if isinstance(expected_duration, bool) or not isinstance(expected_duration, int):
        return []
    if not values or not all(isinstance(value, int) and not isinstance(value, bool) and 4 <= value <= 15 for value in values):
        return []
    return values if sum(values) == expected_duration else []


def _resolve_history_scene_blueprints(
    intent: CreationIntent,
    source: dict[str, Any],
    source_blueprints: Any,
    resolved_contract: dict[str, Any],
    fallback_blueprints: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if intent != "video":
        return []
    total_duration = resolved_contract.get("video_duration_sec")
    validated_fallback = _validated_history_blueprint_fallback(fallback_blueprints, total_duration)
    if "scene_blueprints" not in source or not isinstance(source_blueprints, list):
        return validated_fallback
    if isinstance(total_duration, bool) or not isinstance(total_duration, int):
        return []
    try:
        return normalize_scene_blueprints(
            copy.deepcopy(source_blueprints),
            total_duration_sec=total_duration,
        )
    except ValueError:
        return validated_fallback


def _validated_history_blueprint_fallback(
    fallback_blueprints: list[dict[str, Any]] | None,
    total_duration: Any,
) -> list[dict[str, Any]]:
    if not fallback_blueprints or isinstance(total_duration, bool) or not isinstance(total_duration, int):
        return []
    try:
        return normalize_scene_blueprints(
            copy.deepcopy(fallback_blueprints),
            total_duration_sec=total_duration,
        )
    except ValueError:
        return []


def _resolve_history_asset_manifest(
    intent: CreationIntent,
    source: dict[str, Any],
    source_manifest: Any,
    resolved_blueprints: list[dict[str, Any]],
    fallback_manifest: dict[str, list[dict[str, str]]] | None,
) -> dict[str, list[dict[str, str]]]:
    if intent != "video":
        return empty_asset_manifest()
    if not resolved_blueprints:
        return empty_asset_manifest()
    candidates: list[Any] = []
    if "asset_manifest" in source:
        candidates.append(source_manifest)
    candidates.append(fallback_manifest)
    for candidate in candidates:
        try:
            return normalize_asset_manifest(candidate, resolved_blueprints)
        except ValueError:
            continue
    return fallback_asset_manifest(resolved_blueprints)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _context_text_value(intake_context: dict[str, Any], key: str) -> str:
    return _text(intake_context.get(key))


def _context_int_value(intake_context: dict[str, Any], key: str) -> int | None:
    value = _positive_int(intake_context.get(key), 0)
    return max(1, min(10, value)) if value else None


def _merged_profile(product_creative_profile: dict[str, Any], intake_context: dict[str, Any]) -> dict[str, Any]:
    context_profile = intake_context.get("product_creative_profile")
    return {**context_profile, **product_creative_profile} if isinstance(context_profile, dict) else product_creative_profile


def _visual_anchor(selected_direction: dict[str, Any], product_creative_profile: dict[str, Any]) -> str:
    data = selected_direction.get("data")
    if isinstance(data, dict) and _text(data.get("visual_anchor")):
        return _text(data.get("visual_anchor"))
    anchors = product_creative_profile.get("visual_anchor_keywords")
    if isinstance(anchors, list):
        normalized = "、".join(_text(item) for item in anchors[:3] if _text(item))
        if normalized:
            return normalized
    return "产品质感、真实使用、转化动作"


def _sanitize_user_facing_plan_markdown(markdown: str) -> str:
    """保留记忆对策划的隐式影响，但不把内部记忆与运行日志展示给用户。"""

    if not markdown:
        return markdown
    cleaned = re.sub(
        r"(?ms)^#{1,6}\s*(?:长期记忆约束|内部记忆|PowerMem[^\n]*)\s*$.*?(?=^#{1,6}\s|\Z)",
        "",
        markdown,
    )
    internal_labels = ("长期记忆约束", "PowerMem 记忆", "语义记忆上下文")
    internal_runtime_markers = (
        "stage=",
        "用户创作上下文",
        "采集 Agent 完成意图识别",
        "Skill 经验",
        "Agent 阶段日志",
    )
    lines: list[str] = []
    skipping_internal_item = False
    for line in cleaned.splitlines():
        if any(label in line for label in internal_labels):
            skipping_internal_item = bool(re.match(r"^\s*[-*+]\s+", line))
            continue
        if skipping_internal_item:
            if re.match(r"^\s*(?:[-*+]\s+|#{1,6}\s+)", line):
                skipping_internal_item = False
            else:
                continue
        if any(marker in line for marker in internal_runtime_markers):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _material_summary(materials: list[dict[str, Any]]) -> str:
    return f"{len(materials)} 个可引用素材" if materials else "暂无额外素材，按表单和创意方向执行"


def _timecode(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"
