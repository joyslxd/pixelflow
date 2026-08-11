"""方案 1：规则歧义时由 LLM 只选入口路径，不展开具体 Skill steps。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pixelflow.video_agent.contracts import VideoWorkspace

logger = logging.getLogger(__name__)

ScriptEntryPath = Literal["create", "polish", "continue", "inspect"]
ENTRY_PATH_VALUES: tuple[ScriptEntryPath, ...] = (
    "create",
    "polish",
    "continue",
    "inspect",
)

DEFAULT_ENTRY_PATH_TIMEOUT_SECONDS = 4.0


class EntryPathProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    entry_path: ScriptEntryPath
    reason: str = Field(default="", max_length=500)


class EntryPathModel(Protocol):
    async def propose(self, evidence: Mapping[str, Any]) -> EntryPathProposal: ...


class DeepSeekEntryPathModel:
    """短 structured 调用：只输出 create/polish/continue/inspect。"""

    def __init__(
        self,
        *,
        app_config: object | None = None,
        model_factory: Callable[..., Any] | None = None,
        model_name: str = "deepseek-v4-pro",
    ) -> None:
        self._app_config = app_config
        self._model_factory = model_factory
        self._model_name = model_name

    async def propose(self, evidence: Mapping[str, Any]) -> EntryPathProposal:
        import json

        model_factory = self._model_factory
        if model_factory is None:
            from deerflow.models import create_chat_model

            model_factory = create_chat_model
        model = model_factory(
            name=self._model_name,
            thinking_enabled=False,
            app_config=self._app_config,
        )
        structured = model.with_structured_output(EntryPathProposal)
        system_prompt = (
            "你是 PixelFlow VideoAgent 入口路径选择器。只输出 entry_path 与短 reason，"
            "不输出思维链，不点名具体 Skill 步骤。"
            "四选一含义："
            "create=从选题/故事全流程创作脚本；"
            "polish=用户已有成稿，只做自检/合规/导出；"
            "continue=脚本已确认，准备资产包/成片；"
            "inspect=仅探查工作区，不创作。"
            "改创意、补镜头、加转折、在已有主题上继续编故事 → create；"
            "用户贴了完整分镜/成稿要润色 → polish；"
            "明确继续生成视频且脚本已就绪 → continue；"
            "完全无关或信息不足 → inspect。"
        )
        result = await structured.ainvoke(
            [
                ("system", system_prompt),
                ("human", json.dumps(dict(evidence), ensure_ascii=False)),
            ]
        )
        return EntryPathProposal.model_validate(result)


def build_entry_path_evidence(
    *,
    content: str,
    materials: Sequence[Mapping[str, Any]],
    workspace: VideoWorkspace,
    rule_path: ScriptEntryPath,
) -> dict[str, Any]:
    payload = workspace.payload if isinstance(workspace.payload, dict) else {}
    pipeline = payload.get("script_pipeline")
    stages: list[str] = []
    if isinstance(pipeline, Mapping):
        for key, value in pipeline.items():
            if (
                isinstance(key, str)
                and isinstance(value, Mapping)
                and isinstance(value.get("content"), str)
                and str(value.get("content") or "").strip()
            ):
                stages.append(key)
    script = payload.get("script")
    has_script = (
        isinstance(script, Mapping)
        and isinstance(script.get("content"), str)
        and bool(str(script.get("content") or "").strip())
    )
    latest = payload.get("latest_input")
    return {
        "user_content": content[:2_000],
        "rule_path": rule_path,
        "has_materials": bool(materials),
        "material_count": len(materials),
        "has_script": has_script,
        "script_plan_confirmed": payload.get("script_plan_confirmed") is True,
        "script_entry_path": payload.get("script_entry_path"),
        "pipeline_stages": stages[:12],
        "latest_input_preview": (
            latest.strip()[:500] if isinstance(latest, str) and latest.strip() else ""
        ),
    }


def should_ask_entry_path_llm(
    *,
    rule_path: ScriptEntryPath,
    content: str,
    materials: Sequence[Mapping[str, Any]],
    workspace: VideoWorkspace,
) -> bool:
    """仅在规则落 inspect、且仍像视频创作跟进时问 LLM。"""

    if rule_path != "inspect":
        return False
    if materials:
        return True
    text = content.strip()
    if len(text) >= 40:
        return True
    evidence = build_entry_path_evidence(
        content=text,
        materials=materials,
        workspace=workspace,
        rule_path=rule_path,
    )
    if evidence["has_script"] or evidence["pipeline_stages"] or evidence["latest_input_preview"]:
        return True
    return False


def sanitize_entry_path_proposal(
    proposed: ScriptEntryPath,
    *,
    content: str,
    workspace: VideoWorkspace,
    is_complete_script: Callable[[str], bool],
    has_generatable_script: Callable[[VideoWorkspace], bool],
) -> ScriptEntryPath:
    """服务端消毒：LLM 不能绕过成片确认或把非成稿标成 polish。"""

    if proposed not in ENTRY_PATH_VALUES:
        return "inspect"
    if proposed == "continue":
        if (
            has_generatable_script(workspace)
            and workspace.payload.get("script_plan_confirmed") is True
        ):
            return "continue"
        return "inspect"
    if proposed == "polish":
        if is_complete_script(content):
            return "polish"
        return "create"
    return proposed


async def select_entry_path_with_llm(
    *,
    content: str,
    materials: Sequence[Mapping[str, Any]],
    workspace: VideoWorkspace,
    rule_path: ScriptEntryPath,
    model: EntryPathModel | None = None,
    timeout_seconds: float = DEFAULT_ENTRY_PATH_TIMEOUT_SECONDS,
    is_complete_script: Callable[[str], bool],
    has_generatable_script: Callable[[VideoWorkspace], bool],
) -> ScriptEntryPath:
    if not should_ask_entry_path_llm(
        rule_path=rule_path,
        content=content,
        materials=materials,
        workspace=workspace,
    ):
        return rule_path
    if model is None:
        # 未装配模型时保持纯规则（单测默认）；Gateway 应注入 DeepSeekEntryPathModel。
        return rule_path
    evidence = build_entry_path_evidence(
        content=content,
        materials=materials,
        workspace=workspace,
        rule_path=rule_path,
    )
    try:
        proposal = await asyncio.wait_for(
            model.propose(evidence),
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - 入口选择必须 fail-closed
        logger.warning(
            "入口路径 LLM 选择失败，回退规则路径=%s error_type=%s",
            rule_path,
            type(exc).__name__,
        )
        return rule_path
    sanitized = sanitize_entry_path_proposal(
        proposal.entry_path,
        content=content,
        workspace=workspace,
        is_complete_script=is_complete_script,
        has_generatable_script=has_generatable_script,
    )
    if sanitized != rule_path:
        logger.info(
            "入口路径 LLM 覆盖规则：%s → %s reason=%s",
            rule_path,
            sanitized,
            (proposal.reason or "")[:120],
        )
    return sanitized
