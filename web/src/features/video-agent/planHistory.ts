/** 会话内执行方案历史合并：权威来源是 agent-snapshot.plans（DB）。
 * sessionStorage 仅作同页热缓存，不能替代服务端持久化。
 */

import type { VideoAgentPlanState } from "@/features/video-agent/state/contracts";

export interface VideoAgentPlanHistory {
  plans: Record<string, VideoAgentPlanState>;
  order: string[];
}

const storageKey = (conversationId: string) =>
  `pixelflow:video-agent-plan-history:${conversationId}`;

function stepCount(plan: VideoAgentPlanState | null | undefined): number {
  return plan ? Object.keys(plan.steps || {}).length : 0;
}

export function preferRicherVideoAgentPlan(
  local: VideoAgentPlanState | null | undefined,
  incoming: VideoAgentPlanState | null | undefined,
): VideoAgentPlanState | null {
  if (!local) return incoming ?? null;
  if (!incoming) return local;
  if (local.planId !== incoming.planId) return incoming;
  const incomingStatus = String(incoming.status || "").toLowerCase();
  // 服务端终态优先：避免 confirmation.requested 本地 upsert 步骤盖住 completed。
  if (
    incomingStatus === "completed"
    || incomingStatus === "failed"
    || incomingStatus === "cancelled"
  ) {
    return incoming;
  }
  return stepCount(local) > stepCount(incoming) ? local : incoming;
}

export function emptyVideoAgentPlanHistory(): VideoAgentPlanHistory {
  return { plans: {}, order: [] };
}

export function loadVideoAgentPlanHistory(conversationId: string): VideoAgentPlanHistory {
  if (!conversationId) return emptyVideoAgentPlanHistory();
  try {
    const raw = sessionStorage.getItem(storageKey(conversationId));
    if (!raw) return emptyVideoAgentPlanHistory();
    const parsed = JSON.parse(raw) as Partial<VideoAgentPlanHistory>;
    if (!parsed || typeof parsed !== "object") return emptyVideoAgentPlanHistory();
    const plans = parsed.plans && typeof parsed.plans === "object" && !Array.isArray(parsed.plans)
      ? parsed.plans as Record<string, VideoAgentPlanState>
      : {};
    const order = Array.isArray(parsed.order)
      ? parsed.order.filter((item): item is string => typeof item === "string" && Boolean(plans[item]))
      : Object.keys(plans);
    for (const planId of Object.keys(plans)) {
      if (!order.includes(planId)) order.push(planId);
    }
    return { plans, order };
  } catch {
    return emptyVideoAgentPlanHistory();
  }
}

export function saveVideoAgentPlanHistory(
  conversationId: string,
  history: VideoAgentPlanHistory,
): void {
  if (!conversationId) return;
  try {
    sessionStorage.setItem(storageKey(conversationId), JSON.stringify(history));
  } catch {
    // ignore quota / private mode
  }
}

/** 把运行时 plan 合并进本地历史，保证多轮后旧执行方案卡片仍可渲染。 */
export function mergeVideoAgentPlanHistory(
  previous: VideoAgentPlanHistory,
  runtimePlans: Record<string, VideoAgentPlanState>,
  runtimeOrder: readonly string[],
  currentPlan: VideoAgentPlanState | null,
): VideoAgentPlanHistory {
  const plans: Record<string, VideoAgentPlanState> = { ...previous.plans };
  const order = [...previous.order];

  const upsert = (plan: VideoAgentPlanState | null | undefined) => {
    if (!plan?.planId) return;
    plans[plan.planId] = preferRicherVideoAgentPlan(plans[plan.planId], plan) ?? plan;
    if (!order.includes(plan.planId)) order.push(plan.planId);
  };

  for (const planId of runtimeOrder) {
    upsert(runtimePlans[planId]);
  }
  for (const plan of Object.values(runtimePlans)) {
    upsert(plan);
  }
  upsert(currentPlan);

  return { plans, order };
}
