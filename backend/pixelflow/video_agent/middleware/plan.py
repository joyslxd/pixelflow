"""观察用短计划 Middleware：记录 goal/steps，不驱动 Executor。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from pixelflow.video_agent.tool_runtime_context import get_tool_runtime_context

_FRAMEWORK_TOOLS = frozenset({"ask_clarification", "update_video_plan"})


@dataclass
class ObservationPlanStep:
    title: str
    tool_name: str | None = None
    status: str = "pending"


@dataclass
class ObservationPlan:
    goal: str
    steps: list[ObservationPlanStep] = field(default_factory=list)
    source: str = "model"  # model | auto


class VideoPlanMiddleware(AgentMiddleware[AgentState]):
    """维护本轮观察 Plan；漏调时自动建立单步记录，绝不调用 Executor。"""

    def __init__(self) -> None:
        super().__init__()
        self.current_plan: ObservationPlan | None = None
        self._seen_business_tools: list[str] = []

    def reset(self) -> None:
        self.current_plan = None
        self._seen_business_tools = []

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self.reset()
        return None

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self.reset()
        return None

    def publish_plan(
        self,
        *,
        goal: str,
        steps: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        source: str = "model",
    ) -> ObservationPlan:
        goal_text = goal.strip() or "执行视频任务"
        normalized: list[ObservationPlanStep] = []
        for index, raw in enumerate(list(steps)[:3], start=1):
            title = str(raw.get("title") or "").strip() or f"步骤 {index}"
            tool_name = raw.get("tool_name")
            tool_name_s = (
                str(tool_name).strip()
                if isinstance(tool_name, str) and tool_name.strip()
                else None
            )
            normalized.append(
                ObservationPlanStep(title=title[:200], tool_name=tool_name_s)
            )
        if not normalized:
            normalized = [ObservationPlanStep(title="处理当前请求")]
        self.current_plan = ObservationPlan(
            goal=goal_text[:2_000],
            steps=normalized,
            source=source,
        )
        return self.current_plan

    def note_business_tool(self, tool_name: str) -> None:
        name = tool_name.strip()
        if not name or name in _FRAMEWORK_TOOLS:
            return
        self._seen_business_tools.append(name)
        if self.current_plan is None:
            # 模型漏调 update_video_plan：自动建立单步观察记录。
            self.publish_plan(
                goal=self._fallback_goal(),
                steps=[{"title": f"执行 {name}", "tool_name": name}],
                source="auto",
            )

    def _fallback_goal(self) -> str:
        runtime = get_tool_runtime_context() or {}
        latest = runtime.get("workspace")
        payload = getattr(latest, "payload", None)
        if isinstance(payload, Mapping):
            text = payload.get("latest_input")
            if isinstance(text, str) and text.strip():
                compact = " ".join(text.split())
                return compact[:80]
        return "处理当前视频请求"

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        if self.current_plan is None and self._seen_business_tools:
            name = self._seen_business_tools[0]
            self.publish_plan(
                goal=self._fallback_goal(),
                steps=[{"title": f"执行 {name}", "tool_name": name}],
                source="auto",
            )
        return None

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_agent(state, runtime)
