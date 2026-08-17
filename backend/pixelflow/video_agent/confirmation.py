"""原生确认闸门身份与文案（Gateway 强制，不交给模型）。"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from pixelflow.video_agent.tools.registry import VideoToolCostLevel, VideoToolSpec


def native_confirmation_id(*, plan_id: str, tool_call_id: str) -> str:
    """按 plan + tool_call 派生稳定确认单 ID。"""

    seed = f"pixelflow-video-native-confirmation:{plan_id.strip()}:{tool_call_id.strip()}"
    return f"video_confirmation_{uuid5(NAMESPACE_URL, seed).hex}"


def confirmation_cost_summary(spec: VideoToolSpec) -> str:
    """面向用户的确认摘要；不含内部参数或推理。"""

    if spec.cost_level is VideoToolCostLevel.BILLABLE:
        return (
            f"即将执行「{spec.name}」，此步骤会调用计费能力。"
            "请确认范围与费用后继续；取消则不会启动任务。"
        )
    if spec.cost_level is VideoToolCostLevel.DESTRUCTIVE:
        return (
            f"即将执行「{spec.name}」，此步骤会修改或替换项目内容且不可轻易撤销。"
            "请确认后再继续。"
        )
    return f"即将执行「{spec.name}」，请确认后继续。"


__all__ = ["confirmation_cost_summary", "native_confirmation_id"]
