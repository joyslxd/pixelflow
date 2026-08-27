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
export declare class RunPolicy {
    private readonly config;
    private readonly startedAt;
    private modelSteps;
    private businessTools;
    private billableBatchStarts;
    private cancelled;
    private suspension;
    constructor(config: Required<RunPolicyConfig>);
    assertModelStep(): void;
    assertBusinessTool(): void;
    /** 计费批次只能由 Broker 的稳定结果计数，模型参数和 Skill 均无权修改。 */
    assertBillableBatchStart(): void;
    /** 收到业务挂起结果后阻断同一 Harness Session 的下一次模型或 Tool 调用。 */
    suspend(kind: SuspensionKind): void;
    cancel(): void;
    dispose(): void;
    private assertActive;
}
export declare const name = "pixelflow-run-policy";
export declare function apply(ctx: CordisContext, config?: RunPolicyConfig): () => void;
export {};
