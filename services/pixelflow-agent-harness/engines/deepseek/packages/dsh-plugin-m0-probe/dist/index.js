/** M0 自定义 Tool Plugin，只验证安全的结构化 Tool 合同。 */
import { defineTool } from "@deepseek-ai/dsh-tools";
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export const name = "pixelflow-m0-probe";
/** 声明 Plugin 只依赖 Harness 已提供的 Tool Registry。 */
export const inject = ["tools", "skills"];
/** 验证隔离 Skill 根后注册一个无副作用的只读 Fake Tool。 */
export async function apply(ctx) {
    const availableSkills = await ctx.skills.list();
    if (!availableSkills.some((skill) => skill.name === "m0-probe-skill")) {
        throw new Error("隔离 DSH_HOME 中缺少 m0-probe-skill");
    }
    if (availableSkills.some((skill) => skill.name === "host-skill")) {
        throw new Error("filesystem Skill Provider 读取了未授权宿主 Skill");
    }
    const probeSkill = await ctx.skills.get("m0-probe-skill");
    if (probeSkill?.content !== "M0 隔离 Skill 正文") {
        throw new Error("m0-probe-skill 正文未按隔离根加载");
    }
    if (!ctx.tools.schemas().some((tool) => tool.name === "skill")) {
        throw new Error("官方 skill Tool 未装配");
    }
    ctx.tools.register(defineTool({
        name: "inspect_video_workspace",
        description: "读取模拟视频工作区的安全摘要，不会修改数据或调用外部服务。",
        parameters: {
            workspace_ref: {
                type: "string",
                required: true,
                description: "由 PixelFlow 生成的 opaque 工作区引用，不能包含用户身份或数据库主键。",
            },
        },
        output: {
            schema: {
                type: "object",
                additionalProperties: false,
                properties: {
                    code: { type: "string", required: true, const: "workspace_inspected" },
                    public_summary: { type: "string", required: true },
                    workspace_revision: { type: "integer", required: true },
                },
            },
            render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }],
        },
        async execute(args) {
            if (Object.keys(args).some((key) => key !== "workspace_ref")) {
                throw new Error("inspect_video_workspace 不接受未知参数");
            }
            if (!args.workspace_ref.startsWith("opaque:")) {
                throw new Error("workspace_ref 必须是 opaque 引用");
            }
            return {
                code: "workspace_inspected",
                public_summary: "已读取模拟视频工作区摘要",
                workspace_revision: 0,
            };
        },
    }));
}
