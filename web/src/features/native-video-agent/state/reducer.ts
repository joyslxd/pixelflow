/** 原生 Video Agent Turn 组投影：事件幂等、忽略旧 sequence。 */

import type { AgentEventEnvelope } from "../../../lib/supervisor/contracts.js";
import {
  createEmptyNativeAgentTurn,
  createEmptyNativeVideoAgentUiState,
  type NativeAgentTurn,
  type NativePlanStepView,
  type NativeToolActivity,
  type NativeVideoAgentUiState,
} from "./contracts.js";

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function ensureTurn(
  state: NativeVideoAgentUiState,
  turnId: string,
  sequence: number,
): { state: NativeVideoAgentUiState; turn: NativeAgentTurn } | null {
  const existing = state.turns[turnId];
  if (existing && sequence <= existing.lastSequence) {
    return null;
  }
  const turn = existing
    ? { ...existing, lastSequence: sequence }
    : { ...createEmptyNativeAgentTurn(turnId), lastSequence: sequence };
  const turnOrder = existing ? state.turnOrder : [...state.turnOrder, turnId];
  return {
    state: {
      ...state,
      turnOrder,
      turns: { ...state.turns, [turnId]: turn },
    },
    turn,
  };
}

function putTurn(
  base: NativeVideoAgentUiState,
  turn: NativeAgentTurn,
): NativeVideoAgentUiState {
  return {
    ...base,
    turns: { ...base.turns, [turn.turnId]: turn },
  };
}

function upsertTool(
  tools: NativeToolActivity[],
  next: NativeToolActivity,
): NativeToolActivity[] {
  const index = tools.findIndex((item) => item.toolCallId === next.toolCallId);
  if (index < 0) return [...tools, next];
  const copy = tools.slice();
  copy[index] = { ...tools[index], ...next };
  return copy;
}

function upsertPlanStep(
  steps: NativePlanStepView[],
  next: NativePlanStepView,
): NativePlanStepView[] {
  const index = steps.findIndex((item) => item.stepId === next.stepId);
  if (index < 0) {
    return [...steps, next].sort((a, b) => a.sequence - b.sequence).slice(0, 3);
  }
  const copy = steps.slice();
  copy[index] = { ...steps[index], ...next };
  return copy.sort((a, b) => a.sequence - b.sequence).slice(0, 3);
}

/**
 * 将一条公开 Agent 事件投影到原生 Turn 组状态。
 * 非本对话、缺 turn_id、或 sequence 不前进时原样返回。
 */
export function reduceNativeVideoAgentEvent(
  state: NativeVideoAgentUiState,
  event: AgentEventEnvelope,
): NativeVideoAgentUiState {
  if (event.conversation_id !== state.conversationId) {
    return state;
  }
  const turnId = asString(event.payload.turn_id).trim();
  if (!turnId) return state;

  const prepared = ensureTurn(state, turnId, event.sequence);
  if (!prepared) return state;
  let { turn } = prepared;
  let nextState = prepared.state;

  switch (event.type) {
    case "agent.reasoning_summary.delta": {
      const delta = asString(event.payload.delta);
      if (!delta) return putTurn(nextState, turn);
      turn = {
        ...turn,
        reasoningStatus: "streaming",
        reasoningText: `${turn.reasoningText}${delta}`,
        reasoningStartedAt: turn.reasoningStartedAt ?? event.occurred_at,
      };
      return putTurn(nextState, turn);
    }
    case "agent.reasoning_summary.completed": {
      const summary = asString(event.payload.summary) || asString(event.payload.text);
      turn = {
        ...turn,
        reasoningStatus: "completed",
        reasoningText: summary || turn.reasoningText,
        reasoningStartedAt: turn.reasoningStartedAt ?? event.occurred_at,
        reasoningDurationMs: asNumber(event.payload.duration_ms),
      };
      return putTurn(nextState, turn);
    }
    case "agent.response.delta": {
      const delta = asString(event.payload.delta);
      if (!delta) return putTurn(nextState, turn);
      turn = {
        ...turn,
        responseStatus: "streaming",
        responseText: `${turn.responseText}${delta}`,
        responseCompleted: false,
      };
      return putTurn(nextState, turn);
    }
    case "agent.response.completed": {
      const text = asString(event.payload.text);
      turn = {
        ...turn,
        responseStatus: "completed",
        responseText: text || turn.responseText,
        responseCompleted: true,
      };
      return putTurn(nextState, turn);
    }
    case "agent.tool.started": {
      const toolCallId = asString(event.payload.tool_call_id).trim();
      const toolName = asString(event.payload.tool_name).trim() || "unknown_tool";
      if (!toolCallId) return putTurn(nextState, turn);
      turn = {
        ...turn,
        tools: upsertTool(turn.tools, {
          toolCallId,
          toolName,
          status: "running",
          title: asString(event.payload.title) || toolName,
          publicSummary: "",
          planId: asOptionalString(event.payload.plan_id),
          stepId: asOptionalString(event.payload.step_id),
          startedAt: asOptionalString(event.payload.started_at) ?? event.occurred_at,
          completedAt: null,
          durationMs: null,
          artifactRefs: [],
        }),
      };
      return putTurn(nextState, turn);
    }
    case "agent.tool.progress": {
      const toolCallId = asString(event.payload.tool_call_id).trim();
      if (!toolCallId) return putTurn(nextState, turn);
      const existing = turn.tools.find((item) => item.toolCallId === toolCallId);
      turn = {
        ...turn,
        tools: upsertTool(turn.tools, {
          toolCallId,
          toolName: asString(event.payload.tool_name) || existing?.toolName || "unknown_tool",
          status: "running",
          title: existing?.title || asString(event.payload.tool_name) || "工具执行中",
          publicSummary: asString(event.payload.public_summary) || existing?.publicSummary || "",
          planId: existing?.planId ?? asOptionalString(event.payload.plan_id),
          stepId: existing?.stepId ?? asOptionalString(event.payload.step_id),
          startedAt: existing?.startedAt ?? event.occurred_at,
          completedAt: null,
          durationMs: null,
          artifactRefs: existing?.artifactRefs ?? [],
        }),
      };
      return putTurn(nextState, turn);
    }
    case "agent.tool.completed":
    case "agent.tool.failed": {
      const toolCallId = asString(event.payload.tool_call_id).trim();
      if (!toolCallId) return putTurn(nextState, turn);
      const existing = turn.tools.find((item) => item.toolCallId === toolCallId);
      const refs = Array.isArray(event.payload.artifact_refs)
        ? event.payload.artifact_refs.filter((item): item is string => typeof item === "string")
        : existing?.artifactRefs ?? [];
      turn = {
        ...turn,
        tools: upsertTool(turn.tools, {
          toolCallId,
          toolName: asString(event.payload.tool_name) || existing?.toolName || "unknown_tool",
          status: event.type === "agent.tool.completed" ? "completed" : "failed",
          title: existing?.title || asString(event.payload.tool_name) || "工具",
          publicSummary: asString(event.payload.public_summary) || existing?.publicSummary || "",
          planId: existing?.planId ?? asOptionalString(event.payload.plan_id),
          stepId: existing?.stepId ?? asOptionalString(event.payload.step_id),
          startedAt: existing?.startedAt ?? null,
          completedAt: asOptionalString(event.payload.completed_at) ?? event.occurred_at,
          durationMs: asNumber(event.payload.duration_ms),
          artifactRefs: refs.slice(0, 32),
        }),
      };
      return putTurn(nextState, turn);
    }
    case "agent.plan.created":
    case "agent.plan.updated": {
      const planId = asOptionalString(event.payload.plan_id);
      if (!planId) return putTurn(nextState, turn);
      const rawSteps = Array.isArray(event.payload.steps) ? event.payload.steps : [];
      const planSteps: NativePlanStepView[] = [];
      for (const raw of rawSteps) {
        if (!raw || typeof raw !== "object") continue;
        const step = raw as Record<string, unknown>;
        const stepId = asString(step.step_id).trim();
        if (!stepId) continue;
        planSteps.push({
          stepId,
          planId,
          sequence: asNumber(step.sequence) ?? planSteps.length + 1,
          title: asString(step.title) || asString(step.tool_name) || stepId,
          status: asString(step.status) || "pending",
          toolName: asString(step.tool_name),
          durationMs: asNumber(step.duration_ms),
          publicSummary: asString(step.public_summary),
        });
      }
      turn = {
        ...turn,
        planId,
        planSteps: planSteps.slice(0, 3),
      };
      return putTurn(nextState, turn);
    }
    case "agent.step.started":
    case "agent.step.progressed":
    case "agent.step.completed":
    case "agent.step.failed": {
      const planId = asOptionalString(event.payload.plan_id);
      const stepId = asOptionalString(event.payload.step_id);
      if (!planId || !stepId) return putTurn(nextState, turn);
      const status = event.type === "agent.step.started" || event.type === "agent.step.progressed"
        ? "running"
        : event.type === "agent.step.completed"
          ? "completed"
          : "failed";
      turn = {
        ...turn,
        planId: turn.planId ?? planId,
        planSteps: upsertPlanStep(turn.planSteps, {
          stepId,
          planId,
          sequence: asNumber(event.payload.sequence) ?? turn.planSteps.length + 1,
          title: asString(event.payload.title) || asString(event.payload.tool_name) || stepId,
          status,
          toolName: asString(event.payload.tool_name),
          durationMs: asNumber(event.payload.duration_ms),
          publicSummary: asString(event.payload.public_summary),
        }),
      };
      return putTurn(nextState, turn);
    }
    default:
      return putTurn(nextState, turn);
  }
}

/**
 * Snapshot 刷新时合并思考历史到 Turn 组。
 * 同会话不得清空已有 tools/正文（startTurn 前会 refreshSnapshot）。
 */
export function hydrateNativeVideoAgentUiState(
  previous: NativeVideoAgentUiState,
  conversationId: string,
  thinkingHistory: ReadonlyArray<{
    turnId: string;
    text?: string;
    answer?: string;
    status?: "streaming" | "completed";
    startedAt?: string | null;
  }>,
): NativeVideoAgentUiState {
  const owner = conversationId.trim();
  if (!owner) {
    return createEmptyNativeVideoAgentUiState("");
  }
  let next: NativeVideoAgentUiState = previous.conversationId === owner
    ? previous
    : createEmptyNativeVideoAgentUiState(owner);

  for (const item of thinkingHistory) {
    const turnId = typeof item.turnId === "string" ? item.turnId.trim() : "";
    if (!turnId) continue;
    const historyText = typeof item.text === "string" ? item.text : "";
    const historyAnswer = typeof item.answer === "string" ? item.answer : "";
    if (!historyText.trim() && !historyAnswer.trim()) continue;

    const existing = next.turns[turnId];
    const preferLocalText = (existing?.reasoningText.length || 0) >= historyText.length;
    const preferLocalAnswer = (existing?.responseText.length || 0) >= historyAnswer.length;
    const reasoningText = preferLocalText
      ? (existing?.reasoningText || historyText)
      : historyText;
    const responseText = preferLocalAnswer
      ? (existing?.responseText || historyAnswer)
      : historyAnswer;
    const responseCompleted = Boolean(
      existing?.responseCompleted
      || item.status === "completed"
      || responseText.trim(),
    );
    const turn: NativeAgentTurn = {
      ...(existing ?? createEmptyNativeAgentTurn(turnId)),
      reasoningText,
      reasoningStatus: reasoningText.trim()
        ? (item.status === "streaming" && !existing?.responseCompleted ? "streaming" : "completed")
        : (existing?.reasoningStatus ?? "idle"),
      reasoningStartedAt: existing?.reasoningStartedAt
        ?? (typeof item.startedAt === "string" ? item.startedAt : null),
      responseText,
      responseStatus: responseCompleted
        ? "completed"
        : (responseText.trim() ? "streaming" : (existing?.responseStatus ?? "idle")),
      responseCompleted,
      // Snapshot 暂不回放 tool 事件；保留内存中已投影的活动卡片。
      tools: existing?.tools ?? [],
      planSteps: existing?.planSteps ?? [],
      planId: existing?.planId ?? null,
      lastSequence: existing?.lastSequence ?? 0,
    };
    const turnOrder = existing ? next.turnOrder : [...next.turnOrder, turnId];
    next = {
      ...next,
      turnOrder,
      turns: { ...next.turns, [turnId]: turn },
    };
  }
  return next;
}

export function resetNativeVideoAgentUiState(
  conversationId: string,
): NativeVideoAgentUiState {
  return createEmptyNativeVideoAgentUiState(conversationId);
}
