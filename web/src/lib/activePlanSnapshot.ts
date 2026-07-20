interface ActivePlanAssetManifest {
  characters: ActivePlanAssetManifestItem[];
  scenes: ActivePlanAssetManifestItem[];
  props: ActivePlanAssetManifestItem[];
}

interface ActivePlanAssetManifestItem extends Record<string, unknown> {
  asset_id: string;
  name: string;
  description: string;
  three_view_prompt?: string;
  image_prompt?: string;
}

interface ActivePlanSceneBlueprint extends Record<string, unknown> {
  scene_id: string;
  scene_index: number;
  title: string;
  structure_role: string;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  storyline: string;
  shot_description: string;
  narration: string;
  transition: string;
  asset_requirements: {
    characters: string[];
    scenes: string[];
    props: string[];
  };
}

export interface ActivePlanHistoryEntry {
  version: number;
  plan_markdown: string;
  restored_from_version?: number;
  change_source?: string;
  creation_contract?: Record<string, unknown>;
  scene_durations_sec?: number[];
  scene_blueprints?: ActivePlanSceneBlueprint[];
  asset_manifest?: ActivePlanAssetManifest;
}

export interface ActivePlanSnapshot {
  selected_direction: unknown;
  plan_markdown: string;
  plan_version: number;
  plan_history: ActivePlanHistoryEntry[];
  creation_contract: Record<string, unknown>;
  scene_durations_sec: number[];
  scene_blueprints: ActivePlanSceneBlueprint[];
  asset_manifest: ActivePlanAssetManifest;
  restored_from_version: number | null;
}

interface PlanSnapshotMessage {
  id: string;
  conversationId?: string;
  artifact?: {
    type?: string;
    selectedDirection?: unknown;
    plan?: {
      plan_markdown: string;
      plan_version: number;
      plan_history: ActivePlanHistoryEntry[];
      creation_contract: Record<string, unknown>;
      scene_durations_sec: number[];
      scene_blueprints?: ActivePlanSceneBlueprint[];
      asset_manifest: ActivePlanAssetManifest;
      restored_from_version: number | null;
    };
  };
}

function snapshotClone<T>(value: T): T {
  return value === undefined ? value : structuredClone(value);
}

/** 从当前对话最后一条 Plan 卡片派生可恢复的权威 Plan 上下文。 */
export function activePlanSnapshotForConversation(
  messages: readonly PlanSnapshotMessage[],
  conversationId: string,
  excludedMessageIds?: ReadonlySet<string>,
): ActivePlanSnapshot | Record<string, never> {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (excludedMessageIds?.has(message.id)) continue;
    if ((message.conversationId || conversationId) !== conversationId) continue;
    const artifact = message.artifact;
    if (artifact?.type !== "plan" || !artifact.plan) continue;
    const plan = artifact.plan;
    return {
      selected_direction: snapshotClone(artifact.selectedDirection),
      plan_markdown: plan.plan_markdown,
      plan_version: plan.plan_version,
      plan_history: snapshotClone(plan.plan_history),
      creation_contract: snapshotClone(plan.creation_contract),
      scene_durations_sec: snapshotClone(plan.scene_durations_sec),
      scene_blueprints: snapshotClone(plan.scene_blueprints || []),
      asset_manifest: snapshotClone(plan.asset_manifest || { characters: [], scenes: [], props: [] }),
      restored_from_version: plan.restored_from_version,
    };
  }
  return {};
}
