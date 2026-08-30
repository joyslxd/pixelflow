"""Workspace Payload V2 的四层合同与旧平面字段迁移。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

WORKSPACE_SCHEMA_VERSION = 2
_ARTIFACT_PREFIX = "artifact:"
MIN_SCENE_DURATION_SEC = 4
MAX_SCENE_DURATION_SEC = 30

AssetState = Literal["planned", "generating", "ready", "failed"]
AssetOrigin = Literal["existing_material", "planned_generation", "provider_output"]
GenerationMode = Literal["independent", "extend", "reference"]


class WorkspaceAssetRecord(BaseModel):
    """资产注册表中的稳定记录，不保存授权或 Provider 原始响应。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    slot: str | None = Field(default=None, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=256)
    # 区分用户已经提供的素材和需要后续生成的素材，前端与 Provider 都以此字段编排，
    # 不能通过是否存在 URL 推断，避免把外部 URL 透传进 Prompt Package。
    origin: AssetOrigin = "planned_generation"
    source_material_id: str | None = Field(default=None, max_length=128)
    state: AssetState = "planned"
    reference_asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    provider_artifact_ref: str | None = Field(default=None, max_length=256)
    usable_for_video: bool = False

    @model_validator(mode="after")
    def validate_artifact(self) -> WorkspaceAssetRecord:
        if self.provider_artifact_ref is not None and not self.provider_artifact_ref.startswith(
            _ARTIFACT_PREFIX
        ):
            raise ValueError("provider_artifact_ref 必须是内部 Artifact 引用")
        if self.state == "ready" and not self.provider_artifact_ref:
            raise ValueError("ready 资产必须有内部 Artifact 引用")
        if self.usable_for_video and self.state != "ready":
            raise ValueError("只有 ready 资产可以进入视频生成")
        if self.origin == "existing_material":
            if not self.source_material_id:
                raise ValueError("已有素材必须关联 source_material_id")
            if self.state != "ready" or not self.usable_for_video:
                raise ValueError("已有素材必须处于 ready 且可用于视频")
        return self


class WorkspacePromptPackage(BaseModel):
    """单段可执行 Prompt Package；允许长片按 segment 独立调度。"""

    # 保留导演文档中的扩展字段（对白、时代证据、镜头硬约束等），核心字段仍受下方合同校验。
    model_config = ConfigDict(extra="allow", frozen=True)

    segment_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    duration_sec: int = Field(ge=4, le=30)
    generation_mode: GenerationMode = "independent"
    prompt: str = Field(min_length=1, max_length=20_000)
    reference_asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    continuity_from: str | None = Field(default=None, max_length=128)
    transition_out: str | None = Field(default=None, max_length=2_000)
    era: str | None = Field(default=None, max_length=512)
    camera: str | None = Field(default=None, max_length=2_000)
    sound: str | None = Field(default=None, max_length=2_000)
    hard_constraints: tuple[str, ...] = Field(default=(), max_length=64)
    video_artifact_refs: tuple[str, ...] = Field(default=(), max_length=8)
    operation_ids: tuple[str, ...] = Field(default=(), max_length=8)
    state: Literal["planned", "generating", "ready", "failed"] = "planned"


class WorkspaceCreativeBrief(BaseModel):
    """创意与生产约束；字段可选以兼容逐步补齐的用户需求。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    brand: str | None = Field(default=None, max_length=256)
    product: str | None = Field(default=None, max_length=512)
    audience: str | None = Field(default=None, max_length=2_000)
    platform: str | None = Field(default=None, max_length=128)
    aspect_ratio: str | None = Field(default=None, max_length=32)
    target_duration_sec: int | None = Field(default=None, ge=1, le=3_600)
    audio: str | None = Field(default=None, max_length=2_000)
    cta: str | None = Field(default=None, max_length=2_000)


class WorkspacePayloadV2(BaseModel):
    """四层 Payload 的顶层合同；其它运行时投影字段原样保留。"""

    model_config = ConfigDict(extra="allow", frozen=True)

    workspace_schema_version: Literal[2] = WORKSPACE_SCHEMA_VERSION
    creative_brief: WorkspaceCreativeBrief = Field(default_factory=WorkspaceCreativeBrief)
    narrative_plan: dict[str, JsonValue] = Field(default_factory=dict)
    asset_registry: tuple[WorkspaceAssetRecord, ...] = ()
    prompt_packages: tuple[WorkspacePromptPackage, ...] = ()


def migrate_workspace_payload(payload: Mapping[str, object] | None) -> dict[str, JsonValue]:
    """把旧平面 Workspace 投影为 V2；不删除旧字段，供前端和旧 Adapter 兼容。"""

    source = deepcopy(dict(payload)) if isinstance(payload, Mapping) else {}
    version = source.get("workspace_schema_version")
    if version == WORKSPACE_SCHEMA_VERSION:
        return source  # type: ignore[return-value]

    if "creative_brief" not in source:
        product = source.get("product_info")
        brief: dict[str, JsonValue] = dict(product) if isinstance(product, dict) else {}
        for old_key, new_key in (
            ("ratio", "aspect_ratio"),
            ("video_ratio", "aspect_ratio"),
            ("duration_sec", "target_duration_sec"),
            ("platform", "platform"),
            ("audio", "audio"),
            ("cta", "cta"),
        ):
            if new_key not in brief and old_key in source:
                brief[new_key] = source[old_key]  # type: ignore[assignment]
        source["creative_brief"] = brief

    if "narrative_plan" not in source:
        script = source.get("script")
        narrative: dict[str, JsonValue] = {}
        if isinstance(script, Mapping):
            if isinstance(script.get("content"), str):
                narrative["script"] = script["content"]
            if isinstance(script.get("status"), str):
                narrative["status"] = script["status"]
        if isinstance(source.get("script_pipeline"), dict):
            narrative["pipeline"] = source["script_pipeline"]  # type: ignore[assignment]
        source["narrative_plan"] = narrative

    if "asset_registry" not in source:
        source["asset_registry"] = _legacy_asset_registry(source)

    if "prompt_packages" not in source:
        source["prompt_packages"] = _legacy_prompt_packages(source)

    source["workspace_schema_version"] = WORKSPACE_SCHEMA_VERSION
    return source  # type: ignore[return-value]


def _legacy_asset_registry(source: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    buckets = {"characters": "character", "scenes": "scene", "props": "prop"}
    records: list[dict[str, JsonValue]] = []
    for bucket, kind in buckets.items():
        value = source.get("global_assets")
        items = value.get(bucket) if isinstance(value, Mapping) else None
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if not isinstance(item, Mapping):
                continue
            asset_id = str(item.get("asset_id") or item.get("id") or f"{bucket}-{index}").strip()
            artifact = item.get("artifact_ref")
            ready = isinstance(artifact, str) and artifact.startswith(_ARTIFACT_PREFIX)
            records.append(
                {
                    "asset_id": asset_id,
                    "slot": f"{bucket}-{index}",
                    "kind": kind,
                    "role": str(item.get("name") or item.get("title") or asset_id)[:256],
                    "origin": "provider_output" if ready else "planned_generation",
                    "state": "ready" if ready else "planned",
                    "provider_artifact_ref": artifact if ready else None,
                    "usable_for_video": ready,
                }
            )
    return records


def _legacy_prompt_packages(source: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    raw = source.get("scenes") or source.get("scene_packages")
    if not isinstance(raw, list):
        return []
    packages: list[dict[str, JsonValue]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            continue
        segment_id = str(item.get("segment_id") or item.get("scene_id") or f"scene-{index}").strip()
        prompt = str(item.get("prompt") or item.get("storyline") or item.get("shot_description") or "").strip()
        if not prompt:
            continue
        duration = item.get("duration_sec") or item.get("duration") or 4
        if not isinstance(duration, int) or isinstance(duration, bool):
            duration = 4
        packages.append(
            {
                **dict(item),
                "segment_id": segment_id,
                "sequence": int(item.get("sequence") or item.get("scene_index") or index),
                "duration_sec": max(4, min(30, duration)),
                "generation_mode": item.get("generation_mode") or "independent",
                "prompt": prompt,
                "state": "ready" if item.get("video_url") else "planned",
            }
        )
    return packages


__all__ = [
    "AssetState",
    "GenerationMode",
    "MAX_SCENE_DURATION_SEC",
    "MIN_SCENE_DURATION_SEC",
    "WORKSPACE_SCHEMA_VERSION",
    "WorkspaceAssetRecord",
    "WorkspaceCreativeBrief",
    "WorkspacePayloadV2",
    "WorkspacePromptPackage",
    "migrate_workspace_payload",
]
