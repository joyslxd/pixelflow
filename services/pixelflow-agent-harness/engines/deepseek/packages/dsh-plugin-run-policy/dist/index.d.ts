/** 为单个 Harness Run 提供无业务副作用的资源与取消策略。 */
interface CordisContext {
    provide(name: string, value: unknown): () => void;
    on(name: "agent/request" | "tools/pre-execute" | "session/disposed", listener: (...args: unknown[]) => unknown): () => boolean;
}
export interface RunPolicyConfig {
    maxModelSteps?: number;
    maxBusinessTools?: number;
    deadlineSeconds?: number;
}
export declare class RunPolicy {
    private readonly config;
    private readonly startedAt;
    private modelSteps;
    private businessTools;
    private cancelled;
    constructor(config: Required<RunPolicyConfig>);
    assertModelStep(): void;
    assertBusinessTool(): void;
    cancel(): void;
    dispose(): void;
    private assertActive;
}
export declare const name = "pixelflow-run-policy";
export declare function apply(ctx: CordisContext, config?: RunPolicyConfig): () => void;
export {};
