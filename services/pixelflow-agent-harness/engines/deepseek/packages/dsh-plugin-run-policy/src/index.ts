/** 为单个 Harness Run 提供无业务副作用的资源与取消策略。 */

interface CordisContext {
  provide(name: string, value: unknown): () => void;
  on(name: "agent/request" | "tools/pre-execute" | "session/disposed", listener: (...args: unknown[]) => unknown): () => boolean;
}

export interface RunPolicyConfig {
  maxModelSteps?: number;
  maxBusinessTools?: number;
  maxBillableBatchStarts?: number;
  deadlineSeconds?: number;
}

export type SuspensionKind = "pending_operation" | "awaiting_confirmation" | "authorization_required";

export class RunPolicy {
  private readonly startedAt = Date.now();
  private modelSteps = 0;
  private businessTools = 0;
  private billableBatchStarts = 0;
  private cancelled = false;
  private suspension: SuspensionKind | undefined;

  constructor(private readonly config: Required<RunPolicyConfig>) {}

  assertModelStep(): void {
    this.assertActive();
    if (this.modelSteps >= this.config.maxModelSteps) throw new Error("max_model_steps");
    this.modelSteps += 1;
  }

  assertBusinessTool(): void {
    this.assertActive();
    if (this.businessTools >= this.config.maxBusinessTools) throw new Error("max_business_tools");
    this.businessTools += 1;
  }

  /** 计费批次只能由 Broker 的稳定结果计数，模型参数和 Skill 均无权修改。 */
  assertBillableBatchStart(): void {
    this.assertActive();
    if (this.billableBatchStarts >= this.config.maxBillableBatchStarts) throw new Error("max_billable_batch_starts");
    this.billableBatchStarts += 1;
  }

  /** 收到业务挂起结果后阻断同一 Harness Session 的下一次模型或 Tool 调用。 */
  suspend(kind: SuspensionKind): void {
    this.assertActive();
    this.suspension = kind;
  }

  cancel(): void { this.cancelled = true; }

  dispose(): void { this.cancel(); }

  private assertActive(): void {
    if (this.cancelled) throw new Error("cancelled");
    if (this.suspension !== undefined) throw new Error(this.suspension);
    if (Date.now() - this.startedAt > this.config.deadlineSeconds * 1_000) throw new Error("deadline_exceeded");
  }
}

export const name = "pixelflow-run-policy";

export function apply(ctx: CordisContext, config: RunPolicyConfig = {}): () => void {
  const policy = new RunPolicy({
    maxModelSteps: positive(config.maxModelSteps, 8),
    maxBusinessTools: positive(config.maxBusinessTools, 3),
    maxBillableBatchStarts: nonNegative(config.maxBillableBatchStarts, 0),
    deadlineSeconds: positive(config.deadlineSeconds, 90),
  });
  const release = ctx.provide("pixelflowRunPolicy", policy);
  const stopModelSteps = ctx.on("agent/request", (_payload, next) => {
    policy.assertModelStep();
    return typeof next === "function" ? next() : undefined;
  });
  const stopToolCalls = ctx.on("tools/pre-execute", (_execution, next) => {
    policy.assertBusinessTool();
    return typeof next === "function" ? next() : undefined;
  });
  const stopSession = ctx.on("session/disposed", () => { policy.cancel(); });
  return () => { stopSession(); stopToolCalls(); stopModelSteps(); policy.dispose(); release(); };
}

function positive(value: number | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < 1) throw new Error("run_policy_config_invalid");
  return value;
}

function nonNegative(value: number | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("run_policy_config_invalid");
  return value;
}
