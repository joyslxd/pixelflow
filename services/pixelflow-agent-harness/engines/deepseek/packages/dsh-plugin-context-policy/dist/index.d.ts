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
export declare class ContextPolicy {
    private readonly maxStringLength;
    constructor(maxStringLength: number);
    validate(value: unknown): void;
    dispose(): void;
    private visit;
}
export declare const name = "pixelflow-context-policy";
export declare function apply(ctx: CordisContext, config?: ContextPolicyConfig): () => void;
export {};
