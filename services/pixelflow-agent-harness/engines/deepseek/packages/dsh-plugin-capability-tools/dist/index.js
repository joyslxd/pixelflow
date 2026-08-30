/** 将冻结 Manifest 中的 Capability Tool 调用安全转发给 PixelFlow Tool Broker。 */
import { createHash, createHmac } from "node:crypto";
/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export const name = "pixelflow-capability-tools";
/** 声明 Plugin 只依赖官方 Tool Registry。 */
export const inject = ["tools", "pixelflowRunPolicy"];
/** 只按本 Run 经过 Gateway 摘要校验的 Manifest 注册 Tool，禁止硬编码额外能力。 */
export function apply(ctx) {
    const manifest = frozenManifestFromEnvironment();
    for (const tool of manifest.tools) {
        ctx.tools.register({
            name: tool.name,
            description: tool.description,
            parameters: tool.parameters_schema,
            output: {
                schema: {
                    type: "object",
                    additionalProperties: false,
                    properties: {
                        public_summary: { type: "string" },
                        model_observation: { type: "object" },
                    },
                    required: ["public_summary", "model_observation"],
                },
                render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }],
            },
            async execute(args, exec) {
                const observation = await callBroker(tool, args, String(exec.callId));
                if (tool.cost_level === "billable" && observation.status === "pending_operation") {
                    ctx.pixelflowRunPolicy.assertBillableBatchStart();
                }
                if (observation.status !== "completed") {
                    ctx.pixelflowRunPolicy.suspend(observation.status);
                }
                return observation;
            },
        });
    }
}
/** 从进程环境读取本次 Run 必需的最小绑定数据。 */
function settingsFromEnvironment() {
    const rawRevision = process.env.PIXELFLOW_HARNESS_WORKSPACE_REVISION ?? "";
    const workspaceRevision = Number.parseInt(rawRevision, 10);
    const value = {
        baseUrl: (process.env.PIXELFLOW_TOOL_BROKER_BASE_URL ?? "").replace(/\/$/u, ""),
        signingKey: process.env.PIXELFLOW_TOOL_BROKER_JWT_SIGNING_KEY ?? "",
        issuer: process.env.PIXELFLOW_TOOL_BROKER_JWT_ISSUER ?? "pixelflow-harness-sidecar",
        audience: process.env.PIXELFLOW_TOOL_BROKER_JWT_AUDIENCE ?? "pixelflow-tool-broker",
        instanceId: process.env.PIXELFLOW_SIDECAR_INSTANCE_ID ?? "",
        runId: process.env.PIXELFLOW_HARNESS_RUN_ID ?? "",
        sessionId: process.env.PIXELFLOW_HARNESS_SESSION_ID ?? "",
        contextDigest: process.env.PIXELFLOW_HARNESS_CONTEXT_DIGEST ?? "",
        toolsetVersion: process.env.PIXELFLOW_HARNESS_TOOLSET_VERSION ?? "",
        workspaceRevision,
        maxBillableBatchStarts: parseNonNegativeInteger(process.env.PIXELFLOW_HARNESS_MAX_BILLABLE_BATCH_STARTS ?? ""),
    };
    if (!isSafeBrokerUrl(value.baseUrl) || value.signingKey.length < 32 || !value.instanceId
        || !/^hrun_[a-z0-9]+$/u.test(value.runId) || !/^pfh_[a-z0-9_]+$/u.test(value.sessionId)
        || !/^sha256:[a-f0-9]{64}$/u.test(value.contextDigest) || !value.toolsetVersion
        || !Number.isSafeInteger(value.workspaceRevision) || value.workspaceRevision < 1
        || !Number.isSafeInteger(value.maxBillableBatchStarts) || value.maxBillableBatchStarts < 0) {
        throw new Error("PixelFlow Tool Broker 运行配置不完整或无效");
    }
    return value;
}
/** 读取经 Sidecar 校验 digest 的 Manifest JSON；计费 Tool 仍须由 Broker 返回稳定挂起结果。 */
function frozenManifestFromEnvironment() {
    const raw = process.env.PIXELFLOW_HARNESS_TOOL_MANIFEST_JSON ?? "";
    let parsed;
    try {
        parsed = JSON.parse(raw);
    }
    catch {
        throw new Error("冻结 Tool Manifest 无效");
    }
    if (!isRecord(parsed) || parsed.protocol_version !== "v1" || typeof parsed.version !== "string"
        || typeof parsed.digest !== "string" || !Array.isArray(parsed.tools)
        || parsed.version !== (process.env.PIXELFLOW_HARNESS_TOOLSET_VERSION ?? "")) {
        throw new Error("冻结 Tool Manifest 合同不匹配");
    }
    const tools = parsed.tools.map(validateManifestTool);
    if (new Set(tools.map((tool) => tool.name)).size !== tools.length) {
        throw new Error("冻结 Tool Manifest 存在重复 Tool");
    }
    return { protocol_version: "v1", version: parsed.version, digest: parsed.digest, tools };
}
/** Manifest 只允许已声明成本与确认边界的 Capability Tool。 */
function validateManifestTool(value) {
    if (!isRecord(value) || typeof value.name !== "string" || !/^[a-z][a-z0-9_]{0,127}$/u.test(value.name)
        || typeof value.description !== "string" || value.description.length === 0 || value.description.length > 2_000
        || !isRecord(value.parameters_schema) || !isCostLevel(value.cost_level)
        || typeof value.confirmation_required !== "boolean") {
        throw new Error("冻结 Tool Manifest 包含未授权能力");
    }
    return {
        name: value.name,
        description: value.description,
        parameters_schema: value.parameters_schema,
        cost_level: value.cost_level,
        confirmation_required: value.confirmation_required,
    };
}
function isCostLevel(value) {
    return value === "none" || value === "billable";
}
/** 通过唯一 Gateway Broker 执行调用，Sidecar 不直连 Repository、Provider 或文件系统。 */
async function callBroker(tool, args, toolCallId) {
    const settings = settingsFromEnvironment();
    const response = await fetch(`${settings.baseUrl}/agent/internal/agent-tools/calls`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${serviceJwt(settings)}`,
            "Idempotency-Key": sha256(`${settings.runId}:${toolCallId}`),
        },
        body: JSON.stringify({
            protocol_version: "v1",
            run_id: settings.runId,
            session_id: settings.sessionId,
            tool_call_id: toolCallId,
            tool_name: tool.name,
            arguments: args,
            expected_workspace_revision: settings.workspaceRevision,
            context_digest: settings.contextDigest,
            toolset_version: settings.toolsetVersion,
        }),
        signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok)
        throw new Error("PixelFlow Tool Broker 拒绝了 Tool 调用");
    return canonicalObservation(await response.json());
}
/** 生成仅用于 Sidecar→Broker 的五分钟 HS256 服务 JWT。 */
function serviceJwt(settings) {
    const now = Math.floor(Date.now() / 1000);
    const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
    const claims = base64Url(JSON.stringify({
        sub: "pixelflow-harness-sidecar", iss: settings.issuer, aud: settings.audience,
        service_instance_id: settings.instanceId, iat: now, exp: now + 300,
    }));
    const signature = createHmac("sha256", settings.signingKey).update(`${header}.${claims}`).digest("base64url");
    return `${header}.${claims}.${signature}`;
}
function sha256(value) {
    return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
function base64Url(value) {
    return Buffer.from(value, "utf8").toString("base64url");
}
/** 严格过滤 Broker 结果，绝不把 Provider raw 或未知字段带回 Harness。 */
function canonicalObservation(payload) {
    if (!isRecord(payload) || payload.protocol_version !== "v1" || !isBrokerResponseStatus(payload.status)
        || typeof payload.public_summary !== "string" || payload.public_summary.length === 0 || payload.public_summary.length > 512
        || !isRecord(payload.model_observation)) {
        throw new Error("PixelFlow Tool Broker 返回了无效 Observation");
    }
    // rejected/failed 是 Broker 的受控业务结果，不是协议错误。它们不挂起当前
    // Run，模型可依据安全摘要重新观察 Workspace 或向用户说明下一步。
    if (payload.status === "completed" || payload.status === "rejected" || payload.status === "failed") {
        return { status: "completed", public_summary: payload.public_summary, model_observation: payload.model_observation };
    }
    if (!isRecord(payload.suspension) || payload.suspension.kind !== payload.status) {
        throw new Error("PixelFlow Tool Broker 返回了无效挂起合同");
    }
    const requiresInterruptId = payload.status === "awaiting_confirmation" || payload.status === "authorization_required";
    const interruptId = requiresInterruptId && typeof payload.suspension.interrupt_id === "string"
        && /^[a-zA-Z0-9_-]{1,128}$/u.test(payload.suspension.interrupt_id)
        ? payload.suspension.interrupt_id
        : undefined;
    if (requiresInterruptId && !interruptId) {
        throw new Error("PixelFlow Tool Broker 返回的人工中断身份无效");
    }
    return {
        status: payload.status,
        public_summary: payload.public_summary,
        model_observation: payload.model_observation,
        suspension: { kind: payload.status, ...(interruptId ? { interrupt_id: interruptId } : {}) },
    };
}
function isBrokerResponseStatus(value) {
    return value === "completed" || value === "rejected" || value === "failed"
        || value === "pending_operation" || value === "awaiting_confirmation" || value === "authorization_required";
}
function parseNonNegativeInteger(value) {
    if (!/^\d+$/u.test(value))
        return Number.NaN;
    return Number.parseInt(value, 10);
}
function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isSafeBrokerUrl(value) {
    // Compose 内网只允许固定 Gateway 服务名；与 SidecarSettings 的启动期白名单保持一致。
    return value.startsWith("https://")
        || /^http:\/\/127\.0\.0\.1:\d+$/u.test(value)
        || /^http:\/\/gateway:\d+$/u.test(value);
}
