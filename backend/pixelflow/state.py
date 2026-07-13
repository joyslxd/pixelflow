"""PixelFlow 任务状态和阶段定义。

PixelFlow 不是自由聊天式 ReAct Agent，而是一条固定阶段的业务流水线。
一个任务会按 PRD 主链路依次经过：采集 → 策划 → Brief 人工确认 → 生成
→ 剪辑 → 质检。``TaskState`` 可以理解成贯穿 Controller/Service 调用链的
“上下文 DTO”：每个 LangGraph node 只返回自己负责修改的字段，LangGraph 再把
这些局部更新合并回完整状态。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class Phase(StrEnum):
    """流水线阶段枚举。

    枚举值的顺序表达正常成功路径，路由函数会基于这些阶段决定下一步进入哪个
    LangGraph node。
    """

    INTAKE = "intake"  # 采集：收集商品/需求信息，并做完整性校验。
    CREATIVE = "creative"  # 策划：生成 Brief，并执行硬约束校验。
    BRIEF_REVIEW = "brief_review"  # 人工确认：用户审核 Brief，批准后才生成视频。
    GENERATE = "generate"  # 生成：调用 Borgrise 等视频能力生成片段。
    SEGMENT_REVIEW = "segment_review"  # 人工确认：用户审核生成片段，批准后进入剪辑。
    EDIT = "edit"  # 剪辑：把生成片段组装为剪映草稿或最终成片。
    EDIT_REVIEW = "edit_review"  # 人工确认：用户审核剪辑结果，批准后进入质检。
    QC = "qc"  # 质检：检查覆盖率/时长等，失败时可回到 GENERATE 重试。
    QC_REVIEW = "qc_review"  # 人工确认：用户审核质检结论，批准后完成任务。
    DONE = "done"  # 终态：任务完成或已终止。


class TaskState(TypedDict, total=False):
    """单个 PixelFlow 视频生成任务的状态 DTO。

    ``total=False`` 表示所有字段都是可选字段，方便每个 node 只返回自己需要
    更新的部分。``messages`` 使用 LangGraph reducer 追加消息；其他字段采用
    “后写覆盖前写”的合并方式。
    """

    messages: Annotated[list, add_messages]

    task_id: str
    phase: Phase

    # 采集：从用户、商品链接、参考视频中得到的结构化需求信息，对应 PRD §8。
    product_info: dict[str, Any]  # §8.1 商品信息，包含主图 main_image_url。
    video_params: dict[str, Any]  # §8.4 视频参数，如平台、时长、比例、分辨率。
    creative_direction: dict[str, Any]  # §8.5 创意方向，如风格、语气、核心卖点。
    reference_videos: list[dict[str, Any]]  # §8.6 参考视频资产和拆解结果。
    intake_check: dict[str, Any]  # §8.7 需求完整性检查报告。
    demand_complete: bool
    intake_rounds: int  # 已追问轮数，用于限制 INTAKE 循环次数。

    # 策划：Brief 是后续生成/剪辑/质检都依赖的权威合同，对应 PRD §9.4。
    brief: dict[str, Any]
    brief_valid: bool
    brief_approved: bool
    brief_issues: list[dict[str, Any]]  # §9.5 校验结果，含自动修复和人工确认风险。

    # 生成：按 segment/shot 记录第三方返回的视频片段资产。
    generated_assets: list[dict[str, Any]]
    generation_ready: bool
    segments_approved: bool

    # 剪辑：把生成资产组装成 Timeline，再交给剪映或 FFmpeg skill 渲染。
    timeline: dict[str, Any]  # EDIT 阶段中间表示，包含有序 clips 和输出格式。
    draft_path: str  # 剪辑 skill 产出的可编辑剪映草稿目录。
    final_video_url: str
    edit_notes: list[str]  # 剪辑阶段的跳过片段、渲染失败、依赖缺失等说明。
    edit_approved: bool

    # 质检：保存 QC 结果和重试计数，用于限制 GENERATE 重试循环。
    qc_passed: bool
    qc_approved: bool
    qc_report: dict[str, Any]
    qc_attempts: int

    # 暴露给网关和前端页面展示的错误摘要。
    error: str

    # 任务启动时注入的 P0 结构化用户偏好快照。
    user_preferences: dict[str, Any]
