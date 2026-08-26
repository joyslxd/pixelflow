/** 将模型选择的 Capability Tool 调用安全转发给 PixelFlow Tool Broker。 */

import { createHash, createHmac } from "node:crypto";

interface ToolRegistryContext {
  tools: { register(tool: ToolDefinition): void };
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
    render: (args: Record<string, unknown>, value: unknown) => Array<{ type: "text"; text: string }>;
  };
  execute: (args: Record<string, unknown>, exec: { callId: string | number }) => Promise<BrokerObservation>;
}

interface BrokerSettings {
  baseUrl: string;
  signingKey: string;
  issuer: string;
  audience: string;
  instanceId: string;
  runId: string;
  sessionId: string;
  contextDigest: string;
  toolsetVersion: string;
  workspaceRevision: number;
}

interface BrokerObservation {
  code: "workspace_inspected";
  workspace_revision: number;
  artifact_refs: string[];
}

/** 声明供 Cordis Loader 识别的稳定 Plugin 名称。 */
export const name = "pixelflow-capability-tools";

/** 声明 Plugin 只依赖官方 Tool Registry。 */
export const inject = ["tools"];

/** 注册只读工作区 Tool；真实权限由 Gateway Broker 决定。 */
export function apply(ctx: ToolRegistryContext): void {
  ctx.tools.register(
    {
      name: "inspect_video_workspace",
      description: "读取当前 PixelFlow 视频工作区的安全摘要。用户询问项目现状、分镜、素材或生成进度时应调用此工具，不要猜测工作区内容。",
      parameters: {
        type: "object",
        properties: {},
      },
      output: {
        schema: {
          type: "object",
          additionalProperties: false,
          properties: {
            code: { type: "string", const: "workspace_inspected" },
            public_summary: { type: "string" },
            workspace_revision: { type: "integer" },
            artifact_refs: { type: "array", items: { type: "string" } },
          },
          required: ["code", "public_summary", "workspace_revision", "artifact_refs"],
        },
        render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }],
      },
      async execute(args, exec) {
        if (Object.keys(args).length !== 0) throw new Error("inspect_video_workspace 不接受参数");
        const settings = settingsFromEnvironment();
        const toolCallId = String(exec.callId);
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
            tool_name: "inspect_video_workspace",
            arguments: {},
            expected_workspace_revision: settings.workspaceRevision,
            context_digest: settings.contextDigest,
            toolset_version: settings.toolsetVersion,
          }),
          signal: AbortSignal.timeout(10_000),
        });
        if (!response.ok) throw new Error("PixelFlow Tool Broker 拒绝了工作区读取请求");
        const payload: unknown = await response.json();
        return canonicalObservation(payload);
      },
    },
  );
}

/** 从进程环境读取本次 Run 必需的最小绑定数据。 */
function settingsFromEnvironment(): BrokerSettings {
  const rawRevision = process.env.PIXELFLOW_HARNESS_WORKSPACE_REVISION ?? "";
  const workspaceRevision = Number.parseInt(rawRevision, 10);
  const value: BrokerSettings = {
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
  };
  if (!isSafeBrokerUrl(value.baseUrl) || value.signingKey.length < 32 || !value.instanceId
    || !/^hrun_[a-z0-9]+$/u.test(value.runId) || !/^pfh_[a-z0-9_]+$/u.test(value.sessionId)
    || !/^sha256:[a-f0-9]{64}$/u.test(value.contextDigest) || !value.toolsetVersion
    || !Number.isSafeInteger(value.workspaceRevision) || value.workspaceRevision < 1) {
    throw new Error("PixelFlow Tool Broker 运行配置不完整或无效");
  }
  return value;
}

/** 生成仅用于 Sidecar→Broker 的五分钟 HS256 服务 JWT。 */
function serviceJwt(settings: BrokerSettings): string {
  const now = Math.floor(Date.now() / 1000);
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const claims = base64Url(JSON.stringify({
    sub: "pixelflow-harness-sidecar",
    iss: settings.issuer,
    aud: settings.audience,
    service_instance_id: settings.instanceId,
    iat: now,
    exp: now + 300,
  }));
  const signature = createHmac("sha256", settings.signingKey).update(`${header}.${claims}`).digest("base64url");
  return `${header}.${claims}.${signature}`;
}

/** 计算与 Gateway 完全相同的稳定 Tool Call 身份。 */
function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

/** Base64URL 编码 JWT JSON 节，避免引入第二个 JWT Runtime 依赖。 */
function base64Url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

/** 严格过滤 Broker Observation，拒绝未知字段或安全状态以外的任意载荷。 */
function canonicalObservation(payload: unknown): {
  code: "workspace_inspected";
  public_summary: string;
  workspace_revision: number;
  artifact_refs: string[];
} {
  const observation = isRecord(payload) ? payload.model_observation : undefined;
  const workspaceRevision = isRecord(observation) ? observation.workspace_revision : undefined;
  const artifactRefs = isRecord(observation) ? observation.artifact_refs : undefined;
  if (!isRecord(payload) || payload.protocol_version !== "v1" || payload.status !== "completed"
    || typeof payload.public_summary !== "string" || payload.public_summary.length === 0 || payload.public_summary.length > 512
    || !isRecord(observation) || observation.code !== "workspace_inspected"
    || typeof workspaceRevision !== "number" || !Number.isSafeInteger(workspaceRevision)
    || !Array.isArray(artifactRefs)
    || artifactRefs.some((item) => typeof item !== "string" || !item.startsWith("artifact:"))) {
    throw new Error("PixelFlow Tool Broker 返回了无效 Observation");
  }
  return {
    code: "workspace_inspected",
    public_summary: payload.public_summary,
    workspace_revision: workspaceRevision,
    artifact_refs: artifactRefs as string[],
  };
}

/** 判断未经信任 JSON 是否为普通对象。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 限制 Broker 网络目标，生产只能走 HTTPS。 */
function isSafeBrokerUrl(value: string): boolean {
  return value.startsWith("https://") || /^http:\/\/127\.0\.0\.1:\d+$/u.test(value);
}
