export interface VideoAgentWorkspaceAsset {
  artifactRef: string;
  mediaType: "image" | "video" | "audio" | "file";
  url: string | null;
  sceneId: string | null;
}

export interface VideoAgentScriptEvidence {
  artifactRef: string;
  version: number;
  status: string;
  content: string;
  reviewRequired: boolean;
  source: string | null;
}

export interface VideoAgentScriptStageEvidence {
  stageId: string;
  title: string;
  content: string;
  artifactRef: string | null;
  changeSummary: string | null;
}

export interface VideoAgentSceneVariant {
  variantId: string;
  artifactRef: string;
  videoUrl: string | null;
  selected: boolean;
  reviewStatus: string;
  completedAt: string | null;
}

export interface VideoAgentSceneEvidence {
  sceneId: string;
  sceneIndex: number;
  title: string;
  mediaUrl: string | null;
  artifactRefs: string[];
  issues: string[];
  repairSuggestion: string | null;
  variants: VideoAgentSceneVariant[];
  editStatus: string | null;
  regeneratedAt: string | null;
}

export interface VideoAgentWorkspaceProjection {
  workspaceId: string;
  conversationId: string;
  revision: number;
  scenes: VideoAgentSceneEvidence[];
  assets: VideoAgentWorkspaceAsset[];
  script: VideoAgentScriptEvidence | null;
  scriptStages: VideoAgentScriptStageEvidence[];
}

export interface VideoWorkspaceProjectionState {
  conversationId: string;
  current: VideoAgentWorkspaceProjection | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new TypeError(`${field}不合法`);
  }
  return value.trim();
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : null;
}

function artifactRef(value: unknown): string | null {
  const normalized = optionalText(value);
  return normalized && /^artifact:[A-Za-z0-9._:-]+$/u.test(normalized)
    ? normalized
    : null;
}

function safeMediaUrl(value: unknown): string | null {
  const normalized = optionalText(value);
  if (!normalized) return null;
  try {
    const parsed = new URL(normalized);
    return parsed.protocol === "https:"
      && parsed.username === ""
      && parsed.password === ""
      && parsed.search === ""
      && parsed.hash === ""
      ? normalized
      : null;
  } catch {
    return null;
  }
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function issueSummary(value: unknown): string | null {
  if (typeof value === "string") return optionalText(value);
  if (!isRecord(value)) return null;
  return optionalText(value.message)
    ?? optionalText(value.description)
    ?? optionalText(value.type);
}

function projectVariant(value: Record<string, unknown>): VideoAgentSceneVariant | null {
  const variantId = optionalText(value.variant_id);
  const reference = artifactRef(value.artifact_ref);
  if (!variantId || !reference) return null;
  return {
    variantId,
    artifactRef: reference,
    videoUrl: safeMediaUrl(value.video_url),
    selected: value.selected === true,
    reviewStatus: optionalText(value.review_status) ?? "pending",
    completedAt: optionalText(value.completed_at),
  };
}

function projectScene(
  value: Record<string, unknown>,
  qcByScene: Record<string, unknown>,
): VideoAgentSceneEvidence {
  const sceneId = requiredText(value.scene_id, "scene_id");
  const sceneIndex = value.scene_index;
  if (!Number.isSafeInteger(sceneIndex) || (sceneIndex as number) < 1) {
    throw new TypeError("scene_index不合法");
  }
  const variants = records(value.variants)
    .map(projectVariant)
    .filter((item): item is VideoAgentSceneVariant => item !== null);
  const approvedVariantId = optionalText(value.approved_variant_id);
  const selectedVariant = variants.find((item) => (
    item.variantId === approvedVariantId && item.selected
  )) ?? variants.find((item) => item.selected) ?? null;
  const qc = isRecord(qcByScene[sceneId]) ? qcByScene[sceneId] : {};
  const qcIssues = Array.isArray(qc.issues)
    ? qc.issues.map(issueSummary).filter((item): item is string => item !== null)
    : [];
  const references = new Set<string>();
  for (const variant of variants) references.add(variant.artifactRef);
  if (Array.isArray(qc.evidence_refs)) {
    for (const value of qc.evidence_refs) {
      const reference = artifactRef(value);
      if (reference) references.add(reference);
    }
  }
  return {
    sceneId,
    sceneIndex: sceneIndex as number,
    title: optionalText(value.title)
      ?? optionalText(value.storyline)
      ?? optionalText(value.description)
      ?? `分镜${sceneIndex as number}`,
    mediaUrl: selectedVariant?.videoUrl ?? safeMediaUrl(value.video_url),
    artifactRefs: [...references],
    issues: qcIssues,
    repairSuggestion: optionalText(qc.repair_suggestion),
    variants,
    editStatus: optionalText(value.edit_status),
    regeneratedAt: optionalText(value.regenerated_at),
  };
}

function projectAsset(value: Record<string, unknown>): VideoAgentWorkspaceAsset | null {
  const reference = artifactRef(value.artifact_ref);
  const mediaType = value.media_type;
  if (!reference || !["image", "video", "audio", "file"].includes(String(mediaType))) {
    return null;
  }
  return {
    artifactRef: reference,
    mediaType: mediaType as VideoAgentWorkspaceAsset["mediaType"],
    url: safeMediaUrl(value.url),
    sceneId: optionalText(value.scene_id),
  };
}

function projectScript(value: unknown): VideoAgentScriptEvidence | null {
  if (!isRecord(value)) return null;
  const content = optionalText(value.content);
  const reference = artifactRef(value.artifact_ref);
  const version = value.version;
  if (!content || !reference || !Number.isSafeInteger(version) || (version as number) < 1) {
    return null;
  }
  return {
    artifactRef: reference,
    version: version as number,
    status: optionalText(value.status) ?? "draft",
    content,
    reviewRequired: value.review_required === true,
    source: optionalText(value.source),
  };
}

const SCRIPT_PIPELINE_STAGE_ORDER = [
  "start",
  "plan",
  "characters",
  "outline",
  "episode",
  "review",
  "compliance",
  "export",
] as const;

function projectScriptStages(value: unknown): VideoAgentScriptStageEvidence[] {
  if (!isRecord(value)) return [];
  const stages: VideoAgentScriptStageEvidence[] = [];
  for (const stageId of SCRIPT_PIPELINE_STAGE_ORDER) {
    const raw = value[stageId];
    if (!isRecord(raw)) continue;
    const content = optionalText(raw.content);
    if (!content) continue;
    stages.push({
      stageId,
      title: optionalText(raw.title) ?? stageId,
      content,
      artifactRef: artifactRef(raw.artifact_ref),
      changeSummary: optionalText(raw.change_summary),
    });
  }
  return stages;
}

export function projectVideoWorkspaceSnapshot(
  value: unknown,
  expectedConversationId: string,
): VideoAgentWorkspaceProjection {
  if (!isRecord(value) || !isRecord(value.payload)) {
    throw new TypeError("VideoAgent工作区快照不合法");
  }
  const conversationId = requiredText(value.conversation_id, "conversation_id");
  if (conversationId !== expectedConversationId) {
    throw new TypeError("VideoAgent工作区不属于当前对话");
  }
  const revision = value.revision;
  if (!Number.isSafeInteger(revision) || (revision as number) < 1) {
    throw new TypeError("VideoAgent工作区revision不合法");
  }
  const qcByScene = isRecord(value.payload.qc) ? value.payload.qc : {};
  const scenes = records(value.payload.scenes)
    .map((scene) => projectScene(scene, qcByScene))
    .sort((left, right) => left.sceneIndex - right.sceneIndex);
  if (new Set(scenes.map((scene) => scene.sceneId)).size !== scenes.length) {
    throw new TypeError("VideoAgent工作区包含重复scene_id");
  }
  return {
    workspaceId: requiredText(value.workspace_id, "workspace_id"),
    conversationId,
    revision: revision as number,
    scenes,
    assets: records(value.payload.assets)
      .map(projectAsset)
      .filter((item): item is VideoAgentWorkspaceAsset => item !== null),
    script: projectScript(value.payload.script),
    scriptStages: projectScriptStages(value.payload.script_pipeline),
  };
}

export function createVideoWorkspaceProjectionState(
  conversationId: string,
): VideoWorkspaceProjectionState {
  return {
    conversationId: requiredText(conversationId, "conversation_id"),
    current: null,
  };
}

export function cloneVideoWorkspaceProjectionState(
  value: unknown,
  conversationId: string,
): VideoWorkspaceProjectionState {
  if (!isRecord(value) || value.conversationId !== conversationId) {
    throw new TypeError("VideoAgent工作区投影不合法");
  }
  const current = value.current;
  if (current === null) return createVideoWorkspaceProjectionState(conversationId);
  if (!isRecord(current)) throw new TypeError("VideoAgent工作区投影不合法");
  return applyVideoWorkspaceSnapshot(
    createVideoWorkspaceProjectionState(conversationId),
    projectVideoWorkspaceSnapshot({
      workspace_id: current.workspaceId,
      conversation_id: current.conversationId,
      revision: current.revision,
      payload: {
        scenes: Array.isArray(current.scenes)
          ? current.scenes.map((scene) => isRecord(scene) ? {
            scene_id: scene.sceneId,
            scene_index: scene.sceneIndex,
            title: scene.title,
            video_url: scene.mediaUrl,
            variants: Array.isArray(scene.variants)
              ? scene.variants.map((variant) => isRecord(variant) ? {
                variant_id: variant.variantId,
                artifact_ref: variant.artifactRef,
                video_url: variant.videoUrl,
                selected: variant.selected,
                review_status: variant.reviewStatus,
                completed_at: variant.completedAt,
              } : variant)
              : [],
            edit_status: scene.editStatus,
            regenerated_at: scene.regeneratedAt,
          } : scene)
          : [],
        assets: Array.isArray(current.assets)
          ? current.assets.map((asset) => isRecord(asset) ? {
            artifact_ref: asset.artifactRef,
            media_type: asset.mediaType,
            url: asset.url,
            scene_id: asset.sceneId,
          } : asset)
          : [],
        script: isRecord(current.script) ? {
          artifact_ref: current.script.artifactRef,
          version: current.script.version,
          status: current.script.status,
          content: current.script.content,
          review_required: current.script.reviewRequired,
          source: current.script.source,
        } : null,
        script_pipeline: Array.isArray(current.scriptStages)
          ? Object.fromEntries(
            current.scriptStages
              .filter(isRecord)
              .map((stage) => [
                stage.stageId,
                {
                  stage: stage.stageId,
                  title: stage.title,
                  content: stage.content,
                  artifact_ref: stage.artifactRef,
                  change_summary: stage.changeSummary,
                },
              ]),
          )
          : {},
        qc: Array.isArray(current.scenes)
          ? Object.fromEntries(current.scenes.flatMap((scene) => (
            isRecord(scene) && typeof scene.sceneId === "string"
              ? [[scene.sceneId, {
                issues: Array.isArray(scene.issues) ? scene.issues : [],
                repair_suggestion: scene.repairSuggestion,
                evidence_refs: Array.isArray(scene.artifactRefs) ? scene.artifactRefs : [],
              }]]
              : []
          )))
          : {},
      },
    }, conversationId),
  );
}

export function applyVideoWorkspaceSnapshot(
  state: VideoWorkspaceProjectionState,
  snapshot: VideoAgentWorkspaceProjection,
): VideoWorkspaceProjectionState {
  if (snapshot.conversationId !== state.conversationId) return state;
  if (state.current && snapshot.revision <= state.current.revision) return state;
  return { ...state, current: snapshot };
}

export function selectVideoAssetPackage(
  state: VideoWorkspaceProjectionState,
): VideoAgentWorkspaceProjection | null {
  return state.current;
}

export function selectSceneEvidence(
  state: VideoWorkspaceProjectionState,
  sceneId: string,
): { revision: number; scene: VideoAgentSceneEvidence } | null {
  const current = state.current;
  if (!current) return null;
  const scene = current.scenes.find((item) => item.sceneId === sceneId);
  return scene ? { revision: current.revision, scene } : null;
}

export function resolveSelectedSceneId(
  state: VideoWorkspaceProjectionState,
  requestedSceneId: string | null,
): string | null {
  const scenes = state.current?.scenes ?? [];
  if (
    requestedSceneId
    && scenes.some((scene) => scene.sceneId === requestedSceneId)
  ) return requestedSceneId;
  return scenes[0]?.sceneId ?? null;
}
