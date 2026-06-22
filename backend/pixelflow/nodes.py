"""PixelFlow 各阶段的 LangGraph node。

每个函数可以类比成 Java/Spring 中一个流程型 Service 方法：入参是整条任务的
``TaskState`` 上下文 DTO，返回值是“本阶段要更新的字段”。LangGraph 会把返回
值合并回状态，并根据 ``graph.py`` 里的条件边决定下一阶段。

本文件只做阶段编排和异常兜底；纯业务规则放在 ``intake/``、``creative/``、
``generate/``、``edit/``、``qc/`` 子包，第三方 API 和本地 I/O 放在
``skills/`` 子包，避免把 Controller、Service、Client 的职责写串。
"""

from __future__ import annotations

import asyncio
import logging
import math

from langgraph.types import interrupt

from pixelflow.creative import brief_generate, validate_and_fix
from pixelflow.edit import build_timeline
from pixelflow.generate import build_segment_prompt, plan_segments
from pixelflow.intake import demand_integrity_check, normalize_video_params, product_info_extract, summarize_storyboards
from pixelflow.qc import qc_check
from pixelflow.skills import get_video_decompose_skill, get_video_edit_skill, get_video_skill
from pixelflow.state import Phase, TaskState

logger = logging.getLogger(__name__)

# 限制 QC -> GENERATE 的重试次数，避免第三方生成持续失败时任务无限循环。
MAX_QC_ATTEMPTS = 2
# 限制 INTAKE 追问次数，避免用户始终没有补齐信息时任务无限循环。
MAX_INTAKE_ROUNDS = 3
# seedance-2.0 单次最长 10s(v2 skill 校验上限)；短分镜按下限生成，>10s 由
# 多段并行 + concat 承接（plan_segments），EDIT 再裁回精确时长。
SEEDANCE_MIN_DURATION = 4
SEEDANCE_MAX_DURATION = 10


async def _parse_reference_videos(reference_videos: list | None, task_id: str | None) -> list[dict]:
    """把待处理参考视频拆解为 storyboard。

    这里调用的是 reference decompose skill，可以理解成第三方 Client。函数会先复制
    每个参考视频条目，再只处理有 URL 且状态不是 ``done``/``failed`` 的条目。
    这样 LangGraph 循环重入、任务恢复、用户修改 Brief 后重新进入 INTAKE 时，
    不会重复拆解已经完成或已经失败的视频。

    第三方失败不会让 INTAKE 崩掉，而是把该参考视频标成 ``failed`` 并写入
    ``error``。后续完整性检查会把它作为非阻塞 warning 暴露给用户。
    """
    refs = [dict(r or {}) for r in reference_videos or []]
    pending = [r for r in refs if r.get("url") and r.get("status") not in ("done", "failed")]
    if not pending:
        return refs
    skill = get_video_decompose_skill()
    for ref in pending:
        result = await skill.decompose_video_to_storyboard(ref["url"])
        if result.ok:
            ref["status"] = "done"
            ref["storyboard"] = result.shots
        else:
            ref["status"] = "failed"
            ref["error"] = result.error
            logger.warning("[pixelflow] reference decompose failed task_id=%s url=%s error=%s", task_id, ref["url"], result.error)
    return refs


async def intake_node(state: TaskState) -> TaskState:
    """采集阶段：抽取商品信息、归一化视频参数、检查需求完整性。

    这个 node 的职责类似“需求入参校验 Service”：

    1. 如果有商品链接且还没有商品名，只抽取一次商品页信息。
    2. 解析参考视频并把可用 storyboard 写回 ``reference_videos``。
    3. 将视频参数补默认值/纠偏到平台支持范围。
    4. 调用完整性检查；缺字段时用 ``interrupt`` 暂停任务，让前端收集用户补充。

    ``interrupt`` 类似“流程挂起等待人工处理”。每次进入本 node 最多追问一轮；
    如果用户仍未补齐，图会按 ``MAX_INTAKE_ROUNDS`` 的预算决定是否继续回到
    INTAKE。商品页抓取失败只记录日志，不中断任务，让用户有机会手动补字段。
    """
    task_id = state.get("task_id")
    product_info = dict(state.get("product_info") or {})
    creative_direction = dict(state.get("creative_direction") or {})
    rounds = state.get("intake_rounds", 0)
    logger.info("[pixelflow] intake task_id=%s round=%d", task_id, rounds)

    # 商品页抽取只做一次：用 product_name 作为“已经抽取过”的轻量标记，避免
    # LangGraph 恢复或循环重入时重复请求外部页面。
    if product_info.get("product_url") and not product_info.get("product_name"):
        try:
            extracted = await product_info_extract(product_info["product_url"], product_info.get("user_note", ""))
            # 用户显式填写的字段优先级更高，不能被爬取结果覆盖。
            product_info = {**extracted.model_dump(), **product_info}
        except Exception:  # noqa: BLE001 - boundary: never crash on a bad link
            logger.exception("[pixelflow] product_info_extract failed task_id=%s", task_id)

    reference_videos = await _parse_reference_videos(state.get("reference_videos"), task_id)

    video_params, _notes = normalize_video_params(state.get("video_params"))
    result = demand_integrity_check(product_info, video_params, creative_direction, reference_videos)

    if not result.is_complete and rounds < MAX_INTAKE_ROUNDS:
        answers = interrupt({"action": "collect_demand", "questions": result.questions(), "check": result.model_dump()})
        if isinstance(answers, dict):
            product_info.update(answers.get("product_info") or {})
            creative_direction.update(answers.get("creative_direction") or {})
            video_params, _notes = normalize_video_params({**video_params, **(answers.get("video_params") or {})})
            result = demand_integrity_check(product_info, video_params, creative_direction, reference_videos)

    next_phase = Phase.CREATIVE if result.is_complete else Phase.INTAKE
    return {
        "phase": next_phase.value,
        "product_info": product_info,
        "video_params": video_params,
        "creative_direction": creative_direction,
        "reference_videos": reference_videos,
        "intake_check": result.model_dump(),
        "demand_complete": result.is_complete,
        "intake_rounds": rounds + 1,
    }


async def creative_node(state: TaskState) -> TaskState:
    """策划阶段：生成 Brief，并执行硬约束校验。

    这一步可以类比成“调用大模型生成业务 DTO，然后再用本地规则校验 DTO”：

    1. ``brief_generate`` 使用 LLM 产出分镜 Brief。
    2. ``validate_and_fix`` 是纯逻辑校验器，按 PRD §9.5 自动修复确定性问题。
    3. 如果仍有 ``warn`` 级问题，``brief_valid`` 会置为 False，前端 Brief 审核
       卡片可以提示用户人工确认。

    如果 LLM 或配置不可用，node 不直接抛出导致整条图崩溃，而是返回空 Brief 和
    错误信息，由人工确认阶段承接。
    """
    task_id = state.get("task_id")
    logger.info("[pixelflow] creative task_id=%s", task_id)

    product_info = state.get("product_info") or {}
    vp = state.get("video_params") or {}
    video_params = {
        "platform": vp.get("platform", "douyin"),
        "duration_sec": vp.get("video_duration_sec", 30),
        "ratio": vp.get("ratio", "9:16"),
        "size": vp.get("size", "1080x1920"),
    }
    cd = state.get("creative_direction") or {}
    direction = cd if isinstance(cd, str) else "；".join(f"{k}: {v}" for k, v in cd.items() if v)

    # 参考视频分析是纯逻辑摘要：无参考→original，单参考→reference（结构复刻），
    # 多参考→attribution（归因融合）。这个模式会进入 Brief prompt，影响分镜策略。
    reference_analysis = summarize_storyboards(state.get("reference_videos"))
    video_count = (reference_analysis or {}).get("video_count", 0)
    creative_mode = "original" if video_count == 0 else ("reference" if video_count == 1 else "attribution")

    try:
        brief = await brief_generate(
            product_info=product_info,
            video_params=video_params,
            creative_direction=direction,
            reference_analysis=reference_analysis,
            creative_mode=creative_mode,
        )
    except Exception as exc:  # noqa: BLE001 - boundary: never crash the CREATIVE phase
        logger.exception("[pixelflow] brief_generate failed task_id=%s", task_id)
        return {"phase": Phase.BRIEF_REVIEW.value, "brief": {}, "brief_valid": False, "error": str(exc)}

    fixed, issues = validate_and_fix(brief, product_info)
    brief_valid = not any(i["level"] == "warn" for i in issues)
    logger.info("[pixelflow] creative task_id=%s shots=%d issues=%d valid=%s", task_id, len(fixed.shots), len(issues), brief_valid)
    return {
        "phase": Phase.BRIEF_REVIEW.value,
        "brief": fixed.model_dump(),
        "brief_valid": brief_valid,
        "brief_issues": issues,
    }


async def brief_review_node(state: TaskState) -> TaskState:
    """Brief 人工确认阶段：暂停任务，等待用户批准或退回修改。

    ``interrupt`` 会把 LangGraph run 挂起，前端通过 Brief 确认接口恢复它。
    期望恢复 payload 为 ``{"approved": bool}``。如果 payload 缺失或格式不对，
    默认视为未批准，避免没有明确用户确认就进入视频生成。
    """
    decision = interrupt({"brief": state.get("brief", {}), "action": "confirm_brief"})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.GENERATE if approved else Phase.CREATIVE
    return {"phase": next_phase.value, "brief_approved": approved}


async def segment_review_node(state: TaskState) -> TaskState:
    """Pause after video segments are generated so the user can approve them."""
    decision = interrupt({"action": "confirm_segments", "generated_assets": state.get("generated_assets", [])})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.EDIT if approved else Phase.GENERATE
    return {"phase": next_phase.value, "segments_approved": approved}


async def edit_review_node(state: TaskState) -> TaskState:
    """Pause after editing/draft generation so the user can approve the edit."""
    decision = interrupt(
        {
            "action": "confirm_edit",
            "timeline": state.get("timeline", {}),
            "draft_path": state.get("draft_path", ""),
            "final_video_url": state.get("final_video_url", ""),
        }
    )
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    next_phase = Phase.QC if approved else Phase.EDIT
    return {"phase": next_phase.value, "edit_approved": approved}


async def qc_review_node(state: TaskState) -> TaskState:
    """Pause after QC so the user decides whether to accept or regenerate."""
    decision = interrupt({"action": "confirm_qc", "qc_report": state.get("qc_report", {})})
    approved = bool(decision.get("approved", False)) if isinstance(decision, dict) else False
    if approved:
        next_phase = Phase.DONE
    elif state.get("qc_attempts", 0) >= MAX_QC_ATTEMPTS:
        next_phase = Phase.DONE
    else:
        next_phase = Phase.GENERATE
    return {"phase": next_phase.value, "qc_approved": approved}


async def _generate_segment(skill, segment: dict, *, image_url: str, global_visual: dict, ratio: str) -> dict:
    """生成一个 segment 视频片段。

    segment 是若干连续 shot 的组合。Seedance v2 skill 当前校验单次时长范围为
    4 到 10 秒，所以这里先向上取整，再夹到 ``SEEDANCE_MIN_DURATION`` 和
    ``SEEDANCE_MAX_DURATION`` 之间，保证生成片段合法且足够长，后续 EDIT 阶段
    可以再裁回 Brief 里的精确时长。

    返回值是统一资产记录，使用 ``segment_index`` 作为主索引，方便网关同步任务资产。
    """
    gen_duration = max(SEEDANCE_MIN_DURATION, min(SEEDANCE_MAX_DURATION, math.ceil(segment["duration"])))
    result = await skill.image_to_video(
        image_url=image_url,
        prompt=build_segment_prompt(segment["shots"], global_visual),
        duration=gen_duration,
        ratio=ratio,
    )
    return {
        "segment_index": segment["index"],
        "shot_indices": segment["shot_indices"],
        "duration": segment["duration"],
        "ok": result.ok,
        "url": result.url,
        "task_id": result.task_id,
        "error": result.error,
    }


async def generate_node(state: TaskState) -> TaskState:
    """生成阶段：按 segment 调用视频生成 skill。

    Brief 中的 shots 会先通过 ``plan_segments`` 合并成尽量少的、每段不超过
    ``SEEDANCE_MAX_DURATION`` 的 segment。每个 segment 会融合成一个多场景 prompt，
    再并行调用第三方视频生成能力。商品主图 ``main_image_url`` 是每段生成的视觉锚点。

    空 Brief 或缺少主图都不会抛异常，而是写入结构化错误，方便任务 API 和前端展示。
    单个第三方调用失败也会由 skill 归一化为 ``ok=false`` 的资产记录。
    """
    task_id = state.get("task_id")
    brief = state.get("brief") or {}
    product_info = state.get("product_info") or {}
    shots = brief.get("shots", [])
    global_visual = brief.get("global_visual") or {}
    ratio = brief.get("ratio", "9:16")

    if not shots:
        return {"phase": Phase.GENERATE.value, "generated_assets": [], "generation_ready": False, "error": "Brief 中没有可生成的分镜"}

    segments = plan_segments(shots, SEEDANCE_MAX_DURATION)
    logger.info("[pixelflow] generate task_id=%s shots=%d segments=%d", task_id, len(shots), len(segments))

    image_url = product_info.get("main_image_url")
    if not image_url:
        assets = [{"segment_index": s["index"], "shot_indices": s["shot_indices"], "duration": s["duration"], "ok": False, "error": "无可用图源：商品缺少 main_image_url"} for s in segments]
        return {"phase": Phase.GENERATE.value, "generated_assets": assets, "generation_ready": False, "error": "无可用图源：商品缺少 main_image_url"}

    skill = get_video_skill()
    assets = await asyncio.gather(*(_generate_segment(skill, s, image_url=image_url, global_visual=global_visual, ratio=ratio) for s in segments))
    ready = any(asset.get("ok") and asset.get("url") for asset in assets)
    if not ready:
        errors = [str(asset.get("error")) for asset in assets if asset.get("error")]
        error = "视频生成失败，未返回可用片段"
        if errors:
            error = f"{error}: {'; '.join(errors[:3])}"
        return {"phase": Phase.GENERATE.value, "generated_assets": list(assets), "generation_ready": False, "error": error}
    return {"phase": Phase.SEGMENT_REVIEW.value, "generated_assets": list(assets), "generation_ready": True, "segments_approved": False, "error": ""}


async def edit_node(state: TaskState) -> TaskState:
    """剪辑阶段：把生成片段组装为 Timeline，并交给剪辑 skill 渲染。

    ``build_timeline`` 是纯逻辑转换：把生成成功的片段和 Brief 中的 shot 信息绑定，
    同时保留时长、转场、旁白、花字等剪辑元数据；没有可用片段的 shot 会被跳过并
    写入 ``edit_notes``。

    真正的 I/O 渲染由 ``get_video_edit_skill`` 返回的 skill 承接：默认产出可编辑
    剪映草稿 ``draft_path``，配置为 FFmpeg 时产出 mp4 ``final_video_url``。如果
    本地依赖缺失或渲染失败，只记录到 ``edit_notes``，让流程继续进入 QC。
    """
    task_id = state.get("task_id")
    brief = state.get("brief") or {}
    assets = state.get("generated_assets") or []
    timeline, notes = build_timeline(brief, assets)
    logger.info("[pixelflow] edit task_id=%s clips=%d skipped=%d", task_id, len(timeline.clips), len(notes))

    draft_path = ""
    final_video_url = ""
    if timeline.clips:
        try:
            result = await get_video_edit_skill().render(timeline.model_dump(), draft_name=f"pixelflow_{task_id}")
            if result.ok:
                if result.kind == "video":
                    final_video_url = result.output_path or ""
                else:
                    draft_path = result.output_path or ""
            else:
                notes.append(f"剪辑渲染失败: {result.error}")
        except Exception as exc:  # noqa: BLE001 - boundary: never crash the EDIT phase
            logger.exception("[pixelflow] edit render failed task_id=%s", task_id)
            notes.append(f"剪辑渲染异常: {exc}")

    return {
        "phase": Phase.EDIT_REVIEW.value,
        "timeline": timeline.model_dump(),
        "draft_path": draft_path,
        "final_video_url": final_video_url,
        "edit_notes": notes,
        "edit_approved": False,
    }


async def qc_node(state: TaskState) -> TaskState:
    """质检阶段：检查生成覆盖率和剪辑结果，并决定是否回到生成阶段。

    ``qc_check`` 是纯逻辑校验：生成覆盖率是阻塞项，剪辑总时长偏差是 warning。
    如果出现阻塞性 ``fail``，图会回到 GENERATE 重试；重试次数由
    ``MAX_QC_ATTEMPTS`` 限制，避免持续失败时任务无限循环。
    """
    task_id = state.get("task_id")
    attempts = state.get("qc_attempts", 0) + 1
    result = qc_check(
        state.get("brief") or {},
        state.get("generated_assets") or [],
        state.get("timeline") or {},
        state.get("final_video_url") or "",
    )
    logger.info("[pixelflow] qc task_id=%s attempt=%d passed=%s score=%.2f", task_id, attempts, result.passed, result.score)
    return {
        "phase": Phase.QC_REVIEW.value,
        "qc_passed": result.passed,
        "qc_approved": False,
        "qc_attempts": attempts,
        "qc_report": result.model_dump(),
    }
