/** 校验 PixelFlow 已组装的上下文投影，禁止凭据与运行时控制字段进入 Harness。 */
interface CordisContext {
    provide(name: string, value: unknown): () => void;
}
export interface ContextPolicyConfig {
    maxStringLength?: number;
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
