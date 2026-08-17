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
  /** 本镜 generation_jobs 状态摘要，供分镜视频进度板恢复。 */
  generationJobStatuses: string[];
  /** 本镜失败 job 的公开错误，供「分镜视频」卡展示失败原因。 */
  generationFailures: Array<{
    jobId: string | null;
    status: string;
    reasonCode: string | null;
    error: string;
  }>;
}

export interface VideoAgentWorkspaceProjection {
  workspaceId: string;
  conversationId: string;
  revision: number;
  scenes: VideoAgentSceneEvidence[];
  assets: VideoAgentWorkspaceAsset[];
  script: VideoAgentScriptEvidence | null;
  scriptStages: VideoAgentScriptStageEvidence[];
  /** 完整分镜包（含提示词/旁白），供对话资产包卡片与 StoryboardPanel 使用。 */
  scenePackages: Record<string, unknown>[];
  /** 角色/场景/道具全局资产，含 three_view_prompt / image_prompt。 */
  globalAssets: Record<string, unknown> | null;
  creationContract: Record<string, unknown> | null;
  targetDurationMs: number | null;
  /** 脚本方案是否已确认（用于刷新后恢复资产包进度卡）。 */
  scriptPlanConfirmed: boolean;
  /** Workspace 内场景包 Job 摘要；刷新后判断 prepare 是否仍在跑。 */
  scenePackageJob: {
    jobId: string;
    status: string;
  } | null;
  /** 参考图生成增量进度（completed/total），供分镜与执行规划逐步刷新。 */
  sceneAssetProgress: {
    completed: number;
    total: number;
    assetId: string | null;
    assetName: string | null;
    assetType: string | null;
    ok: boolean | null;
  } | null;
  /** 分镜视频生成增量进度（completed/total）。 */
  sceneVideoProgress: {
    completed: number;
    total: number;
    sceneId: string | null;
    sceneIndex: number | null;
    ok: boolean | null;
  } | null;
  /** 合并成片 HTTPS URL；供资产包底部「查看合并后的视频」。 */
  mergedVideoUrl: string | null;
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
    mediaUrl: selectedVariant?.videoUrl
      ?? variants.find((item) => Boolean(item.videoUrl))?.videoUrl
      ?? safeMediaUrl(value.video_url),
    artifactRefs: [...references],
    issues: qcIssues,
    repairSuggestion: optionalText(qc.repair_suggestion),
    variants,
    editStatus: optionalText(value.edit_status),
    regeneratedAt: optionalText(value.regenerated_at),
    generationJobStatuses: records(value.generation_jobs)
      .map((job) => optionalText(job.status))
      .filter((item): item is string => item !== null),
    generationFailures: records(value.generation_jobs)
      .map((job) => {
        const status = optionalText(job.status)?.toLowerCase() || "";
        if (!["failed", "timeout", "expired", "error"].includes(status)) return null;
        const error = optionalText(job.error)
          || optionalText(job.message)
          || optionalText(job.reason_code)
          || "分镜视频生成失败";
        return {
          jobId: optionalText(job.job_id),
          status,
          reasonCode: optionalText(job.reason_code),
          error,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null),
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
  const version = value.version;
  if (!content || !Number.isSafeInteger(version) || (version as number) < 1) {
    return null;
  }
  // intake_draft 等种子稿可能尚未带 artifact_ref；合成稳定引用，避免右侧预览整块消失。
  const reference = artifactRef(value.artifact_ref)
    ?? `artifact:script:draft:v${version as number}`;
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

function projectScenePackages(value: unknown): Record<string, unknown>[] {
  return records(value).filter((item) => (
    typeof item.scene_id === "string"
    || typeof item.scene_index === "number"
    || typeof item.title === "string"
  ));
}

function projectGlobalAssets(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  return value;
}

function projectCreationContract(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function projectTargetDurationMs(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1_000
    ? value
    : null;
}

function httpsUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.toLowerCase().startsWith("https://") ? normalized : null;
}

/** 从 deliveries / outputs / merged_video 解析可预览成片 URL。 */
function projectMergedVideoUrl(payload: Record<string, unknown>): string | null {
  const merged = isRecord(payload.merged_video) ? payload.merged_video : null;
  if (merged) {
    const fromLegacy = httpsUrl(merged.merged_video_url) || httpsUrl(merged.video_url);
    if (fromLegacy && merged.ok !== false) return fromLegacy;
  }
  for (const key of ["outputs", "deliveries"] as const) {
    for (const item of records(payload[key])) {
      if (String(item.output_type || "") !== "mp4") continue;
      const status = String(item.status || "").toLowerCase();
      if (status && !["succeeded", "success", "completed"].includes(status)) {
        continue;
      }
      const url = httpsUrl(item.video_url) || httpsUrl(item.merged_video_url);
      if (url) return url;
    }
  }
  return null;
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
  const scenePackages = projectScenePackages(
    value.payload.scene_packages ?? value.payload.scenes,
  );
  const rawJob = isRecord(value.payload.scene_package_job)
    ? value.payload.scene_package_job
    : null;
  const jobId = rawJob ? optionalText(rawJob.job_id) : null;
  const jobStatus = rawJob ? optionalText(rawJob.status) : null;
  const rawProgress = isRecord(value.payload.scene_asset_progress)
    ? value.payload.scene_asset_progress
    : null;
  const progressCompleted = rawProgress && Number.isFinite(Number(rawProgress.completed))
    ? Number(rawProgress.completed)
    : null;
  const progressTotal = rawProgress && Number.isFinite(Number(rawProgress.total))
    ? Number(rawProgress.total)
    : null;
  const rawVideoProgress = isRecord(value.payload.scene_video_progress)
    ? value.payload.scene_video_progress
    : null;
  const videoProgressCompleted = rawVideoProgress && Number.isFinite(Number(rawVideoProgress.completed))
    ? Number(rawVideoProgress.completed)
    : null;
  const videoProgressTotal = rawVideoProgress && Number.isFinite(Number(rawVideoProgress.total))
    ? Number(rawVideoProgress.total)
    : null;
  const videoSceneIndex = rawVideoProgress && Number.isSafeInteger(Number(rawVideoProgress.scene_index))
    ? Number(rawVideoProgress.scene_index)
    : null;
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
    scenePackages,
    globalAssets: projectGlobalAssets(value.payload.global_assets),
    creationContract: projectCreationContract(value.payload.creation_contract),
    targetDurationMs: projectTargetDurationMs(value.payload.target_duration_ms),
    scriptPlanConfirmed: value.payload.script_plan_confirmed === true,
    scenePackageJob: jobId && jobStatus
      ? { jobId, status: jobStatus }
      : null,
    sceneAssetProgress: progressCompleted !== null && progressTotal !== null
      ? {
          completed: progressCompleted,
          total: progressTotal,
          assetId: optionalText(rawProgress?.asset_id) || null,
          assetName: optionalText(rawProgress?.asset_name) || null,
          assetType: optionalText(rawProgress?.asset_type) || null,
          ok: typeof rawProgress?.ok === "boolean" ? rawProgress.ok : null,
        }
      : null,
    sceneVideoProgress: videoProgressCompleted !== null && videoProgressTotal !== null
      ? {
          completed: videoProgressCompleted,
          total: videoProgressTotal,
          sceneId: optionalText(rawVideoProgress?.scene_id) || null,
          sceneIndex: videoSceneIndex && videoSceneIndex >= 1 ? videoSceneIndex : null,
          ok: typeof rawVideoProgress?.ok === "boolean" ? rawVideoProgress.ok : null,
        }
      : null,
    mergedVideoUrl: projectMergedVideoUrl(value.payload),
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
            // 克隆须保留 job 状态，否则单镜重生蒙版依赖的 busy 判定会丢。
            generation_jobs: Array.isArray(scene.generationJobStatuses)
              ? scene.generationJobStatuses.map((status) => ({ status }))
              : [],
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
        scene_packages: Array.isArray(current.scenePackages) ? current.scenePackages : [],
        global_assets: isRecord(current.globalAssets) ? current.globalAssets : null,
        creation_contract: isRecord(current.creationContract) ? current.creationContract : null,
        target_duration_ms: typeof current.targetDurationMs === "number"
          ? current.targetDurationMs
          : null,
        script_plan_confirmed: current.scriptPlanConfirmed === true,
        scene_package_job: (() => {
          const job = isRecord(current.scenePackageJob) ? current.scenePackageJob : null;
          const jobId = job ? optionalText(job.jobId) : null;
          const status = job ? optionalText(job.status) : null;
          return jobId && status ? { job_id: jobId, status } : null;
        })(),
        scene_asset_progress: (() => {
          const progress = isRecord(current.sceneAssetProgress) ? current.sceneAssetProgress : null;
          if (!progress) return null;
          const completed = Number(progress.completed);
          const total = Number(progress.total);
          if (!Number.isFinite(completed) || !Number.isFinite(total)) return null;
          return {
            completed,
            total,
            asset_id: optionalText(progress.assetId) || "",
            asset_name: optionalText(progress.assetName) || "",
            asset_type: optionalText(progress.assetType) || "",
            ok: typeof progress.ok === "boolean" ? progress.ok : null,
          };
        })(),
        scene_video_progress: (() => {
          const progress = isRecord(current.sceneVideoProgress) ? current.sceneVideoProgress : null;
          if (!progress) return null;
          const completed = Number(progress.completed);
          const total = Number(progress.total);
          if (!Number.isFinite(completed) || !Number.isFinite(total)) return null;
          return {
            completed,
            total,
            scene_id: optionalText(progress.sceneId) || "",
            scene_index: typeof progress.sceneIndex === "number" ? progress.sceneIndex : null,
            ok: typeof progress.ok === "boolean" ? progress.ok : null,
          };
        })(),
        // 克隆时必须带回成片 URL，否则 Snapshot 经 reducer 二次投影后按钮/成品卡全丢。
        merged_video: (() => {
          const url = typeof current.mergedVideoUrl === "string"
            ? current.mergedVideoUrl.trim()
            : "";
          if (!url.toLowerCase().startsWith("https://")) return null;
          return {
            ok: true,
            merged_video_url: url,
          };
        })(),
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
  // 同会话换了 workspace 身份时必须替换，即使 revision 数字相同或更小。
  if (
    state.current
    && state.current.workspaceId === snapshot.workspaceId
    && snapshot.revision <= state.current.revision
  ) {
    return state;
  }
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
