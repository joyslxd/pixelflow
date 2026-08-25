/** 将模型选择的 Capability Tool 调用安全转发给 PixelFlow Tool Broker。 */
import { type ToolDefinition } from "@deepseek-ai/dsh-tools";
interface ToolRegistryContext {
    tools: {
        register(tool: ToolDefinition): void;
    };
}
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export declare const name = "pixelflow-capability-tools";
/** 声明 Plugin 只依赖官方 Tool Registry。 */
export declare const inject: string[];
/** 注册只读工作区 Tool；真实权限由 Gateway Broker 决定。 */
export declare function apply(ctx: ToolRegistryContext): void;
export {};
