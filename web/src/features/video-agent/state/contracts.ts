export type VideoAgentPlanStatus = "planning" | "running" | "awaiting_confirmation" | "completed" | "failed" | "cancelled";

export type VideoAgentStepStatus = "pending" | "running" | "awaiting_confirmation" | "completed" | "failed" | "skipped";

export interface VideoAgentStepState {
  stepId: string;
  sequence: number;
  title: string;
  status: VideoAgentStepStatus;
  publicSummary: string | null;
  artifactRefs: string[];
  startedAt: string | null;
  completedAt: string | null;
  durationMs: number | null;
}

export interface VideoAgentPlanState {
  planId: string;
  workspaceId: string;
  status: VideoAgentPlanStatus;
  publicGoal: string | null;
  steps: Record<string, VideoAgentStepState>;
}

export interface VideoAgentConfirmationState {
  confirmationId: string;
  planId: string;
  stepId: string;
  title: string;
  costSummary: string;
  affectedSceneIds: string[];
  submittable: boolean;
  unavailableReason: string | null;
}

export interface VideoAgentQuotaState {
  quotaInterruptId: string;
  planId: string;
  stepId: string;
  quotaPauseRevision: number;
  phase: "start" | "status";
  reasonCode: "provider_quota_insufficient";
  submittable: boolean;
  unavailableReason: string | null;
}

export interface VideoAgentTimelineState {
  plans: Record<string, VideoAgentPlanState>;
}

export interface VideoAgentPublicEvent {
  type: string;
  payload: Record<string, unknown>;
}
