import type { JsonObject, JsonValue, TurnStartRequest } from "./contracts.js";

export interface SupervisorSubmissionInput {
  conversationId: string;
  clientInputId: string;
  content: string;
  materials: Array<Record<string, unknown>>;
  replyToMessageId?: string | null;
  artifactRefs?: string[];
  interruptId?: string | null;
}

export type SupervisorSubmission =
  | {
    kind: "turn";
    request: TurnStartRequest;
  }
  | {
    kind: "interrupt";
    interruptId: string;
    request: JsonObject;
  };

const ARTIFACT_REF_PATTERN = /^artifact:\S+$/u;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function cloneJsonValue(value: unknown, ancestors = new WeakSet<object>()): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "object" || ancestors.has(value)) {
    throw new TypeError("提交元数据不是合法 JSON");
  }

  ancestors.add(value);
  const cloned: JsonValue = Array.isArray(value)
    ? value.map((item) => cloneJsonValue(item, ancestors))
    : Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([key, item]) => [key, cloneJsonValue(item, ancestors)]),
    );
  ancestors.delete(value);
  return cloned;
}

function cloneMaterial(value: unknown): JsonObject {
  if (!isRecord(value)) throw new TypeError("素材元数据格式不合法");
  const cloned = cloneJsonValue(value);
  if (!isRecord(cloned)) throw new TypeError("素材元数据格式不合法");
  return cloned as JsonObject;
}

function normalizeRequiredId(value: unknown, message: string): string {
  if (typeof value !== "string" || value.trim().length === 0) throw new TypeError(message);
  return value.trim();
}

function optionalId(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new TypeError("目标标识格式不合法");
  return value.trim() || null;
}

function artifactRefsFromValue(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value];
  return values
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => ARTIFACT_REF_PATTERN.test(item));
}

function collectArtifactRefs(
  explicitRefs: string[] | undefined,
  materials: JsonObject[],
): string[] {
  const candidates = [
    ...artifactRefsFromValue(explicitRefs || []),
    ...materials.flatMap((material) => [
      ...artifactRefsFromValue(material.artifact_ref),
      ...artifactRefsFromValue(material.artifact_refs),
      ...artifactRefsFromValue(material.mention_ref),
      ...artifactRefsFromValue(material.mention_refs),
    ]),
  ];
  return [...new Set(candidates)];
}

function collectReplyToMessageId(
  explicitReplyToMessageId: string | null | undefined,
  materials: JsonObject[],
): string | null {
  const candidates = [
    optionalId(explicitReplyToMessageId),
    ...materials.flatMap((material) => [
      optionalId(material.reply_to_message_id),
      optionalId(material.storyboard_message_id),
    ]),
  ].filter((item): item is string => item !== null);
  const uniqueCandidates = [...new Set(candidates)];
  if (uniqueCandidates.length > 1) throw new TypeError("目标消息引用不唯一");
  return uniqueCandidates[0] || null;
}

function validateMaterialOwners(materials: JsonObject[], conversationId: string): void {
  for (const material of materials) {
    for (const owner of [material.conversation_id, material.conversationId]) {
      if (owner === undefined || owner === null) continue;
      if (typeof owner !== "string" || owner.trim() !== conversationId) {
        throw new TypeError("目标元数据与当前会话不一致");
      }
    }
  }
}

export function buildSupervisorSubmission(
  input: SupervisorSubmissionInput,
  expectedContextVersion: number,
): SupervisorSubmission {
  const conversationId = normalizeRequiredId(input.conversationId, "会话标识不能为空");
  const clientInputId = normalizeRequiredId(input.clientInputId, "客户端输入标识不能为空");
  if (typeof input.content !== "string") throw new TypeError("输入内容格式不合法");
  if (!Number.isInteger(expectedContextVersion) || expectedContextVersion < 0) {
    throw new TypeError("会话上下文版本不合法");
  }

  const materials = input.materials.map(cloneMaterial);
  if (input.content.trim().length === 0 && materials.length === 0) {
    throw new TypeError("输入内容和素材不能同时为空");
  }
  validateMaterialOwners(materials, conversationId);
  const replyToMessageId = collectReplyToMessageId(input.replyToMessageId, materials);
  const artifactRefs = collectArtifactRefs(input.artifactRefs, materials);
  const interruptId = optionalId(input.interruptId);

  if (interruptId) {
    return {
      kind: "interrupt",
      interruptId,
      request: {
        client_response_id: clientInputId,
        value: {
          content: input.content,
          materials,
          reply_to_message_id: replyToMessageId,
          artifact_refs: artifactRefs,
        },
      },
    };
  }

  return {
    kind: "turn",
    request: {
      client_input_id: clientInputId,
      content: input.content,
      materials,
      reply_to_message_id: replyToMessageId,
      artifact_refs: artifactRefs,
      expected_context_version: expectedContextVersion,
    },
  };
}
