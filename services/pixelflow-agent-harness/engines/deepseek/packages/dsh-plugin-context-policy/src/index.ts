/** 校验 PixelFlow 已组装的上下文投影，禁止凭据与运行时控制字段进入 Harness。 */

interface CordisContext {
  provide(name: string, value: unknown): () => void;
  on(name: "agent/request", listener: (payload: unknown, next?: () => unknown) => unknown): () => boolean;
}
export interface ContextPolicyConfig {
  maxStringLength?: number;
  /** Gateway 冻结的会话、工作区与偏好投影；不接收 Provider 运行时载荷。 */
  projection?: unknown;
}

const forbidden = ["authorization", "credential", "secret", "token", "password", "api_key", "provider"];

export class ContextPolicy {
  constructor(private readonly maxStringLength: number) {}
  validate(value: unknown): void { this.visit(value); }
  dispose(): void {}

  private visit(value: unknown): void {
    if (typeof value === "string") {
      if (value.length > this.maxStringLength) throw new Error("context_value_too_large");
      return;
    }
    if (Array.isArray(value)) { value.forEach((item) => this.visit(item)); return; }
    if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) {
        if (forbidden.some((fragment) => key.toLowerCase().includes(fragment))) throw new Error("context_forbidden_field");
        this.visit(child);
      }
    }
  }
}

export const name = "pixelflow-context-policy";
export function apply(ctx: CordisContext, config: ContextPolicyConfig = {}): () => void {
  const max = config.maxStringLength ?? 32_000;
  if (!Number.isSafeInteger(max) || max < 1) throw new Error("context_policy_config_invalid");
  const policy = new ContextPolicy(max);
  // Composition 启动时验证本 Run 的冻结投影，使 Policy 在真实 Engine 调用前生效。
  if (config.projection !== undefined) policy.validate(config.projection);
  const release = ctx.provide("pixelflowContextPolicy", policy);
  // `agent/request` 的完整载荷包含 Runtime/Provider 元数据，不能把它当成业务上下文
  // 直接校验。只有 Gateway 明确标记的投影才属于本 Policy 的输入边界。
  const stopValidation = ctx.on("agent/request", (payload, next) => {
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      const projection = (payload as Record<string, unknown>).pixelflow_context_projection;
      if (projection !== undefined) policy.validate(projection);
    }
    return typeof next === "function" ? next() : undefined;
  });
  return () => { stopValidation(); policy.dispose(); release(); };
}
