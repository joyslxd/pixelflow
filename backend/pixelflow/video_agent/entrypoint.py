"""统一视频输入进入 VideoAgent 的最小 P0 入口。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pixelflow.agent_runtime.persistence.repositories import (
    AgentRuntimeRecordConflictError,
    AgentRuntimeRepository,
)
from pixelflow.video_agent.contracts import (
    AgentPlan,
    AgentPlanStatus,
    VideoWorkspace,
)
from pixelflow.video_agent.executor.events import (
    build_plan_created_event,
    build_plan_updated_event,
)
from pixelflow.video_agent.native_invoke import NativeVideoAgentInvoker
from pixelflow.video_agent.production_fields import (
    looks_like_production_field_reply,
    normalize_user_text,
)
from pixelflow.video_agent.workspace.ids import video_workspace_id_for_conversation
from pixelflow.video_agent.workspace.repository import VideoAgentRepository

logger = logging.getLogger(__name__)

# 思考流耗时期间确认脚本/旧 Plan 执行器可能已 bump revision；冲突时重读再写。
_WORKSPACE_PATCH_MAX_ATTEMPTS = 3


def _stable_id(prefix: str, *parts: str) -> str:
    value = ":".join(("pixelflow-video-agent", prefix, *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, value).hex}"


def video_agent_plan_id(conversation_id: str, turn_id: str) -> str:
    """由 conversation + turn 派生稳定 plan_id，供 Runtime 收尾/接力查找。"""

    return _stable_id("video_plan", conversation_id, turn_id)


def video_workspace_id(conversation_id: str) -> str:
    """Entrypoint 与 legacy upgrade 共用的会话 Workspace 身份。"""

    return video_workspace_id_for_conversation(conversation_id)


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
        "剧本",
        "分镜",
        "镜头",
        "成片",
        "episode",
        "/episode",
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
    if _is_merge_videos_intent(text):
        return False
    if looks_like_complete_shooting_script(text):
        return False
    # 补字段判定必须带 workspace；无 payload 时 len<=48 会把「合并视频吧」等短句误判成补字段。
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


def _merge_turn_with_workspace_context(
    text: str,
    workspace: VideoWorkspace,
    *,
    materials: Sequence[Mapping[str, Any]] = (),
) -> str:
    """补字段 / 改创意短跟进：先拼回 workspace 脚本或 brief，再思考与规划。

    历史消息合并（service 层）可能已带【本轮指令】；此处再兜底用权威 workspace。
    禁止把「本轮原文」与刚写入的同一段 brief 自拼成双份。
    """

    del materials  # 签名保留，便于调用方与材料规则对齐；合并判定不依赖素材。
    raw = text.strip()
    instruction = (
        normalize_user_text(raw)
        if len(raw) <= 240 and not looks_like_complete_shooting_script(raw)
        else raw
    )
    if not instruction or "【本轮指令】" in instruction:
        return instruction
    if _is_continue_video_generation(instruction):
        return instruction
    # 合并成片短令：禁止拼回整篇脚本，否则 Plan 标题变成镜头正文、模型上下文被撑爆。
    if _is_merge_videos_intent(instruction):
        return instruction
    # 重新生成分镜包必须保持短指令入模；拼回整篇脚本会撑爆 checkpointer 并空转。
    if _is_reprepare_scene_packages(instruction) or _is_confirm_script_plan(instruction):
        return instruction
    if looks_like_complete_shooting_script(instruction):
        return instruction
    field_reply = looks_like_production_field_reply(
        instruction,
        workspace_payload=workspace.payload,
    )
    if len(instruction) >= 400 and not field_reply:
        return instruction

    needs_merge = (
        field_reply
        or _looks_like_creative_followup(instruction)
        or is_short_video_followup_instruction(instruction)
    )
    if not needs_merge:
        return instruction

    base = _workspace_script_markdown(workspace) or _workspace_creative_brief(workspace)
    prior = (base or "").strip()
    if not prior or prior == instruction:
        return instruction
    return f"{prior}\n\n【本轮指令】{instruction}"


def _is_confirm_script_plan(content: str) -> bool:
    """用户明确确认脚本方案（资产包前门禁）。"""

    lowered = content.strip().casefold()
    if not lowered:
        return False
    # 「确认并生成分镜视频」是 generate_scenes，禁止被「确认并生成视频」子串误判为确认脚本。
    if "确认并生成分镜视频" in lowered or "确认并生成分镜" in lowered:
        return False
    if "重新生成已修改的分镜视频" in lowered or "继续生成失败的分镜视频" in lowered:
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
        "用户已确认当前脚本方案",
    )
    return any(marker.casefold() in lowered for marker in markers)


def _is_reprepare_scene_packages(content: str) -> bool:
    """用户明确要求重新生成分镜/场景/资产包（勿与「重新生成分镜视频」混淆）。"""

    text = re.sub(r"\s+", "", content.strip())
    if not text or len(text) > 80:
        return False
    if "分镜视频" in text or "场景视频" in text:
        return False
    markers = (
        "重新生成视频分镜包",
        "重新生成分镜包",
        "重新生成视频场景包",
        "重新生成场景包",
        "重新生成资产包",
        "重新生成视频资产包",
        "重拆分镜包",
        "重拆场景包",
    )
    return any(marker in text for marker in markers)


def is_confirm_script_plan(content: str) -> bool:
    """公开别名：供 native bootstrap 等模块判断确认意图。"""

    return _is_confirm_script_plan(content)


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
        # 工作台分镜视频短令：禁止拼回整篇脚本，交 generate_scenes bootstrap。
        "确认并生成分镜视频",
        "重新生成已修改的分镜视频",
        "继续生成失败的分镜视频",
    )
    return any(marker in lowered for marker in markers)


def _is_merge_videos_intent(content: str) -> bool:
    """「合并视频 / 合成成片」短令：禁止拼回脚本，交由 ReAct compose_or_export_video。"""

    text = re.sub(r"\s+", "", content.strip())
    if not text or len(text) > 80:
        return False
    if any(
        token in text
        for token in (
            "合并视频",
            "合并成片",
            "合成视频",
            "合成成片",
            "合并分镜视频",
            "合成分镜视频",
            "导出成片",
            "导出mp4",
            "导出MP4",
        )
    ):
        return True
    if text in {
        "合并",
        "合并吧",
        "合成",
        "合成吧",
        "开始合并",
        "开始合成",
        "帮我合并",
        "请合并",
    }:
        return True
    return bool(
        re.match(
            r"^(请)?(帮我)?(把)?(分镜)?(视频)?(合并|合成)(成片|成视频|视频|一下|吧)?$",
            text,
        )
    )


def _public_goal_seed(text: str) -> str:
    """Plan 标题优先用【本轮指令】短句，避免拼回脚本后标题变成镜头正文。"""

    raw = (text or "").strip()
    if not raw:
        return ""
    if "【本轮指令】" in raw:
        followup = raw.rsplit("【本轮指令】", 1)[-1].strip()
        if followup:
            return followup
    return raw


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
        "继续资产",
        "开始资产",
        "生成资产",
        "开始生图",
        "继续生图",
        "生成参考图",
        "生成成片",
        "继续成片",
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

    raw = current.strip()
    if not raw:
        return raw
    if "【本轮指令】" in raw:
        return raw
    if looks_like_complete_shooting_script(raw) or len(raw) >= 400:
        return raw
    # 短跟进才归一全角冒号（如 9：16 → 9:16），不改写长脚本正文。
    text = normalize_user_text(raw)
    # 「确认脚本并生成资产包」等成片确认短令不得拼回整篇脚本，否则 Intake 会误判 clarify。
    # 「重新生成分镜包」同理：拼回成稿会让原生 Agent 重复吃长文，易空转「已完成本轮处理」。
    if (
        _is_continue_video_generation(text)
        or _is_confirm_script_plan(text)
        or _is_reprepare_scene_packages(text)
        or _is_merge_videos_intent(text)
    ):
        return text
    if (
        not is_short_video_followup_instruction(text)
        and not _looks_like_creative_followup(text)
        and not looks_like_production_field_reply(text)
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


@dataclass(frozen=True)
class VideoAgentSubmission:
    workspace: VideoWorkspace
    plan: AgentPlan | None = None


class VideoAgentEntrypoint:
    """Thin Turn 入口：只登记 Workspace / 观察 Plan，由原生 Agent 选 Tool。"""

    def __init__(
        self,
        *,
        runtime_repository: AgentRuntimeRepository,
        video_repository: VideoAgentRepository,
        native_invoker: NativeVideoAgentInvoker,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._video_repository = video_repository
        self._native_invoker = native_invoker
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _apply_workspace_patch_resilient(
        self,
        *,
        owner: str,
        workspace: VideoWorkspace,
        patch: Mapping[str, Any],
        now: datetime,
    ) -> VideoWorkspace:
        """按最新 revision 写入 workspace；冲突则重读后重试。"""

        current = workspace
        last_error: AgentRuntimeRecordConflictError | None = None
        for attempt in range(_WORKSPACE_PATCH_MAX_ATTEMPTS):
            try:
                return await self._video_repository.apply_workspace_patch(
                    owner,
                    current.workspace_id,
                    dict(patch),
                    expected_revision=current.revision,
                    now=now,
                )
            except AgentRuntimeRecordConflictError as exc:
                last_error = exc
                logger.warning(
                    "workspace patch 冲突，重读后重试 workspace_id=%s attempt=%s",
                    current.workspace_id,
                    attempt + 1,
                )
                refreshed = await self._video_repository.get_workspace(
                    owner,
                    current.workspace_id,
                )
                if refreshed is None:
                    raise
                current = refreshed
        assert last_error is not None
        raise last_error

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
        # 全角标点归一只作用于短补丁，避免改写完整脚本正文里的「：」。
        raw_content = content.strip()
        text = (
            normalize_user_text(raw_content)
            if len(raw_content) <= 240 and not looks_like_complete_shooting_script(raw_content)
            else raw_content
        )
        if not owner or not conversation_id.strip() or not turn_id.strip() or not text:
            raise ValueError("VideoAgent 输入必须包含用户、对话、Turn 和内容")
        occurred_at = self._clock()
        workspace_id = video_workspace_id_for_conversation(conversation_id)
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

        return await self._submit_turn_native(
            owner=owner,
            conversation_id=conversation_id,
            turn_id=turn_id,
            text=text,
            raw_content=raw_content,
            artifact_refs=artifact_refs,
            materials=materials,
            workspace_id=workspace_id,
            plan_id=plan_id,
            occurred_at=occurred_at,
        )

    async def _submit_turn_native(
        self,
        *,
        owner: str,
        conversation_id: str,
        turn_id: str,
        text: str,
        raw_content: str,
        artifact_refs: tuple[str, ...],
        materials: Sequence[Mapping[str, Any]] | None,
        workspace_id: str,
        plan_id: str,
        occurred_at: datetime,
    ) -> VideoAgentSubmission:
        """Thin Entrypoint：只登记 Workspace 与观察 Plan，交由原生 Agent 选 Tool。"""

        del raw_content  # 已在 submit_turn 归一化为 text
        safe_materials = _safe_materials(materials)
        product_info = _product_info_from_materials(safe_materials)
        workspace = await self._video_repository.create_workspace(
            owner,
            VideoWorkspace(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                payload={
                    "latest_input": text,
                    "artifact_refs": list(artifact_refs),
                    "materials": safe_materials,
                    "product_info": product_info,
                    "active_turn_id": turn_id,
                    "native_agent": True,
                },
                created_at=occurred_at,
                updated_at=occurred_at,
            ),
        )
        workspace = await self._apply_workspace_patch_resilient(
            owner=owner,
            workspace=workspace,
            patch={
                "latest_input": text,
                "artifact_refs": list(artifact_refs),
                "materials": safe_materials,
                "product_info": product_info,
                "active_turn_id": turn_id,
                "native_agent": True,
            },
            now=occurred_at,
        )
        merged = _merge_turn_with_workspace_context(
            text,
            workspace,
            materials=safe_materials,
        )
        if merged != text:
            workspace = await self._apply_workspace_patch_resilient(
                owner=owner,
                workspace=workspace,
                patch={"latest_input": merged},
                now=occurred_at,
            )
            text = merged

        compact = " ".join(_public_goal_seed(text).split())
        public_goal = (
            f"处理视频请求：{compact[:37]}..."
            if len(compact) > 40
            else f"处理视频请求：{compact}"
            if compact
            else "处理视频请求"
        )
        plan = AgentPlan(
            plan_id=plan_id,
            workspace_id=workspace.workspace_id,
            conversation_id=conversation_id,
            status=AgentPlanStatus.RUNNING,
            public_goal=public_goal[:2_000],
            steps=(),
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        return await self._persist_and_publish_plan(
            owner=owner,
            conversation_id=conversation_id,
            turn_id=turn_id,
            workspace=workspace,
            plan=plan,
            occurred_at=occurred_at,
        )

    async def _persist_and_publish_plan(
        self,
        *,
        owner: str,
        conversation_id: str,
        turn_id: str,
        workspace: VideoWorkspace,
        plan: AgentPlan,
        occurred_at: datetime,
    ) -> VideoAgentSubmission:
        plan = await self._video_repository.save_plan(
            owner,
            plan,
            list(plan.steps),
        )
        events = await self._runtime_repository.list_events(owner, conversation_id)
        created = any(
            event.type.value == "agent.plan.created"
            and event.payload.get("plan_id") == plan.plan_id
            for event in events
        )
        if not created:
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
            events = await self._runtime_repository.list_events(owner, conversation_id)
        update_id = _stable_id("video_event", plan.plan_id, "updated")
        await self._runtime_repository.create_event(
            owner,
            build_plan_updated_event(
                event_id=update_id,
                cursor=_stable_id("video_cursor", update_id),
                sequence=1 if not events else events[-1].sequence + 1,
                conversation_id=conversation_id,
                run_id=turn_id,
                occurred_at=self._clock(),
                plan=plan,
            ),
        )
        return VideoAgentSubmission(workspace=workspace, plan=plan)
