/** 将模型选择的 Capability Tool 调用安全转发给 PixelFlow Tool Broker。 */
interface ToolRegistryContext {
    tools: {
        register(tool: ToolDefinition): void;
    };
}
/**
 * 用途：声明本 Plugin 使用的最小 Tool 注册合同；影响：只依赖官方 Runtime 已注入的 tools 服务，
 * 避免离线镜像再解析 Plugin 目录下未受版本控制的 npm 依赖。
 */
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
    code: "workspace_inspected";
    workspace_revision: number;
    artifact_refs: string[];
}
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export declare const name = "pixelflow-capability-tools";
/** 声明 Plugin 只依赖官方 Tool Registry。 */
export declare const inject: string[];
/** 注册只读工作区 Tool；真实权限由 Gateway Broker 决定。 */
export declare function apply(ctx: ToolRegistryContext): void;
export {};
