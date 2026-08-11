"""统一视频输入进入 VideoAgent 的最小 P0 入口。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.persistence.repositories import AgentRuntimeRepository
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    AgentPlanStep,
    PlanStepStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.executor.events import build_plan_created_event
from pixelflow.video_agent.planner import (
    VideoAgentPlanner,
    VideoAgentPlanningContext,
    VideoAgentPlanningError,
)
from pixelflow.video_agent.planner.entry_path import (
    EntryPathModel,
    select_entry_path_with_llm,
)
from pixelflow.video_agent.planner.workspace_digest import (
    blocking_confirmation_from_plan,
    build_workspace_digest,
    summarize_operations,
)
from pixelflow.video_agent.workspace.repository import VideoAgentRepository

logger = logging.getLogger(__name__)

# V2.1：Planner 主路径超时后降级为 inspect，避免 turns/start 无限挂起。
# DeepSeek 结构化规划单次常需 5–12s，且最多 3 次修复重试，10s 极易误降级。
_DEFAULT_PLANNING_TIMEOUT_SEC = 45.0


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join(("pixelflow-video-agent", prefix, *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


def video_agent_plan_id(conversation_id: str, turn_id: str) -> str:
    """由 conversation + turn 派生稳定 plan_id，供 Runtime 收尾/接力查找。"""

    return _stable_id("video_plan", conversation_id, turn_id)


def _safe_materials(
    materials: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not materials:
        return []
    safe: list[dict[str, Any]] = []
    for item in materials:
        if not isinstance(item, Mapping):
            continue
        safe.append({str(key): value for key, value in item.items()})
    return safe


def _product_info_from_materials(
    materials: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    images: list[dict[str, str]] = []
    for item in materials:
        mime = str(item.get("mimeType") or item.get("mime_type") or "")
        kind = str(item.get("type") or "")
        is_image = kind == "image" or mime.startswith("image/")
        if not is_image:
            continue
        url = str(item.get("url") or item.get("path") or "").strip()
        if not url:
            continue
        name = str(item.get("name") or item.get("filename") or "").strip()
        entry = {"url": url}
        if name:
            entry["name"] = name
        images.append(entry)
    if not images:
        return {}
    product: dict[str, Any] = {"images": images}
    first_name = images[0].get("name")
    if first_name:
        stem = PurePosixPath(first_name).stem.strip()
        if stem:
            product["name"] = stem
            product["product_name"] = stem
    return product


# A 全创作 / B 成稿润色 / C 直接成片 / inspect 资料探查
ScriptEntryPath = Literal["create", "polish", "continue", "inspect"]


def _should_seed_script_draft(
    content: str,
    materials: Sequence[Mapping[str, Any]],
) -> bool:
    if materials:
        return True
    lowered = content.casefold()
    markers = (
        "视频",
        "带货",
        "广告",
        "脚本",
        "分镜",
        "成片",
        "video",
        "script",
        "tvc",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_creative_followup(content: str) -> bool:
    """改创意 / 补镜头 / 加转折：本身未必含「视频」，但应继续 Path A。"""

    text = content.strip()
    if not text:
        return False
    if _is_continue_video_generation(text) or _is_confirm_script_plan(text):
        return False
    if looks_like_complete_shooting_script(text):
        return False
    lowered = text.casefold()
    markers = (
        "改成",
        "换成",
        "加上",
        "加个",
        "增加",
        "补上",
        "补一个",
        "转折",
        "戏剧",
        "镜头",
        "变成",
        "不要",
        "删掉",
        "调整",
        "重写",
        "重新",
        "更有意思",
        "冲突",
        "反转",
        "拍立得",
        "相纸",
        "碰杯",
        "蓝妹",
        "多年以前",
        "多年以后",
    )
    if any(marker in lowered for marker in markers):
        return True
    narrative = ("故事", "朋友", "人物", "场景", "旁白", "画面", "以前", "现在")
    return len(text) >= 40 and any(token in text for token in narrative)


def _pipeline_stage_content(workspace: VideoWorkspace, stage: str) -> str:
    pipeline = workspace.payload.get("script_pipeline")
    if not isinstance(pipeline, Mapping):
        return ""
    item = pipeline.get(stage)
    if not isinstance(item, Mapping):
        return ""
    content = item.get("content")
    return content.strip() if isinstance(content, str) else ""


def _workspace_creative_brief(workspace: VideoWorkspace) -> str:
    """会话里已有的选题/故事 brief，供改创意跟进合并。

    优先用户 latest_input（原始主题），其次 /start 产物。
    """

    latest = workspace.payload.get("latest_input")
    if isinstance(latest, str) and latest.strip():
        brief = latest.strip()
        if _should_seed_script_draft(brief, ()) or len(brief) >= 40:
            return brief
    start = _pipeline_stage_content(workspace, "start")
    if start:
        return start
    return ""


def _merge_creative_revision_with_brief(current: str, brief: str) -> str:
    text = current.strip()
    prior = brief.strip()
    if not text or not prior:
        return text
    if text == prior or prior in text or "【本轮指令】" in text:
        return text
    return f"{prior}\n\n【本轮指令】{text}"


def _is_confirm_script_plan(content: str) -> bool:
    """用户明确确认脚本方案（资产包前门禁）。"""

    lowered = content.strip().casefold()
    if not lowered:
        return False
    markers = (
        "确认脚本",
        "确认方案",
        "确认plan",
        "确认执行方案",
        "确认脚本方案",
        "确认脚本plan",
        "确认并生成视频",
        "确认并生成资产包",
        "同意脚本",
        "同意方案",
    )
    return any(marker.casefold() in lowered for marker in markers)


def _is_continue_video_generation(content: str) -> bool:
    """脚本就绪后的成片短指令。

    刻意不含裸「生成视频」，避免「根据这个脚本生成视频」误走 C 并跳过确认。
    """

    lowered = content.strip().casefold()
    if not lowered:
        return False
    if _is_confirm_script_plan(lowered):
        return True
    markers = (
        "继续生成视频",
        "继续做视频",
        "继续出片",
        "继续生成资产包",
        "继续准备资产包",
        "生成资产包",
        "准备资产包",
        "视频资产包",
        "生成场景包",
        "准备场景包",
        "继续生成场景包",
    )
    return any(marker in lowered for marker in markers)


def _extract_character_section(text: str) -> str:
    patterns = (
        r"#{1,3}\s*[0-9一二三四五六七八九十.、)）]*\s*角色设定[\s\S]*?(?=#{1,3}\s*[0-9一二三四五六七八九十.、)）]*\s*(?:场景设定|道具|大纲|完整镜头|合规)|$)",
        r"#{1,3}\s*角色\s*[/／]\s*场景\s*[/／]\s*道具[^\n]*[\s\S]*?(?=#{1,3}\s*(?:大纲|完整镜头|合规|三幕)|$)",
        r"#{1,3}\s*[^\n]*/characters[^\n]*[\s\S]*?(?=#{1,3}\s*[^\n]*/(?:outline|episode|review|compliance|export)\b|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group(0).strip():
            return match.group(0)
    return ""


def _expected_character_count(text: str) -> int:
    labels = {
        re.sub(r"\s+", "", match.group(0))
        for match in re.finditer(r"(?:男|女)\s*[1234一二三四]", text)
    }
    if len(labels) >= 2:
        return len(labels)
    if re.search(r"四个朋友|四位朋友|四人组|四位老友|四人聚会", text):
        return 4
    if re.search(r"三个朋友|三位朋友|三人组", text):
        return 3
    if re.search(r"两位朋友|两个朋友|二人组", text):
        return 2
    return 0


def _character_profile_count(text: str) -> int:
    section = text.strip()
    if not section:
        return 0
    names: set[str] = set()

    def collect(raw: str) -> None:
        name = re.sub(r"[*_#`]", "", raw).strip()
        if not name or len(name) > 24:
            return
        if re.match(r"^(角色设定|场景设定|道具|视觉形象|身份|性格|金句|核心标签|角色关系|角色档案)", name):
            return
        names.add(name.split("（")[0].split("(")[0].strip())

    for match in re.finditer(r"^#{2,4}\s+(.+)$", section, flags=re.MULTILINE):
        collect(re.split(r"[（(：:\-—|]", match.group(1), maxsplit=1)[0])
    for match in re.finditer(
        r"^[-*]\s+\*{0,2}([^:*\n]{1,24})\*{0,2}\s*[:：]",
        section,
        flags=re.MULTILINE,
    ):
        collect(match.group(1))
    for match in re.finditer(r"\*\*([^*]{1,24})\*\*", section):
        collect(re.split(r"[（(：:\-—|]", match.group(1), maxsplit=1)[0])
    for match in re.finditer(
        r"(?:主角|配角|人物|角色|男主|女主|男\s*[1234一二三四]|女\s*[1234一二三四])[：:\s]*([^\s，,；;（(/]{1,12})",
        section,
    ):
        collect(match.group(1))
    for match in re.finditer(
        r"([\u4e00-\u9fffA-Za-z]{1,12})\s*[（(]\s*(?:男|女)\s*[1234一二三四]",
        section,
    ):
        collect(match.group(1))
    return len(names)


def _pipeline_stage_content(workspace: VideoWorkspace, stage: str) -> str:
    pipeline = workspace.payload.get("script_pipeline")
    if not isinstance(pipeline, dict):
        return ""
    item = pipeline.get(stage)
    if isinstance(item, dict) and isinstance(item.get("content"), str):
        return str(item["content"]).strip()
    return ""


def _workspace_readiness_corpus(workspace: VideoWorkspace, fallback: str = "") -> str:
    parts: list[str] = []
    for stage in ("characters", "export", "episode"):
        content = _pipeline_stage_content(workspace, stage)
        if content:
            parts.append(content)
    script_md = _workspace_script_markdown(workspace)
    if script_md:
        parts.append(script_md)
    if fallback.strip():
        parts.append(fallback.strip())
    # 保序去重
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            ordered.append(part)
    return "\n\n".join(ordered)


def analyze_script_character_readiness(
    content: str,
    *,
    workspace: VideoWorkspace | None = None,
) -> dict[str, Any]:
    """启发式：多人戏是否具备可生成资产的角色设定。"""

    text = (
        _workspace_readiness_corpus(workspace, content)
        if workspace is not None
        else content.strip()
    )
    missing: list[str] = []
    if not text:
        return {
            "expected_count": 0,
            "profile_count": 0,
            "has_character_section": False,
            "multi_person_cue": False,
            "ready": False,
            "missing_hints": ["脚本为空，请先生成或粘贴完整脚本"],
        }
    characters_stage = (
        _pipeline_stage_content(workspace, "characters") if workspace is not None else ""
    )
    section = _extract_character_section(text) or characters_stage
    has_section = bool(section.strip()) or bool(
        re.search(r"角色设定|角色\s*[/／]\s*场景|/characters\b", text, flags=re.I)
    )
    has_scene = bool(re.search(r"场景设定|/characters\b", text, flags=re.I))
    has_props = bool(re.search(r"道具(?:与产品)?设定|道具设定", text))
    label_source = section or text
    expected = _expected_character_count(label_source)
    multi_person = expected >= 2 or bool(
        re.search(r"多人|群戏|好友们|朋友们|同学聚会|老友局", section or text)
    )
    profiles = _character_profile_count(section)
    if (
        characters_stage
        and has_section
        and has_scene
        and has_props
        and (profiles >= 1 or re.search(r"视觉形象|身份|核心标签", characters_stage))
    ):
        return {
            "expected_count": max(expected, profiles),
            "profile_count": max(profiles, 1),
            "has_character_section": True,
            "multi_person_cue": multi_person,
            "ready": True,
            "missing_hints": [],
        }
    if multi_person and not has_section:
        missing.append("缺少「角色设定」章节，需补齐每位出镜人物的视觉形象与身份")
    if multi_person and expected >= 2 and profiles > 0 and profiles < expected:
        missing.append(
            f"剧本像是 {expected} 人戏，但角色设定仅识别到 {profiles} 人，请补充全部角色"
        )
    if multi_person and profiles < 2 and not characters_stage:
        missing.append("多人出镜时至少需要 2 个可区分的角色设定，否则资产包容易塌成单人")
    return {
        "expected_count": expected,
        "profile_count": profiles,
        "has_character_section": has_section,
        "multi_person_cue": multi_person,
        "ready": len(missing) == 0,
        "missing_hints": missing,
    }


def script_needs_full_character_plan(
    content: str,
    *,
    workspace: VideoWorkspace | None = None,
) -> bool:
    readiness = analyze_script_character_readiness(content, workspace=workspace)
    return bool(readiness["multi_person_cue"] and not readiness["ready"])


def _has_explicit_polish_intent(content: str) -> bool:
    """用户明确要求对已有成稿做自检/合规/导出。"""

    lowered = content.strip().casefold()
    if not lowered:
        return False
    markers = (
        "这是完整脚本",
        "这是完整成稿",
        "已有完整脚本",
        "已有完整成稿",
        "已有脚本",
        "完整脚本",
        "完整成稿",
        "成稿润色",
        "请自检",
        "五维自检",
        "自检后导出",
        "合规后导出",
        "合规检查后导出",
        "自检并导出",
        "润色脚本",
        "不要重写脚本",
        "不要从选题重做",
        "跳过选题",
        "直接自检",
        "直接合规",
        "polish script",
        "complete script",
    )
    return any(marker.casefold() in lowered for marker in markers)


def _structural_complete_script_score(content: str) -> int:
    """启发式：时间轴 + 镜头语言 + 篇幅，分数越高越像可拍成稿。"""

    text = content.strip()
    if len(text) < 160:
        return 0
    score = 0
    if len(text) >= 220:
        score += 1
    if len(text) >= 500:
        score += 1
    if len(text) >= 1200:
        score += 1
    # 中文分镜里时间码两侧常无英文词界，不用 \b
    timecodes = re.findall(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)", text)
    if len(timecodes) >= 3:
        score += 2
    elif len(timecodes) >= 2:
        score += 1
    shot_markers = (
        "景别",
        "运镜",
        "旁白",
        "台词",
        "镜头",
        "分镜",
        "特写",
        "中景",
        "全景",
        "近景",
        "推镜",
        "拉镜",
        "摇镜",
        "画面",
        "行动引导",
        "cta",
        "shot",
        "close-up",
    )
    lowered = text.casefold()
    hit_count = sum(1 for marker in shot_markers if marker in lowered)
    if hit_count >= 4:
        score += 2
    elif hit_count >= 2:
        score += 1
    # 多镜序号：镜头1 / 镜 2 / Shot 3
    if len(re.findall(r"(?:镜头|镜)\s*\d+|shot\s*\d+", lowered)) >= 3:
        score += 1
    return score


def _is_complete_script_polish(content: str) -> bool:
    """路径 B：明确成稿意图，或结构上足够像完整拍摄脚本。"""

    if _has_explicit_polish_intent(content):
        # 明确意图仍要求有一定正文，避免「请自检」空口短句误入 B
        if len(content.strip()) >= 200:
            return True
        return _structural_complete_script_score(content) >= 2
    return _structural_complete_script_score(content) >= 4


def looks_like_complete_shooting_script(content: str) -> bool:
    """供路由层识别「用户已贴成稿」：避免长分镜因无「生成视频」落入 unknown。"""

    return _is_complete_script_polish(content) or _structural_complete_script_score(content) >= 3


def is_short_video_followup_instruction(content: str) -> bool:
    """澄清/跟进短指令：本身不含故事，需合并上文成稿或 brief。"""

    text = content.strip()
    if not text or len(text) > 48:
        return False
    if looks_like_complete_shooting_script(text):
        return False
    if _is_continue_video_generation(text):
        return True
    lowered = text.casefold()
    markers = (
        "生成视频",
        "创建视频",
        "制作视频",
        "带货视频",
        "生成带货",
        "做视频",
        "出视频",
        "拍视频",
        "广告视频",
        "视频广告",
        "生成广告",
    )
    return any(marker in lowered for marker in markers)


def merge_video_turn_content_with_history(
    current: str,
    prior_user_contents: Sequence[str],
) -> str:
    """短指令 / 改创意跟进进 VideoAgent 时，把最近一条成稿或创作 brief 拼回 latest_input。

    典型坏路径：
    1) 用户先贴完整 /episode，路由 unknown 澄清后只发「生成带货视频」；
    2) 用户先发蓝妹主题，取消创意确认后再发「镜头要加转折」——短跟进不含「视频」关键词。
    """

    text = current.strip()
    if not text:
        return text
    if "【本轮指令】" in text:
        return text
    if looks_like_complete_shooting_script(text) or len(text) >= 400:
        return text
    if (
        not is_short_video_followup_instruction(text)
        and not _looks_like_creative_followup(text)
        and len(text) >= 80
    ):
        return text

    best: str | None = None
    best_score = -1
    for prior in prior_user_contents:
        candidate = str(prior or "").strip()
        if not candidate or candidate == text:
            continue
        score = _structural_complete_script_score(candidate)
        if score >= 3:
            ranked = score * 1000 + min(len(candidate), 5000)
        elif len(candidate) >= 400:
            ranked = 500 + min(len(candidate), 5000)
        elif _should_seed_script_draft(candidate, ()) and len(candidate) >= 24:
            # 模糊主题 brief（含「视频」等）也要能拼回改创意跟进。
            ranked = 100 + min(len(candidate), 5000)
        else:
            continue
        if ranked >= best_score:
            best_score = ranked
            best = candidate
    if best is None:
        return text
    return f"{best}\n\n【本轮指令】{text}"


def _workspace_script_markdown(workspace: VideoWorkspace) -> str:
    script = workspace.payload.get("script")
    if isinstance(script, dict):
        content = script.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    pipeline = workspace.payload.get("script_pipeline")
    if not isinstance(pipeline, dict):
        return ""
    for stage in ("export", "episode", "outline", "characters"):
        item = pipeline.get(stage)
        if (
            isinstance(item, dict)
            and isinstance(item.get("content"), str)
            and str(item["content"]).strip()
        ):
            return str(item["content"]).strip()
    return ""


def _workspace_has_generatable_script(workspace: VideoWorkspace) -> bool:
    return bool(_workspace_script_markdown(workspace))


def _resolve_script_entry_path(
    *,
    content: str,
    materials: Sequence[Mapping[str, Any]],
    workspace: VideoWorkspace,
    continue_generation: bool,
) -> ScriptEntryPath:
    """解析本轮入口路径：C 成片 > B 成稿润色 > A 全创作 > inspect。"""

    workspace_script = _workspace_script_markdown(workspace)
    character_source = workspace_script or content
    if continue_generation:
        # 多人戏角色设定不清：即使要成片也先走全流程补设定。
        if script_needs_full_character_plan(character_source, workspace=workspace):
            return "create"
        # 资产包前门禁：未确认脚本时不走 C。
        if workspace.payload.get("script_plan_confirmed") is True:
            return "continue"
        return "inspect"
    # 用户贴成稿要润色（即使会话里已有旧脚本也不走 C）。
    if _is_complete_script_polish(content):
        if script_needs_full_character_plan(content, workspace=workspace):
            return "create"
        return "polish"
    if _should_seed_script_draft(content, materials):
        return "create"
    # 会话已有选题/故事：改创意、补镜头类跟进继续 Path A，避免落成 inspect。
    if _workspace_creative_brief(workspace) and _looks_like_creative_followup(content):
        return "create"
    return "inspect"


def _user_episode_pipeline_item(content: str) -> dict[str, Any]:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return {
        "stage": "episode",
        "title": "用户成稿 /episode",
        "content": content,
        "artifact_ref": f"artifact:video-script-episode-user-{digest}",
        "change_summary": "载入用户完整脚本作为待审成稿",
        "source": "user_complete_script",
    }


def _looks_like_confirmation_response(content: str) -> bool:
    """用户是否在回应待确认闸门（同意/取消/修改），而非开新编排。"""

    text = content.strip().casefold()
    if not text or len(text) > 80:
        return False
    markers = (
        "同意",
        "确认",
        "取消",
        "拒绝",
        "不同意",
        "换个方向",
        "重新选题",
        "已充值",
        "继续",
    )
    return any(marker.casefold() in text for marker in markers)


def _public_goal(content: str, *, entry_path: ScriptEntryPath = "create") -> str:
    if entry_path == "continue":
        return "准备视频资产包"
    if entry_path == "polish":
        return "成稿自检与导出"
    if entry_path == "create" and script_needs_full_character_plan(content):
        readiness = analyze_script_character_readiness(content)
        hint = readiness["missing_hints"][0] if readiness["missing_hints"] else "角色设定不完整"
        return f"全流程补齐设定（{hint}）"
    compact = " ".join(content.split())
    if len(compact) <= 40:
        return f"处理视频创作请求：{compact}"
    return f"处理视频创作请求：{compact[:37]}..."


@dataclass(frozen=True)
class VideoAgentSubmission:
    workspace: VideoWorkspace
    plan: AgentPlan


class VideoAgentEntrypoint:
    """把一个已登记的用户 Turn 转换为可恢复的 VideoAgent 短计划。

    V2.1：主路径调用 `VideoAgentPlanner.plan_turn()`（最多 3 步）；
    Planner 缺失/超时/失败时仅降级为单步 `inspect_video_workspace`。
    """

    def __init__(
        self,
        *,
        runtime_repository: AgentRuntimeRepository,
        video_repository: VideoAgentRepository,
        planner: VideoAgentPlanner | None = None,
        entry_path_model: EntryPathModel | None = None,
        clock: Callable[[], datetime] | None = None,
        planning_timeout_sec: float = _DEFAULT_PLANNING_TIMEOUT_SEC,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._video_repository = video_repository
        self._planner = planner
        # entry_path 仅用于 workspace 诊断/润色种子，不再展开完整步骤表。
        self._entry_path_model = entry_path_model
        self._clock = clock or (lambda: datetime.now(UTC))
        self._planning_timeout_sec = max(0.1, float(planning_timeout_sec))

    async def submit_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        content: str,
        artifact_refs: tuple[str, ...],
        materials: Sequence[Mapping[str, Any]] | None = None,
    ) -> VideoAgentSubmission:
        owner = user_id.strip()
        text = content.strip()
        if not owner or not conversation_id.strip() or not turn_id.strip() or not text:
            raise ValueError("VideoAgent 输入必须包含用户、对话、Turn 和内容")
        occurred_at = self._clock()
        workspace_id = _stable_id("video_workspace", conversation_id)
        plan_id = video_agent_plan_id(conversation_id, turn_id)
        existing_plan = await self._video_repository.get_plan(owner, plan_id)
        if existing_plan is not None:
            workspace = await self._video_repository.get_workspace(
                owner,
                existing_plan.workspace_id,
            )
            if workspace is None:
                raise ValueError("VideoAgent plan 缺少对应 workspace")
            return VideoAgentSubmission(workspace=workspace, plan=existing_plan)

        safe_materials = _safe_materials(materials)
        product_info = _product_info_from_materials(safe_materials)
        payload = {
            "latest_input": text,
            "artifact_refs": list(artifact_refs),
            "materials": safe_materials,
            "product_info": product_info,
        }
        workspace = await self._video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                payload=payload,
                created_at=occurred_at,
                updated_at=occurred_at,
            ),
        )
        # 改创意跟进：先用工作区已有 brief 拼回，再写入 latest_input，避免只剩镜头补丁。
        prior_brief = _workspace_creative_brief(workspace)
        if prior_brief and (
            _looks_like_creative_followup(text)
            or not _should_seed_script_draft(text, safe_materials)
        ):
            text = _merge_creative_revision_with_brief(text, prior_brief)
        continue_generation = (
            _workspace_has_generatable_script(workspace)
            and _is_continue_video_generation(text)
        )
        if workspace.payload.get("latest_input") != text or (
            safe_materials and workspace.payload.get("materials") != safe_materials
        ):
            # 同会话后续 Turn 复用 workspace，需要写入本轮输入与素材。
            # 「继续生成视频」不得覆盖已有故事/脚本 latest_input。
            merged_product = {
                **(
                    dict(workspace.payload["product_info"])
                    if isinstance(workspace.payload.get("product_info"), dict)
                    else {}
                ),
                **product_info,
            }
            patch: dict[str, Any] = {
                "artifact_refs": list(artifact_refs),
                "materials": safe_materials or list(
                    workspace.payload.get("materials") or []
                ),
                "product_info": merged_product,
                "pending_generation_request": text,
            }
            if not continue_generation:
                patch["latest_input"] = text
            workspace = await self._video_repository.apply_workspace_patch(
                owner,
                workspace.workspace_id,
                patch,
                expected_revision=workspace.revision,
                now=occurred_at,
            )
        # 脚本已就绪后的「继续生成」要按最新 workspace 再判一次。
        continue_generation = (
            _workspace_has_generatable_script(workspace)
            and _is_continue_video_generation(text)
        )
        # 入口路径只写 workspace 诊断/润色种子，不再决定步骤表。
        entry_path = _resolve_script_entry_path(
            content=text,
            materials=safe_materials,
            workspace=workspace,
            continue_generation=continue_generation,
        )
        entry_path = await select_entry_path_with_llm(
            content=text,
            materials=safe_materials,
            workspace=workspace,
            rule_path=entry_path,
            model=self._entry_path_model,
            is_complete_script=_is_complete_script_polish,
            has_generatable_script=_workspace_has_generatable_script,
        )
        if entry_path == "create":
            script_md = _workspace_script_markdown(workspace)
            readiness = analyze_script_character_readiness(script_md or text, workspace=workspace)
            if script_needs_full_character_plan(script_md or text, workspace=workspace):
                story = script_md or str(workspace.payload.get("latest_input") or text)
                hints = "；".join(readiness["missing_hints"][:2]) or "角色设定不完整"
                enriched = (
                    f"{story.strip()}\n\n"
                    f"【本轮指令】当前脚本角色设定不清晰（{hints}）。"
                    "请走全流程补充角色/场景/道具设定，再导出可拍摄终稿；"
                    "不要只保留单一主角。"
                )
                workspace = await self._video_repository.apply_workspace_patch(
                    owner,
                    workspace.workspace_id,
                    {
                        "latest_input": enriched,
                        "script_entry_path": "create",
                        "script_plan_confirmed": False,
                        "character_plan_required": True,
                    },
                    expected_revision=workspace.revision,
                    now=occurred_at,
                )
        if entry_path == "polish":
            # 路径 B：把用户成稿注入为虚拟 episode，供后续 Tool 读取。
            existing_pipeline = workspace.payload.get("script_pipeline")
            pipeline: dict[str, Any] = (
                dict(existing_pipeline) if isinstance(existing_pipeline, dict) else {}
            )
            pipeline["episode"] = _user_episode_pipeline_item(text)
            workspace = await self._video_repository.apply_workspace_patch(
                owner,
                workspace.workspace_id,
                {
                    "script_pipeline": pipeline,
                    "script_entry_path": "polish",
                    "latest_input": text,
                },
                expected_revision=workspace.revision,
                now=occurred_at,
            )
        elif entry_path in ("create", "continue", "inspect"):
            if workspace.payload.get("script_entry_path") != entry_path:
                workspace = await self._video_repository.apply_workspace_patch(
                    owner,
                    workspace.workspace_id,
                    {"script_entry_path": entry_path},
                    expected_revision=workspace.revision,
                    now=occurred_at,
                )

        plan = await self._plan_turn_or_fallback(
            owner=owner,
            conversation_id=conversation_id,
            turn_id=turn_id,
            content=text,
            artifact_refs=artifact_refs,
            materials=safe_materials,
            workspace=workspace,
            plan_id=plan_id,
            occurred_at=occurred_at,
            entry_path=entry_path,
        )
        plan = await self._video_repository.save_plan(
            owner,
            plan,
            list(plan.steps),
        )
        events = await self._runtime_repository.list_events(owner, conversation_id)
        if not any(
            event.type.value == "agent.plan.created"
            and event.payload.get("plan_id") == plan.plan_id
            for event in events
        ):
            event_id = _stable_id("video_event", plan.plan_id, "created")
            await self._runtime_repository.create_event(
                owner,
                build_plan_created_event(
                    event_id=event_id,
                    cursor=_stable_id("video_cursor", event_id),
                    sequence=1 if not events else events[-1].sequence + 1,
                    conversation_id=conversation_id,
                    run_id=turn_id,
                    occurred_at=occurred_at,
                    plan=plan,
                ),
            )
        return VideoAgentSubmission(workspace=workspace, plan=plan)

    async def _plan_turn_or_fallback(
        self,
        *,
        owner: str,
        conversation_id: str,
        turn_id: str,
        content: str,
        artifact_refs: tuple[str, ...],
        materials: list[dict[str, Any]],
        workspace: VideoWorkspace,
        plan_id: str,
        occurred_at: datetime,
        entry_path: ScriptEntryPath,
    ) -> AgentPlan:
        state = await self._video_repository.load_conversation_state(owner, conversation_id)
        latest_plan = state[1] if state is not None else None
        blocking = blocking_confirmation_from_plan(latest_plan)
        if blocking is not None and not _looks_like_confirmation_response(content):
            return self._inspect_fallback_plan(
                conversation_id=conversation_id,
                content=content,
                workspace=workspace,
                plan_id=plan_id,
                occurred_at=occurred_at,
                public_goal="当前有待确认步骤，请先确认或取消后再继续",
            )

        operation_summaries: list[dict[str, Any]] = []
        try:
            operations = await self._runtime_repository.list_operations(owner, conversation_id)
            operation_summaries = summarize_operations(operations)
        except Exception:  # noqa: BLE001 — Operation 读取失败不得阻断规划
            logger.exception("VideoAgent list_operations failed; planning without ops")

        if self._planner is None:
            return self._inspect_fallback_plan(
                conversation_id=conversation_id,
                content=content,
                workspace=workspace,
                plan_id=plan_id,
                occurred_at=occurred_at,
                public_goal=_public_goal(content, entry_path=entry_path),
            )

        context = VideoAgentPlanningContext(
            user_id=owner,
            conversation_id=conversation_id,
            turn_id=turn_id,
            content=content,
            artifact_refs=artifact_refs,
            materials=tuple(materials),
            workspace=workspace,
            workspace_digest=build_workspace_digest(workspace),
            operation_summaries=tuple(operation_summaries),
            blocking_confirmation=blocking,
        )
        try:
            plan = await asyncio.wait_for(
                self._planner.plan_turn(context),
                timeout=self._planning_timeout_sec,
            )
        except TimeoutError:
            logger.warning(
                "VideoAgent planner timed out after %.1fs; falling back to inspect",
                self._planning_timeout_sec,
            )
            return self._inspect_fallback_plan(
                conversation_id=conversation_id,
                content=content,
                workspace=workspace,
                plan_id=plan_id,
                occurred_at=occurred_at,
                public_goal="规划超时，先读取项目资料",
            )
        except VideoAgentPlanningError as exc:
            logger.warning("VideoAgent planner rejected proposal: %s", exc)
            return self._inspect_fallback_plan(
                conversation_id=conversation_id,
                content=content,
                workspace=workspace,
                plan_id=plan_id,
                occurred_at=occurred_at,
                public_goal="规划失败，先读取项目资料",
            )
        except Exception:  # noqa: BLE001
            logger.exception("VideoAgent planner failed; falling back to inspect")
            return self._inspect_fallback_plan(
                conversation_id=conversation_id,
                content=content,
                workspace=workspace,
                plan_id=plan_id,
                occurred_at=occurred_at,
                public_goal="规划异常，先读取项目资料",
            )

        # 保证 plan_id 与 Turn 派生一致（幂等回放依赖）。
        if plan.plan_id != plan_id:
            remapped_steps = tuple(
                step.model_copy(update={"plan_id": plan_id}) for step in plan.steps
            )
            plan = plan.model_copy(update={"plan_id": plan_id, "steps": remapped_steps})
        return plan

    def _inspect_fallback_plan(
        self,
        *,
        conversation_id: str,
        content: str,
        workspace: VideoWorkspace,
        plan_id: str,
        occurred_at: datetime,
        public_goal: str | None = None,
    ) -> AgentPlan:
        """Planner 故障时的最小降级：单步 inspect，禁止展开完整流水线。"""

        return AgentPlan(
            plan_id=plan_id,
            workspace_id=workspace.workspace_id,
            conversation_id=conversation_id,
            status=AgentPlanStatus.PLANNING,
            public_goal=public_goal or _public_goal(content, entry_path="inspect"),
            steps=(
                AgentPlanStep(
                    step_id=_stable_id("video_step", plan_id, "1"),
                    plan_id=plan_id,
                    sequence=1,
                    tool_name="inspect_video_workspace",
                    title="读取项目资料",
                    status=PlanStepStatus.PENDING,
                ),
            ),
            created_at=occurred_at,
            updated_at=occurred_at,
        )

    def _deterministic_plan(
        self,
        *,
        conversation_id: str,
        content: str,
        materials: list[dict[str, Any]],
        workspace: VideoWorkspace,
        plan_id: str,
        occurred_at: datetime,
        entry_path: ScriptEntryPath | None = None,
        continue_generation: bool = False,
    ) -> AgentPlan:
        """DEPRECATED stub：兼容旧调用点，一律降级为 inspect。

        V2.1 主路径已走 ``VideoAgentPlanner.plan_turn``；勿新增对此方法的依赖。
        生产观测稳定后可随批次 E 硬删一并移除。
        """

        del materials, entry_path, continue_generation
        return self._inspect_fallback_plan(
            conversation_id=conversation_id,
            content=content,
            workspace=workspace,
            plan_id=plan_id,
            occurred_at=occurred_at,
        )
