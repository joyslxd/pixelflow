/** 为单个 Harness Run 提供无业务副作用的资源与取消策略。 */
export class RunPolicy {
    config;
    startedAt = Date.now();
    modelSteps = 0;
    businessTools = 0;
    cancelled = false;
    constructor(config) {
        this.config = config;
    }
    assertModelStep() {
        this.assertActive();
        if (this.modelSteps >= this.config.maxModelSteps)
            throw new Error("max_model_steps");
        this.modelSteps += 1;
    }
    assertBusinessTool() {
        this.assertActive();
        if (this.businessTools >= this.config.maxBusinessTools)
            throw new Error("max_business_tools");
        this.businessTools += 1;
    }
    cancel() { this.cancelled = true; }
    dispose() { this.cancel(); }
    assertActive() {
        if (this.cancelled)
            throw new Error("cancelled");
        if (Date.now() - this.startedAt > this.config.deadlineSeconds * 1_000)
            throw new Error("deadline_exceeded");
    }
}
export const name = "pixelflow-run-policy";
export function apply(ctx, config = {}) {
    const policy = new RunPolicy({
        maxModelSteps: positive(config.maxModelSteps, 8),
        maxBusinessTools: positive(config.maxBusinessTools, 3),
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
function positive(value, fallback) {
    if (value === undefined)
        return fallback;
    if (!Number.isSafeInteger(value) || value < 1)
        throw new Error("run_policy_config_invalid");
    return value;
}
