/** 原生 Video Agent Turn 组合同（设计 §11.6 / §12）。 */

export type NativeToolActivityStatus =
  | "running"
  | "completed"
  | "failed";

export type NativeReasoningStatus = "idle" | "streaming" | "completed";

export type NativeResponseStatus = "idle" | "streaming" | "completed";

export interface NativeToolActivity {
  toolCallId: string;
  toolName: string;
  status: NativeToolActivityStatus;
  title: string;
  publicSummary: string;
  planId: string | null;
  stepId: string | null;
  startedAt: string | null;
  completedAt: string | null;
  durationMs: number | null;
  artifactRefs: string[];
}

export interface NativePlanStepView {
  stepId: string;
  planId: string;
  sequence: number;
  title: string;
  status: string;
  toolName: string;
  durationMs: number | null;
  publicSummary: string;
}

export interface NativeAgentTurn {
  turnId: string;
  /** 按 sequence 推进；乱序/旧 sequence 忽略。 */
  lastSequence: number;
  reasoningStatus: NativeReasoningStatus;
  reasoningText: string;
  reasoningStartedAt: string | null;
  reasoningDurationMs: number | null;
  planId: string | null;
  planSteps: NativePlanStepView[];
  tools: NativeToolActivity[];
  responseStatus: NativeResponseStatus;
  responseText: string;
  /** 用户可见最终回答是否已落定。 */
  responseCompleted: boolean;
}

export interface NativeVideoAgentUiState {
  conversationId: string;
  /** 按出现顺序的 turn_id。 */
  turnOrder: string[];
  turns: Record<string, NativeAgentTurn>;
}

export function createEmptyNativeVideoAgentUiState(
  conversationId = "",
): NativeVideoAgentUiState {
  return {
    conversationId,
    turnOrder: [],
    turns: {},
  };
}

export function createEmptyNativeAgentTurn(turnId: string): NativeAgentTurn {
  return {
    turnId,
    lastSequence: 0,
    reasoningStatus: "idle",
    reasoningText: "",
    reasoningStartedAt: null,
    reasoningDurationMs: null,
    planId: null,
    planSteps: [],
    tools: [],
    responseStatus: "idle",
    responseText: "",
    responseCompleted: false,
  };
}
