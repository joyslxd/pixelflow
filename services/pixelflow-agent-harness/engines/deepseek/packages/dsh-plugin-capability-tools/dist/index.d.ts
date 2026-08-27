/** 将冻结 Manifest 中的 Capability Tool 调用安全转发给 PixelFlow Tool Broker。 */
interface ToolRegistryContext {
    tools: {
        register(tool: ToolDefinition): void;
    };
}
interface RunPolicy {
    assertBillableBatchStart(): void;
    suspend(kind: SuspensionKind): void;
}
type SuspensionKind = "pending_operation" | "awaiting_confirmation" | "authorization_required";
interface ToolDefinition {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    output: {
        schema: Record<string, unknown>;
        render: (args: Record<string, unknown>, value: unknown) => Array<{
            type: "text";
            text: string;
        }>;
    };
    execute: (args: Record<string, unknown>, exec: {
        callId: string | number;
    }) => Promise<BrokerObservation>;
}
interface BrokerObservation {
    status: "completed" | SuspensionKind;
    public_summary: string;
    model_observation: Record<string, unknown>;
    suspension?: {
        kind: SuspensionKind;
        interrupt_id?: string;
    };
}
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export declare const name = "pixelflow-capability-tools";
/** 声明 Plugin 只依赖官方 Tool Registry。 */
export declare const inject: string[];
/** 只按本 Run 经过 Gateway 摘要校验的 Manifest 注册 Tool，禁止硬编码额外能力。 */
export declare function apply(ctx: ToolRegistryContext & {
    pixelflowRunPolicy: RunPolicy;
}): void;
export {};
