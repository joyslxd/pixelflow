/** 校验 PixelFlow 已组装的上下文投影，禁止凭据与运行时控制字段进入 Harness。 */
const forbidden = ["authorization", "credential", "secret", "token", "password", "api_key", "provider"];
export class ContextPolicy {
    maxStringLength;
    constructor(maxStringLength) {
        this.maxStringLength = maxStringLength;
    }
    validate(value) { this.visit(value); }
    dispose() { }
    visit(value) {
        if (typeof value === "string") {
            if (value.length > this.maxStringLength)
                throw new Error("context_value_too_large");
            return;
        }
        if (Array.isArray(value)) {
            value.forEach((item) => this.visit(item));
            return;
        }
        if (value && typeof value === "object") {
            for (const [key, child] of Object.entries(value)) {
                if (forbidden.some((fragment) => key.toLowerCase().includes(fragment)))
                    throw new Error("context_forbidden_field");
                this.visit(child);
            }
        }
    }
}
export const name = "pixelflow-context-policy";
export function apply(ctx, config = {}) {
    const max = config.maxStringLength ?? 32_000;
    if (!Number.isSafeInteger(max) || max < 1)
        throw new Error("context_policy_config_invalid");
    const policy = new ContextPolicy(max);
    const release = ctx.provide("pixelflowContextPolicy", policy);
    return () => { policy.dispose(); release(); };
}
