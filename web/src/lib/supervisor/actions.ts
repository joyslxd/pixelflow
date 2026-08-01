import {
  ACTION_VALUES,
  type AgentAction,
  type ExplicitActionSignal,
  type JsonObject,
  type JsonValue,
} from "./contracts.js";

export interface SupervisorWorkflowActionInput {
  action: AgentAction;
  intent?: "video" | null;
  workflowId?: string | null;
  stage?: string | null;
  artifactRef?: string | null;
  patch?: Readonly<Record<string, unknown>> | null;
}

export class SupervisorActionValidationError extends Error {
  readonly reasonCode: string;

  constructor(reasonCode: string) {
    super(reasonCode);
    this.name = "SupervisorActionValidationError";
    this.reasonCode = reasonCode;
  }
}

const TARGETED_WORKFLOW_ACTIONS = new Set<AgentAction>([
  "continue_workflow",
  "modify_workflow",
  "regenerate_stage",
  "retry_failed",
  "switch_workflow",
  "cancel_workflow",
]);
const READ_ONLY_ACTIONS = new Set<AgentAction>(["answer_only", "clarify"]);
const ARTIFACT_REF_PATTERN = /^artifact:\S+$/u;

function assertAction(condition: unknown, reasonCode: string): asserts condition {
  if (!condition) {
    throw new SupervisorActionValidationError(reasonCode);
  }
}

function cloneJsonValue(value: unknown, ancestors: WeakSet<object>): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    assertAction(Number.isFinite(value), "patch_invalid_json");
    return value;
  }
  assertAction(typeof value === "object" && !ancestors.has(value), "patch_invalid_json");

  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      const clone: JsonValue[] = [];
      for (let index = 0; index < value.length; index += 1) {
        assertAction(Object.hasOwn(value, index), "patch_invalid_json");
        clone.push(cloneJsonValue(value[index], ancestors));
      }
      return clone;
    }

    const prototype = Object.getPrototypeOf(value);
    assertAction(
      prototype === Object.prototype || prototype === null,
      "patch_invalid_json",
    );
    const clone: JsonObject = {};
    for (const key of Reflect.ownKeys(value)) {
      assertAction(typeof key === "string", "patch_invalid_json");
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      assertAction(
        descriptor !== undefined
          && descriptor.enumerable
          && Object.hasOwn(descriptor, "value"),
        "patch_invalid_json",
      );
      Object.defineProperty(clone, key, {
        value: cloneJsonValue(descriptor.value, ancestors),
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
    return clone;
  } finally {
    ancestors.delete(value);
  }
}

function cloneJsonObject(value: unknown): JsonObject {
  try {
    assertAction(
      value !== null && typeof value === "object" && !Array.isArray(value),
      "patch_invalid_json",
    );
    const clone = cloneJsonValue(value, new WeakSet());
    assertAction(
      !Array.isArray(clone) && clone !== null && typeof clone === "object",
      "patch_invalid_json",
    );
    return clone;
  } catch (error) {
    if (error instanceof SupervisorActionValidationError) throw error;
    throw new SupervisorActionValidationError("patch_invalid_json");
  }
}

function normalizeOptionalText(value: unknown, reasonCode: string): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  assertAction(typeof value === "string", reasonCode);
  return value.trim() || null;
}

export function buildSupervisorWorkflowAction(
  input: SupervisorWorkflowActionInput,
): ExplicitActionSignal {
  assertAction(
    (ACTION_VALUES as readonly unknown[]).includes(input.action),
    "action_invalid",
  );
  const intent = input.intent ?? null;
  assertAction(intent === null || intent === "video", "intent_invalid");
  const workflowId = normalizeOptionalText(input.workflowId, "workflow_id_invalid");
  const stage = normalizeOptionalText(input.stage, "stage_invalid");
  const artifactRef = normalizeOptionalText(input.artifactRef, "artifact_ref_invalid");
  assertAction(
    artifactRef === null || ARTIFACT_REF_PATTERN.test(artifactRef),
    "artifact_ref_invalid",
  );
  const patch = cloneJsonObject(input.patch ?? {});

  if (input.action === "start_workflow") {
    assertAction(
      intent === "video" && workflowId === null,
      "start_workflow_target_invalid",
    );
  } else if (READ_ONLY_ACTIONS.has(input.action)) {
    assertAction(Object.keys(patch).length === 0, "read_only_action_patch_forbidden");
  } else if (TARGETED_WORKFLOW_ACTIONS.has(input.action)) {
    assertAction(workflowId !== null, "workflow_id_required");
  }

  return {
    action: input.action,
    intent,
    workflow_id: workflowId,
    stage,
    artifact_ref: artifactRef,
    patch,
  };
}
