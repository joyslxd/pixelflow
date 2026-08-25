/** M0 自定义 Tool Plugin，只验证安全的结构化 Tool 合同。 */
import { type ToolDefinition } from "@deepseek-ai/dsh-tools";
/** 描述 Cordis 注入的最小 Tool Registry 能力，避免显式依赖不匹配版本的 Cordis 包。 */
interface ToolRegistryContext {
    tools: {
        register(tool: ToolDefinition): void;
        schemas(): Array<{
            name: string;
        }>;
    };
    skills: {
        list(): Promise<Array<{
            name: string;
        }>>;
        get(name: string): Promise<{
            content: string;
        } | undefined>;
    };
}
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export declare const name = "pixelflow-m0-probe";
/** 声明 Plugin 只依赖 Harness 已提供的 Tool Registry。 */
export declare const inject: string[];
/** 验证隔离 Skill 根后注册一个无副作用的只读 Fake Tool。 */
export declare function apply(ctx: ToolRegistryContext): Promise<void>;
export {};
