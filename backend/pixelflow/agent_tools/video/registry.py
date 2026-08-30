"""实现视频 Tool 的受控注册、参数校验与 Workspace 变更白名单。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from pydantic import ValidationError

from pixelflow.video.contracts import VideoToolResult

from .contracts import (
    VideoTool,
    VideoToolContext,
    VideoToolExecutionError,
    VideoToolSpec,
    VideoToolValidationError,
)

logger = logging.getLogger(__name__)


class VideoToolRegistry:
    """只解析启动时显式注册的 Tool，并在执行前统一校验参数与变更范围。"""

    def __init__(self, tools: Iterable[VideoTool]) -> None:
        """冻结本次运行可用的 Tool 集合，拒绝空名和重复名。"""

        registered: dict[str, VideoTool] = {}
        for tool in tools:
            name = tool.spec.name.strip()
            if not name:
                raise ValueError("工具名称不能为空")
            if name in registered:
                raise ValueError(f"工具名称重复：{name}")
            registered[name] = tool
        self._tools = registered

    def names(self) -> tuple[str, ...]:
        """返回稳定排序后的 Tool 名称，供 Manifest 与测试使用。"""

        return tuple(sorted(self._tools))

    def resolve(self, name: str) -> VideoTool | None:
        """按名称读取启动期已冻结的 Tool。"""

        return self._tools.get(name)

    def specs(self) -> tuple[VideoToolSpec, ...]:
        """返回当前 Tool 集合的稳定规格快照。"""

        return tuple(self._tools[name].spec for name in self.names())

    async def execute(
        self,
        context: VideoToolContext,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> VideoToolResult:
        """校验输入、执行 Handler，并限制结果只能修改已声明的 Workspace 根键。"""

        tool = self.resolve(tool_name)
        if tool is None:
            raise VideoToolValidationError("规划器选择了未注册工具")
        try:
            validated = tool.spec.input_model.model_validate(dict(arguments))
        except ValidationError as error:
            fields = _safe_validation_fields(error)
            summary = "工具参数无效，请修正后重试"
            if fields:
                summary = f"工具参数无效，请修正字段：{'、'.join(fields)}"
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary=summary,
                model_observation=(
                    {"validation_fields": fields}
                    if "validation_fields" in tool.spec.model_observation_keys
                    else {}
                ),
            )
        try:
            result = await tool.execute(
                context,
                validated.model_dump(mode="json", exclude_unset=True),
            )
        except VideoToolValidationError as error:
            detail = str(error).strip()
            return VideoToolResult(
                tool_name=tool.spec.name,
                public_summary=detail[:280] if detail else "工具参数无效，请修正后重试",
            )
        except VideoToolExecutionError as error:
            detail = str(error).strip()
            logger.warning(
                "video tool business failure name=%s error_type=%s detail_prefix=%s",
                tool.spec.name,
                type(error).__name__,
                detail[:80] if detail else "",
            )
            if detail and (
                detail.startswith(
                    (
                        "场景包",
                        "参考图",
                        "generate_scene_assets",
                        "prepare_scene_packages",
                        "视频交付",
                        "视频合并",
                        "剪映交付",
                    )
                )
                or "不是可生成实体" in detail
                or "缺少计划身份" in detail
                or "缺少临时授权" in detail
                or "尚未装配" in detail
                or "校验失败" in detail
            ):
                summary = detail[:280]
            else:
                summary = f"{tool.spec.name} 执行失败，请稍后重试"
            return VideoToolResult(tool_name=tool.spec.name, public_summary=summary)
        allowed_roots = {
            mutation.split(".", maxsplit=1)[0]
            for mutation in tool.spec.workspace_mutations
        }
        patch_roots = set(result.workspace_patch)
        if result.tool_name != tool.spec.name or not patch_roots.issubset(allowed_roots):
            undeclared = sorted(patch_roots - allowed_roots)
            logger.warning(
                "工具结果根键校验失败 tool=%s undeclared=%s",
                tool.spec.name,
                undeclared,
            )
            raise VideoToolExecutionError("工具结果无效，请稍后重试")
        return result


def _safe_validation_fields(error: ValidationError) -> list[str]:
    """只反馈 DTO 字段路径，帮助模型修正参数，不回显用户输入或校验原文。"""

    detailed: list[str] = []
    top_level: list[str] = []
    for item in error.errors():
        location = item.get("loc")
        if not isinstance(location, tuple):
            continue
        parts = [str(part) for part in location if isinstance(part, (str, int))]
        if not parts:
            continue
        field = ".".join(parts)
        target = detailed if len(parts) > 1 else top_level
        if 0 < len(field) <= 160 and field not in target:
            target.append(field)
        if len(detailed) >= 8:
            break
    # 嵌套 DTO 缺少字段时，Pydantic 还会附带父数组“元素不足”错误；优先返回可修正的
    # 详细路径，避免模型把 scenes 误认为需要替换为另一种数据类型。
    return (detailed or top_level)[:8]


__all__ = ["VideoToolRegistry"]
