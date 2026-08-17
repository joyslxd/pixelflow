import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { GenParamsDialog, type CreationIntent, type GenParamsForm } from "@/components/composer/GenParamsDialog";
import { PlanRevisionDialog, type PlanRevisionMode } from "@/components/composer/PlanRevisionDialog";
import {
  ApiError,
  api,
  setActiveConversationId as setActiveConversationIdForTrace,
  subscribeTaskEvents,
  type ConversationDetailResponse,
  type ConversationMessageResponse,
  type ConversationMessageJobStatusResponse,
  type CreativeDirectionResponse,
  type CreativeDirectionsResponse,
  type ImageEditModelSelection,
  type ImageAssetEditResponse,
  type ImageAssetFusionResponse,
  type ImageGenerateResponse,
  type ImageModelParamConfig,
  type ImagePrepareResponse,
  type IntakeIntentResponse,
  type JianyingDraftCapability,
  type JianyingDraftJobResponse,
  type JianyingDraftStartRequest,
  type MergeSceneVideosResponse,
  type PlanManualEditRequest,
  type PlanMarkdownResponse,
  type PptFileResult,
  type PptImagesResult,
  type PptJobStatusResponse,
  type PptPageImage,
  type PptContentJsonResult,
  type PptSummaryResult,
  type GenerateSceneAssetsResponse,
  type GenerateSceneVideosJobStatusResponse,
  type GenerateSceneVideosResponse,
  type PrepareScenePackagesJobResult,
  type PrepareScenePackagesJobStatusResponse,
  type PrepareScenePackagesResponse,
  type ScenePackageAssetRevisionRequest,
  type ScenePackageAssetRevisionResponse,
  type SceneGenerationPayload,
  type SceneVideoPayload,
  type TaskEvent,
  type VideoCreationContract,
} from "@/lib/api";
import type { ChatMessage, CanvasState, Brief, BriefShot, JianyingDraftRecordMap } from "@/lib/chat";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import { activePlanSnapshotForConversation } from "@/lib/activePlanSnapshot";
import { preferredSceneAssetImageSize,
  SCENE_ASSET_PREFERRED_MODELS,
  sceneAssetModelLabel,
} from "@/lib/sceneAssetModelSelection";
import { enrichFailedSceneForDisplay } from "@/lib/sceneVideoFailures";
import {
  remapMessageAnchorId,
  resolveAssetPackageProgressAnchorId,
} from "@/lib/assetPackageProgressAnchor";
import { resolveVideoAgentPlanAnchorId } from "@/lib/videoAgentPlanAnchor";
import {
  classifyScenePackageJobResume,
  scenePackageJobResumeDelayMs,
} from "@/lib/scenePackageJobResume";
import {
  hasMediaResultMessage,
  isSceneAssetGenerationMaterialized,
  mediaResultClientMessageId,
  markConfirmedSceneAssetModelOptions,
  preferredVideoScenePackagesMessageIndex,
  reconcileStaleSceneAssetUiFlags,
  resolveVideoScenePackagesForRestore,
  scenePackageHasGeneratedImages,
} from "@/lib/scenePackageAssetUi";
import {
  isPendingPlanSaveForConversation,
  isSameMessageJobGeneration,
  planContextFromSavedMessage,
  planMessageResumeDelayMs,
  resumePlanMessageJobStep,
} from "@/lib/planMessageRecovery";
import {
  classifyPlanJobResume,
  clearPendingPlanJobRecovery,
  continueStartedPlanJob,
  loadPendingPlanJobRecovery,
  planJobResumeDelayMs,
  savePendingPlanJobRecovery,
  shouldRetryPlanJobPersistence,
} from "@/lib/planJobRecovery";

const PlanMarkdownEditor = lazy(() =>
  import("@/components/canvas/PlanMarkdownEditor").then((module) => ({ default: module.PlanMarkdownEditor })),
);
import {
  appendVisibleConversationMessage,
  messageConversationId,
  replaceMessageById,
  restoredConversationMessages,
  shouldApplyVisibleConversationSideEffect,
} from "@/lib/conversationRouting";
import { useSupervisorConversation } from "@/hooks/useSupervisorConversation";
import {
  AgentTurnGroup,
  selectNativeAgentTurns,
} from "@/features/native-video-agent";
import { AgentPlanTimeline } from "@/features/video-agent/AgentPlanTimeline";
import {
  AgentPipelineProgress,
  applyAssetPackageAssetProgress,
  applyAssetPackageStructureProgress,
  applyAssetPackageJobStage,
  applySceneVideoProgress,
  createAssetPackageProgressSteps,
  createSceneVideoProgressSteps,
  failAssetPackageProgressSteps,
  resolveNativeSceneVideoBatchTotal,
  type AgentPipelineProgressStep,
} from "@/features/video-agent/AgentPipelineProgress";
import {
  AgentConfirmationCard,
  type AgentConfirmationSubmission,
} from "@/features/video-agent/AgentConfirmationCard";
import {
  AgentQuotaCard,
  type AgentQuotaSubmission,
} from "@/features/video-agent/AgentQuotaCard";
import { AgentThinkingStream, type AgentThinkingStreamModel } from "@/features/video-agent/AgentThinkingStream";
import { resolveThinkingAfterMessageId } from "@/features/video-agent/thinkingAnchor";
import { AgentScriptPreviewPanel } from "@/features/video-agent/AgentScriptPreviewPanel";
import { VideoAgentStoryboardSurface } from "@/features/video-agent/VideoAgentStoryboardSurface";
import {
  ArtifactCanvasRouter,
  ScenePackageCanvas,
  resolveCanvasKindFromArtifact,
} from "@/features/native-video-agent/canvas";
import { useVideoAgent } from "@/features/video-agent/hooks/useVideoAgent";
import {
  emptyVideoAgentPlanHistory,
  loadVideoAgentPlanHistory,
  mergeVideoAgentPlanHistory,
  saveVideoAgentPlanHistory,
  type VideoAgentPlanHistory,
} from "@/features/video-agent/planHistory";
import { stageIdFromStep, resolveGeneratableScriptMarkdown, buildAssetPackagePlanMarkdown, extractConcreteProductHint, workspaceHasExportReady, analyzeScriptCharacterReadiness, scriptNeedsFullCharacterPlan, isScriptCreativeConfirmationTitle, isAgreeScriptCreativeRequest, isCancelScriptCreativeRequest, creativeConfirmNeedsClarification } from "@/features/video-agent/scriptSkillStages";
import { buildImageRevisionPreparePayload, canAcceptImageResult, imageResultSummary } from "@/lib/imageReview";
import { isReviewExpired, reviewExpiresAt, timeoutReviewMessage } from "@/lib/reviewWindow";
import {
  addGlobalSceneAssetReference,
  DEFAULT_TARGET_DURATION_MS,
  defaultGlobalSceneAssetRatio,
  globalSceneAssetRatioFromMetadata,
  inferGlobalSceneAssetRatioFromMetadata,
  nearestSupportedAspectRatio,
  sceneAssetRetryTargets,
  sceneGenerationPayloadFromPackage,
  sceneIdsForRevision,
  scenePackagesWithRevisionContract,
  scenePackagesWithoutRevisionContract,
  updateScenePackageField,
  uploadedReferenceMaterials,
  type GlobalSceneAssetGroup,
  type SceneGlobalAssetReference,
  type SceneGlobalAssetReplacement,
  type SceneAssetRetryTarget,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import {
  imageModelCapabilities,
  preferredImageRatio,
  preferredImageSize,
  resolveImageModel,
} from "@/lib/videoRequirementConfig";
import { formatMessageTime } from "@/lib/time";
import type { FlowTimelineEntry, TaskPhase, VideoResult } from "@/lib/types";
import type { SceneGlobalAssetEditReview } from "@/lib/chat";
import {
  isJianyingDraftSucceededResultValid,
  jianyingDraftPublicErrorMessage,
  JianyingDraftStartGuard,
  patchJianyingDraftTargetConversation,
  storyboardVersionId,
  type JianyingDraftScene,
} from "@/lib/jianyingDraft";
import {
  deriveWorkflowTaskBoard,
  deriveWorkflowTaskBoardFromAgentPlan,
  type WorkflowFlowKind,
  type WorkflowProgressSnapshot,
} from "@/lib/workflowTaskBoard";
import type {
  AgentAction,
  ExplicitActionSignal,
  JsonObject,
  JsonValue,
  OrchestrationMode,
  TurnStartRequest,
  WorkflowRecord,
} from "@/lib/supervisor/contracts";
import { buildSupervisorWorkflowAction } from "@/lib/supervisor/actions";
import {
  createConversationWriteSequencer,
  resolveAssistHandoffAction,
  resolveUnavailableSupervisorRecovery,
  resolveWorkspaceAgentRuntimeMode,
  resolveWorkspaceOrchestrationMode,
  resolveWorkspacePrimaryExecutionReady,
  resolveWorkspaceInteractionPolicy,
  resolveWorkspaceRuntimePolicy,
  type WorkspaceAgentRuntimeMode,
} from "@/lib/supervisor/legacyAdapter";
import { resolveSupervisorRuntimeNotice } from "@/lib/supervisor/runtimeNotice";
import { buildSupervisorSubmission } from "@/lib/supervisor/turnSubmission";
import { supervisorApi, SupervisorApiError } from "@/lib/supervisor/api";
import {
  mergeSupervisorMessagesWithPending,
  selectSupervisorArtifactMessage,
} from "@/lib/supervisor/workspaceProjection";

import type {
  ChatArtifact,
  ConversationOwnership,
  DeferredOwnershipInput,
  FlowDraft,
  FlowDraftStage,
  PendingConversationArtifact,
  PendingDirectionJob,
  PendingDirectionJobContext,
  PendingHandleSendMessageContinuation,
  PendingImageEditRequest,
  PendingImageJob,
  PendingIntakeJob,
  PendingJianyingDraftJob,
  PendingMessageJob,
  PendingPlanJob,
  PendingPlanRevisionChoice,
  PendingPlanSaveMessageContinuation,
  PendingPptJob,
  PendingScenePackageJob,
  PendingSupervisorTurn,
  PendingVideoJob,
  PrepareScenePackagesJobRequest,
  RegisteredSupervisorTurn,
  RestoredSupervisorVideoUi,
  SceneAssetsJobRequest,
  SendRuntimeOptions,
  SubmitSupervisorActionOptions,
  SupervisorVideoTarget,
  WorkspaceSnapshot,
} from "./legacyWorkspaceTypes";
import {
  applyImageEditConfirmedSelectionsToMessages,
  AUTO_CONFIRM_TIMEOUT_MS,
  AUTO_CONFIRM_TIMEOUT_SECONDS,
  browserSessionStorage,
  buildVideoAgentCreationContract,
  canReuseUneditedSceneVideos,
  CONTENT_APP_CONVERSATIONS_UPDATED_MESSAGE_TYPE,
  creativeDirectionsFingerprint,
  dedupeRestoredScenePackageMessages,
  DEFAULT_IMAGE_EDIT_MODEL_CONFIG,
  directImageEditFormValues,
  EMPTY_CANVAS,
  EVENT_FALLBACK_TITLE,
  EXPLAINABLE_EVENT_NAMES,
  failedSceneIdsFromGeneratedSceneVideos,
  formatSceneIndexesForMessage,
  hasLaterDirectionSuccessor,
  hasMaterializedMessageJob,
  hasMaterializedPlanJob,
  hasMaterializedScenePackageJob,
  hasPassedRequirementCollection,
  hasPostDirectionArtifactForContext,
  hasPostDirectionArtifactForDirections,
  imageEditRequestFromArtifact,
  inferVideoDurationSecFromScript,
  isCreationIntent,
  isDirectionSuccessorArtifact,
  isImageEditIntake,
  isJsonObject,
  isRecoverablePendingPlanJob,
  isTransientFetchAbort,
  JIANYING_DRAFT_CLIENT_TIMEOUT_MS,
  latestImageResultArtifactForConversation,
  latestOriginalVideoScenePackagesForConversation,
  latestPptRevisionRequestedForConversation,
  latestScenePackageSnapshotForConversation,
  latestVideoResultArtifactForConversation,
  LEGACY_VIDEO_JOB_CONTINUE_TIP,
  LEGACY_VIDEO_JOB_HTTP_REMOVED,
  isNoRefImageContinueRequest,
  markLatestImageResultAcceptedFromContext,
  markLatestPptFileDoneFromContext,
  markLatestVideoResultAcceptedFromContext,
  mergeMaterials,
  mergePartialGeneratedSceneVideos,
  mergeProjectedMessagesWithLocalCards,
  messageFromResponse,
  normalizeMaterialStoryboardReferences,
  normalizeRestoredMessageReferences,
  now,
  parseExplicitAction,
  parsePendingSupervisorTurn,
  parseRegisteredSupervisorTurn,
  pendingPlanJobPersistenceContext,
  pendingPlanJobRunningPhase,
  PHASE_MSG,
  pptContentPages,
  pptImageFileUrls,
  pptPageJson,
  pptProjectId,
  processedArtifactKey,
  quotaMessage,
  restoreLatestVideoScenePackagesFromContext,
  restorePendingMessageJobMessage,
  restoreSupervisorVideoUi,
  resolveVideoAgentCreationContract,
  REVIEW_ARTIFACT,
  SCENE_GLOBAL_ASSET_DELETE_PROMPT,
  sceneGlobalAssetReferenceFromMaterials,
  scenePackageJobMessageId,
  sceneVideoForPackageScene,
  selectedDirectionMatchesDirections,
  toBrief,
  toTimelineEntry,
  uid,
  UNAVAILABLE_SUPERVISOR_NOTICE,
  UNAVAILABLE_SUPERVISOR_NOTICE_VERSION,
  unavailableSupervisorNoticeId,
  failedSupervisorNoticeId,
  valuesFromForm,
  videoResultsFromGeneratedScenes,
  workflowIntentFromPhase,
  notifyContentAppConversationsUpdated,
  countGlobalAssetImageUrls,
} from "./legacyWorkspaceHelpers";

export function WorkspacePage() {
  const navigate = useNavigate();
  const { conversationId } = useParams<{ conversationId?: string }>();
  // 页面可渲染状态：聊天消息、右侧画布、参数弹窗、旧运行时忙碌态和 Brief 确认态。
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvas, setCanvas] = useState<CanvasState>(EMPTY_CANVAS);
  const [canvasOpen, setCanvasOpen] = useState(false);
  /** 右侧脚本预览默认收起；仅对话卡片/执行方案入口打开。 */
  const [scriptPreviewOpen, setScriptPreviewOpen] = useState(false);
  const [selectedStoryboardMessageId, setSelectedStoryboardMessageId] = useState("");
  const [selectedPlanEditorMessageId, setSelectedPlanEditorMessageId] = useState("");
  const [savingPlanEdit, setSavingPlanEdit] = useState(false);
  const [savingVideoAgentScript, setSavingVideoAgentScript] = useState(false);
  const [agentRevisionSourceMessageId, setAgentRevisionSourceMessageId] = useState("");
  const [assetPackageAnchorMessageId, setAssetPackageAnchorMessageId] = useState("");
  const assetPackageAnchorMessageIdRef = useRef("");
  const [assetPackageProgressSteps, setAssetPackageProgressSteps] = useState<AgentPipelineProgressStep[]>([]);
  const assetPackageProgressStepsRef = useRef<AgentPipelineProgressStep[]>([]);
  const [sceneVideoProgressSteps, setSceneVideoProgressSteps] = useState<AgentPipelineProgressStep[]>([]);
  /** 点击「确认并生成分镜 N」后立刻蒙版；等 Workspace job 终态再清。 */
  const [optimisticGeneratingSceneIds, setOptimisticGeneratingSceneIds] = useState<string[]>([]);
  /** 乐观蒙版挂上时的 Workspace revision；revision 未推进前不清，避免旧终态立刻抹掉蒙版。 */
  const optimisticGeneratingRevisionRef = useRef<Record<string, number>>({});
  /** 强制重投「视频场景包」卡：resume/同会话重挂清气泡后，revision 不变时 effect 不会自然重跑。 */
  const [workspaceScenePackageReprojectEpoch, setWorkspaceScenePackageReprojectEpoch] = useState(0);
  const [optimisticAgentThinking, setOptimisticAgentThinking] = useState<AgentThinkingStreamModel | null>(null);
  /** 本地仅作会话内即时归档缓存；刷新后以 Snapshot agentThinkingHistory 为准。 */
  const [agentThinkingHistory, setAgentThinkingHistory] = useState<Array<AgentThinkingStreamModel & {
    afterMessageId: string;
  }>>([]);
  /** 当前 Turn 思考打字机未追平时，延后展示本轮 Plan，避免「边想边出卡」。 */
  const [holdActivePlanForThinking, setHoldActivePlanForThinking] = useState(false);
  /** turnId(runId 或乐观 clientInputId) → 触发该轮的用户消息 id；与方案卡同锚。 */
  const thinkingTurnAnchorsRef = useRef<Record<string, string>>({});
  const thinkingAnswerNoticeInFlightRef = useRef<Set<string>>(new Set());
  const [dialogOpen, setDialogOpen] = useState(false);
  const [pendingCore, setPendingCore] = useState("");
  const [pendingIntent, setPendingIntent] = useState<CreationIntent>("video");
  const [pendingFormValues, setPendingFormValues] = useState<Record<string, unknown>>({});
  const [pendingMaterials, setPendingMaterials] = useState<Array<Record<string, unknown>>>([]);
  const [referencedMaterials, setReferencedMaterials] = useState<SceneGlobalAssetReference[]>([]);
  const [composerPrefillRequest, setComposerPrefillRequest] = useState<{ id: string; content: string } | null>(null);
  const [legacyBusy, setBusy] = useState(false);
  const [briefConfirmed, setBriefConfirmed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState("");
  const [pendingPlanRevisionChoice, setPendingPlanRevisionChoice] = useState<PendingPlanRevisionChoice | null>(null);
  const [jianyingDraftCapability, setJianyingDraftCapability] = useState<JianyingDraftCapability>({
    available: false,
    reason: "剪映草稿服务待接入",
    poll_interval_seconds: 2,
  });
  const [, setJianyingDraftUiRevision] = useState(0);
  const [workflowProgress, setWorkflowProgress] = useState<WorkflowProgressSnapshot | null>(null);
  const [orchestrationMode, setOrchestrationMode] = useState<OrchestrationMode>("frontend_v2");
  const [agentRuntimeMode, setAgentRuntimeMode] = useState<WorkspaceAgentRuntimeMode>("off");
  const [orchestrationResolved, setOrchestrationResolved] = useState(true);
  const [pendingSupervisorTurns, setPendingSupervisorTurns] = useState<PendingSupervisorTurn[]>([]);
  const pendingSupervisorTurnsRef = useRef<PendingSupervisorTurn[]>([]);
  const pendingSupervisorTurnsByConversationRef = useRef(
    new Map<string, PendingSupervisorTurn[]>(),
  );
  const pendingSupervisorTurnWritesRef = useRef(
    createConversationWriteSequencer(),
  );
  const orchestrationModeRef = useRef<OrchestrationMode | null>("frontend_v2");
  const agentRuntimeModeRef = useRef<WorkspaceAgentRuntimeMode | null>("off");
  const primaryExecutionReadyRef = useRef(false);
  const deferredOwnershipInputsRef = useRef<DeferredOwnershipInput[]>([]);
  const supervisorTurnInFlightRef = useRef<Set<string>>(new Set());
  // 旧 v2 接力可能持续数秒；在接力完成并写回上下文前，禁止同一 Turn 被
  // Runtime effect 因状态刷新重复执行，避免重复计费或重复启动供应商任务。
  const supervisorLegacyHandoffClaimedRef = useRef<Set<string>>(new Set());
  const unavailableSupervisorNoticeVersionsRef = useRef(
    new Map<string, number>(),
  );
  const unavailableSupervisorRecoveryInFlightRef = useRef(
    new Set<string>(),
  );
  const resolvedRuntimePolicy = resolveWorkspaceRuntimePolicy(
    orchestrationMode,
    currentConversationId,
    agentRuntimeMode,
  );
  const runtimePolicy = orchestrationResolved
    ? resolvedRuntimePolicy
    : {
      supervisorEnabled: false,
      legacyRunnerEnabled: false,
      legacyArtifactActionsEnabled: false,
    };
  const primaryExecutionUnavailable = orchestrationResolved
    && orchestrationMode === "video_agent_v2"
    && !primaryExecutionReadyRef.current;
  // 新旧运行时共用同一个页面入口；R1 assist 只挂载会话基础设施，业务仍由旧 runner 推进。
  // 空会话使用稳定占位符，保证 Hook 顺序不变且不会向后端发起请求。
  const supervisorRuntime = useSupervisorConversation(currentConversationId || "workspace-pending", {
    enabled: runtimePolicy.supervisorEnabled,
  });
  useEffect(() => {
    if (supervisorRuntime.state.agentThinking && optimisticAgentThinking) {
      setOptimisticAgentThinking(null);
    }
  }, [supervisorRuntime.state.agentThinking, optimisticAgentThinking]);
  useEffect(() => {
    // 换会话清空历史思考，避免串对话。
    setAgentThinkingHistory([]);
    setOptimisticAgentThinking(null);
    setHoldActivePlanForThinking(false);
    thinkingTurnAnchorsRef.current = {};
    thinkingAnswerNoticeInFlightRef.current.clear();
  }, [currentConversationId]);
  useEffect(() => {
    const live = supervisorRuntime.state.agentThinking ?? optimisticAgentThinking;
    if (!live) {
      setHoldActivePlanForThinking(false);
    }
  }, [supervisorRuntime.state.agentThinking, optimisticAgentThinking]);
  useEffect(() => {
    const thinking = supervisorRuntime.state.agentThinking;
    if (!thinking || thinking.status !== "completed" || !thinking.text.trim()) return;
    const afterMessageId = resolveThinkingAfterMessageId(thinking.turnId, messagesRef.current, {
      pendingTurns: pendingSupervisorTurnsRef.current,
      knownAnchor: thinkingTurnAnchorsRef.current[thinking.turnId],
    });
    if (!afterMessageId) return;
    thinkingTurnAnchorsRef.current[thinking.turnId] = afterMessageId;
    setAgentThinkingHistory((previous) => {
      const index = previous.findIndex((item) => item.turnId === thinking.turnId);
      // 已归档锚点不随「最新用户消息」漂移。
      const stableAnchor = index >= 0
        ? (previous[index].afterMessageId || afterMessageId)
        : afterMessageId;
      const nextItem = { ...thinking, afterMessageId: stableAnchor };
      if (index >= 0) {
        const copy = previous.slice();
        copy[index] = { ...nextItem, afterMessageId: previous[index].afterMessageId || afterMessageId };
        return copy;
      }
      return [...previous, nextItem];
    });
  }, [supervisorRuntime.state.agentThinking]);
  // Snapshot 恢复的思考历史：合并进本地归档，刷新后仍可回显 Thought。
  useEffect(() => {
    const restored = supervisorRuntime.state.agentThinkingHistory || [];
    if (restored.length === 0) return;
    setAgentThinkingHistory((previous) => {
      const byTurn = new Map(previous.map((item) => [item.turnId, item]));
      for (const item of restored) {
        if (!item.turnId || !item.text?.trim()) continue;
        const afterMessageId = resolveThinkingAfterMessageId(item.turnId, messagesRef.current, {
          pendingTurns: pendingSupervisorTurnsRef.current,
          knownAnchor: thinkingTurnAnchorsRef.current[item.turnId]
            || byTurn.get(item.turnId)?.afterMessageId,
          afterMessageId: item.afterMessageId || item.clientInputId || null,
        });
        if (!afterMessageId) continue;
        thinkingTurnAnchorsRef.current[item.turnId] = afterMessageId;
        const local = byTurn.get(item.turnId);
        const preferLocal = Boolean(
          local
          && (
            (local.text?.length || 0) > (item.text?.length || 0)
            || (local.answer?.length || 0) > (item.answer?.length || 0)
          ),
        );
        byTurn.set(item.turnId, preferLocal && local
          ? { ...local, afterMessageId: local.afterMessageId || afterMessageId }
          : {
            turnId: item.turnId,
            title: item.title,
            subtitle: item.subtitle,
            text: item.text,
            answer: item.answer,
            startedAt: item.startedAt,
            status: item.status === "streaming" ? "streaming" : "completed",
            afterMessageId,
          });
      }
      return [...byTurn.values()];
    });
  }, [supervisorRuntime.state.agentThinkingHistory, messages.length, currentConversationId]);
  useEffect(() => {
    if (supervisorRuntime.state.videoAgentConfirmation) {
      setOptimisticAgentThinking(null);
    }
  }, [supervisorRuntime.state.videoAgentConfirmation]);
  const [videoAgentConfirmationSubmitting, setVideoAgentConfirmationSubmitting] = useState(false);
  const [videoAgentConfirmationError, setVideoAgentConfirmationError] = useState<string | null>(null);
  const [selectedVideoAgentStepId, setSelectedVideoAgentStepId] = useState<string | null>(null);
  const [videoAgentPlanAnchors, setVideoAgentPlanAnchors] = useState<Record<string, string>>({});
  const videoAgentPlanAnchorsRef = useRef<Record<string, string>>({});
  const [videoAgentPlanHistory, setVideoAgentPlanHistory] = useState<VideoAgentPlanHistory>(emptyVideoAgentPlanHistory);
  const restoringRef = useRef(false);
  const [confirmingVideoAgentScript, setConfirmingVideoAgentScript] = useState(false);
  const visibleVideoAgentConfirmationId = supervisorRuntime.state.videoAgentConfirmation?.confirmationId ?? null;
  useEffect(() => {
    setVideoAgentConfirmationSubmitting(false);
    setVideoAgentConfirmationError(null);
  }, [currentConversationId, visibleVideoAgentConfirmationId]);
  const handleVideoAgentConfirmation = (submission: AgentConfirmationSubmission) => {
    if (
      videoAgentConfirmationSubmitting
      || submission.confirmationId !== visibleVideoAgentConfirmationId
    ) return;
    const creativeGate = isScriptCreativeConfirmationTitle(
      supervisorRuntime.state.videoAgentConfirmation?.title,
    );
    if (
      creativeGate
      && submission.decision === "confirm"
      && creativeConfirmNeedsClarification(
        supervisorRuntime.state.videoAgentConfirmation?.costSummary,
      )
    ) {
      setVideoAgentConfirmationError(
        "还缺画幅或结尾行动引导。请先在对话框回复，例如：画幅 9:16，结尾引导进直播间下单。",
      );
      pushAssistant(
        "创意方向可以，但还需要你确认视频画幅（如 9:16 / 16:9 / 1:1）和结尾引导行动（如进直播间、小黄车下单）。请直接回复这两项后再点「同意创意继续」。",
        conversationIdRef.current || currentConversationId || undefined,
      );
      return;
    }
    setVideoAgentConfirmationSubmitting(true);
    setVideoAgentConfirmationError(null);
    void supervisorRuntime.respondToVideoAgentConfirmation(
      submission.confirmationId,
      {
        step_id: submission.stepId,
        decision: submission.decision,
      },
    ).then(() => {
      if (creativeGate && submission.decision === "cancel") {
        pushAssistant(
          "已取消当前创意方向。请直接用自然语言说明想怎么改（例如加冲突、换情绪、换时空跨度），我会重新从选题开始。",
          conversationIdRef.current || currentConversationId || undefined,
        );
      }
    }).catch(() => {
      setVideoAgentConfirmationError("确认请求未完成，请刷新后重试。");
    }).finally(() => {
      setVideoAgentConfirmationSubmitting(false);
    });
  };
  const [videoAgentQuotaSubmitting, setVideoAgentQuotaSubmitting] = useState(false);
  const [videoAgentQuotaError, setVideoAgentQuotaError] = useState<string | null>(null);
  const visibleVideoAgentQuotaId = supervisorRuntime.state.videoAgentQuota?.quotaInterruptId ?? null;
  useEffect(() => {
    setVideoAgentQuotaSubmitting(false);
    setVideoAgentQuotaError(null);
  }, [currentConversationId, visibleVideoAgentQuotaId]);
  const handleVideoAgentQuota = (submission: AgentQuotaSubmission) => {
    if (
      videoAgentQuotaSubmitting
      || submission.quotaInterruptId !== visibleVideoAgentQuotaId
    ) return;
    setVideoAgentQuotaSubmitting(true);
    setVideoAgentQuotaError(null);
    void supervisorRuntime.respondToVideoAgentQuota(
      submission.quotaInterruptId,
      { decision: submission.decision },
    ).catch(() => {
      setVideoAgentQuotaError("额度恢复请求未完成，请刷新后重试。");
    }).finally(() => {
      setVideoAgentQuotaSubmitting(false);
    });
  };
  const restoredSupervisorUi = useMemo(
    () => restoreSupervisorVideoUi(supervisorRuntime.state.interrupt?.payload),
    [supervisorRuntime.state.interrupt?.payload],
  );
  const videoAgentView = useVideoAgent(
    supervisorRuntime.state.videoAgentWorkspace,
  );
  // 有脚本草稿时不再自动撑开右侧预览；仅用户从对话卡片打开。
  useEffect(() => {
    const script = videoAgentView.workspace?.script;
    if (!script?.content?.trim()) {
      setScriptPreviewOpen(false);
    }
  }, [
    videoAgentView.workspace?.script?.artifactRef,
    videoAgentView.workspace?.script?.version,
    videoAgentView.workspace?.script?.content,
  ]);
  const videoAgentCompletedStepKey = useMemo(() => {
    const plans = supervisorRuntime.state.videoAgentPlanOrder
      .map((planId) => supervisorRuntime.state.videoAgentPlans[planId])
      .filter((plan): plan is NonNullable<typeof plan> => Boolean(plan));
    if (plans.length === 0 && supervisorRuntime.state.videoAgentPlan) {
      return Object.values(supervisorRuntime.state.videoAgentPlan.steps)
        .filter((step) => step.status === "completed")
        .map((step) => step.stepId)
        .sort()
        .join(",");
    }
    return plans
      .flatMap((plan) => Object.values(plan.steps))
      .filter((step) => step.status === "completed")
      .map((step) => step.stepId)
      .sort()
      .join(",");
  }, [
    supervisorRuntime.state.videoAgentPlan,
    supervisorRuntime.state.videoAgentPlanOrder,
    supervisorRuntime.state.videoAgentPlans,
  ]);
  useEffect(() => {
    if (
      orchestrationMode !== "video_agent_v2"
      || !currentConversationId
      || !videoAgentCompletedStepKey
    ) return;
    // 步骤完成事件不含 workspace 全文；刷新 Snapshot 才能露出脚本/分镜预览。
    void supervisorRuntime.refreshSnapshot().catch(() => {});
  }, [
    currentConversationId,
    orchestrationMode,
    videoAgentCompletedStepKey,
  ]);

  // prepare_scene_packages 完成后必须拉 Snapshot，否则对话里没有「视频场景包」卡片。
  const nativePrepareCompletedKey = useMemo(() => {
    if (orchestrationMode !== "video_agent_v2") return "";
    return selectNativeAgentTurns(supervisorRuntime.nativeUiState)
      .flatMap((turn) => turn.tools)
      .filter((tool) => tool.toolName === "prepare_scene_packages" && tool.status === "completed")
      .map((tool) => `${tool.toolCallId}:${tool.completedAt || tool.publicSummary}`)
      .join("|");
  }, [orchestrationMode, supervisorRuntime.nativeUiState]);
  useEffect(() => {
    if (!nativePrepareCompletedKey || !currentConversationId) return;
    void supervisorRuntime.refreshSnapshot().catch(() => {});
  }, [currentConversationId, nativePrepareCompletedKey]);

  // 仅看「最近一次」generate_scene_assets：历史失败不得把进度卡永久钉在失败态。
  const nativeAssetsToolSignal = useMemo(() => {
    if (orchestrationMode !== "video_agent_v2") {
      return { key: "", status: "", summary: "" };
    }
    const tools = selectNativeAgentTurns(supervisorRuntime.nativeUiState)
      .flatMap((turn) => turn.tools)
      .filter((tool) => tool.toolName === "generate_scene_assets");
    const latest = tools[tools.length - 1];
    if (!latest) return { key: "", status: "", summary: "" };
    return {
      key: `${latest.toolCallId}:${latest.status}:${latest.completedAt || latest.publicSummary || ""}`,
      status: latest.status,
      summary: String(latest.publicSummary || "").trim(),
    };
  }, [orchestrationMode, supervisorRuntime.nativeUiState]);
  const nativePrepareToolSignal = useMemo(() => {
    if (orchestrationMode !== "video_agent_v2") {
      return { key: "", status: "", summary: "" };
    }
    const tools = selectNativeAgentTurns(supervisorRuntime.nativeUiState)
      .flatMap((turn) => turn.tools)
      .filter((tool) => tool.toolName === "prepare_scene_packages");
    const latest = tools[tools.length - 1];
    if (!latest) return { key: "", status: "", summary: "" };
    return {
      key: `${latest.toolCallId}:${latest.status}:${latest.completedAt || ""}`,
      status: latest.status,
      summary: String(latest.publicSummary || "").trim(),
    };
  }, [orchestrationMode, supervisorRuntime.nativeUiState]);
  const nativeGenerateScenesToolSignal = useMemo(() => {
    if (orchestrationMode !== "video_agent_v2") {
      return { key: "", status: "", summary: "" };
    }
    const tools = selectNativeAgentTurns(supervisorRuntime.nativeUiState)
      .flatMap((turn) => turn.tools)
      .filter((tool) => tool.toolName === "generate_scenes");
    const latest = tools[tools.length - 1];
    if (!latest) return { key: "", status: "", summary: "" };
    return {
      key: `${latest.toolCallId}:${latest.status}:${latest.completedAt || latest.publicSummary || ""}`,
      status: latest.status,
      summary: String(latest.publicSummary || "").trim(),
    };
  }, [orchestrationMode, supervisorRuntime.nativeUiState]);
  useEffect(() => {
    if (!nativeAssetsToolSignal.key) return;
    if (nativeAssetsToolSignal.status === "failed") {
      const detail = nativeAssetsToolSignal.summary
        || "generate_scene_assets · 参考图生成失败，请检查场景包资产后重试";
      setAssetPackageProgressSteps((current) => {
        const base = current.length > 0 ? current : createAssetPackageProgressSteps();
        return applyAssetPackageJobStage(base, "generate_scene_assets_failed").map((step) => (
          step.id === "assets"
            ? { ...step, detail: detail.slice(0, 280) }
            : step
        ));
      });
      // 失败后清假「生成中」，并解锁选模卡，否则用户无法再点确认。
      if (currentConversationId) {
        setMessages((items) => {
          const scoped = items.filter((message) => (
            messageConversationId(message, currentConversationId) === currentConversationId
          ));
          const next = reconcileStaleSceneAssetUiFlags(scoped, { hasActiveAssetJob: false });
          if (next === scoped) return items;
          const unlocked = new Map(next.map((message) => [message.id, message]));
          const merged = items.map((message) => {
            if (messageConversationId(message, currentConversationId) !== currentConversationId) {
              return message;
            }
            return unlocked.get(message.id) || message;
          });
          messagesRef.current = merged;
          return merged;
        });
      }
      return;
    }
    if (nativeAssetsToolSignal.status === "running") {
      setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        "generate_scene_assets",
      ));
      return;
    }
    if (nativeAssetsToolSignal.status === "completed") {
      // 启动成功（含异步 polling）后进度进入生图中；有图完成由 snapshot 投影再推进 completed。
      setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        "generate_scene_assets",
      ));
    }
  }, [nativeAssetsToolSignal, currentConversationId]);
  // 参考图生图中：定期拉 Snapshot，把 Workspace 增量写入的 global_assets 投影到分镜画布。
  // 除 tool=running 外，进度卡 assets=running / 场景包 generating 也要轮询（热重载后 tool 事件可能已丢）。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const assetsStepRunning = assetPackageProgressSteps.some(
      (step) => step.id === "assets" && step.status === "running",
    );
    const sceneAssetsGenerating = messagesRef.current.some((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
      && message.artifact?.type === "video_scene_packages"
      && Boolean(message.artifact.sceneAssetsGenerating)
    ));
    const shouldPoll = (
      nativeAssetsToolSignal.status === "running"
      || assetsStepRunning
      || sceneAssetsGenerating
    );
    if (!shouldPoll) return;
    if (countGlobalAssetImageUrls(videoAgentView.workspace?.globalAssets) > 0
      && nativeAssetsToolSignal.status !== "running"
      && !assetsStepRunning) {
      return;
    }
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      void supervisorRuntime.refreshSnapshot().catch(() => {});
    };
    tick();
    const timer = window.setInterval(tick, 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    orchestrationMode,
    nativeAssetsToolSignal.status,
    assetPackageProgressSteps,
    currentConversationId,
    videoAgentView.workspace?.globalAssets,
    supervisorRuntime,
  ]);
  // 网关热重载后：无图、工具未跑、卡片也不在 generating → 解锁模型卡。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2" || !currentConversationId) return;
    if (nativeAssetsToolSignal.status === "running") return;
    if (countGlobalAssetImageUrls(videoAgentView.workspace?.globalAssets) > 0) return;
    const anyGenerating = messagesRef.current.some((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
      && message.artifact?.type === "video_scene_packages"
      && Boolean(message.artifact.sceneAssetsGenerating)
    ));
    if (anyGenerating) return;
    const needsUnlock = messagesRef.current.some((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
      && (
        (message.artifact?.type === "scene_asset_model_options"
          && Boolean(message.artifact.sceneAssetModelConfirmed))
        || (message.artifact?.type === "video_scene_packages"
          && !message.artifact.sceneAssetsAwaitingModel
          && !scenePackageHasGeneratedImages(message.artifact.videoScenePackages || null))
      )
    ));
    if (!needsUnlock) return;
    setMessages((items) => {
      const scoped = items.filter((message) => (
        messageConversationId(message, currentConversationId) === currentConversationId
      ));
      const next = reconcileStaleSceneAssetUiFlags(scoped, { hasActiveAssetJob: false });
      if (next === scoped) return items;
      const unlocked = new Map(next.map((message) => [message.id, message]));
      const merged = items.map((message) => {
        if (messageConversationId(message, currentConversationId) !== currentConversationId) {
          return message;
        }
        return unlocked.get(message.id) || message;
      });
      messagesRef.current = merged;
      return merged;
    });
  }, [
    orchestrationMode,
    currentConversationId,
    nativeAssetsToolSignal.status,
    videoAgentView.workspace?.globalAssets,
    videoAgentView.workspace?.revision,
  ]);
  // Workspace 增量进度 → 执行规划第 3 步细节（x/y：角色「安然」已完成）。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const progress = videoAgentView.workspace?.sceneAssetProgress;
    if (!progress || progress.total <= 0) return;
    setAssetPackageProgressSteps((current) => {
      const withAssets = applyAssetPackageAssetProgress(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        {
          completed: progress.completed,
          total: progress.total,
          asset_id: progress.assetId || undefined,
          asset_name: progress.assetName || undefined,
          asset_type: progress.assetType || undefined,
          ok: progress.ok ?? undefined,
        },
      );
      // 参考图全部完成后收口整块资产包进度，避免 packages 步永久 running。
      if (progress.completed >= progress.total) {
        return applyAssetPackageJobStage(withAssets, "completed");
      }
      return withAssets;
    });
  }, [
    orchestrationMode,
    videoAgentView.workspace?.sceneAssetProgress?.completed,
    videoAgentView.workspace?.sceneAssetProgress?.total,
    videoAgentView.workspace?.sceneAssetProgress?.assetId,
    videoAgentView.workspace?.sceneAssetProgress?.assetName,
    videoAgentView.workspace?.sceneAssetProgress?.ok,
  ]);
  // generate_scenes 启动后：收掉资产包进度板，切到分镜视频进度。
  useEffect(() => {
    if (!nativeGenerateScenesToolSignal.key) return;
    if (
      nativeGenerateScenesToolSignal.status !== "running"
      && nativeGenerateScenesToolSignal.status !== "completed"
    ) {
      return;
    }
    const scenes = videoAgentView.workspace?.scenes || [];
    let jobTotal = 0;
    for (const scene of scenes) {
      jobTotal += (scene.generationJobStatuses || []).length;
    }
    // 单镜生成时禁止用 scenes.length（全量包数）冒充本批总数。
    const total = resolveNativeSceneVideoBatchTotal({
      progressTotal: videoAgentView.workspace?.sceneVideoProgress?.total,
      jobTotal,
      generatingFallback: nativeGenerateScenesToolSignal.status === "running" ? 1 : null,
    });
    setAssetPackageProgressSteps([]);
    setSceneVideoProgressSteps((current) => {
      if (current.length > 0) {
        // progress/jobs 晚到时，把首屏误建的占位总数纠正为本批真实值。
        if (total > 0) {
          return applySceneVideoProgress(current, {
            completed: videoAgentView.workspace?.sceneVideoProgress?.completed ?? 0,
            total,
            scene_id: videoAgentView.workspace?.sceneVideoProgress?.sceneId,
            scene_index: videoAgentView.workspace?.sceneVideoProgress?.sceneIndex,
            ok: videoAgentView.workspace?.sceneVideoProgress?.ok ?? true,
          });
        }
        return current;
      }
      return createSceneVideoProgressSteps(total);
    });
    void supervisorRuntime.refreshSnapshot().catch(() => {});
  }, [
    nativeGenerateScenesToolSignal,
    videoAgentView.workspace?.sceneVideoProgress?.total,
    videoAgentView.workspace?.sceneVideoProgress?.completed,
    videoAgentView.workspace?.sceneVideoProgress?.sceneId,
    videoAgentView.workspace?.sceneVideoProgress?.sceneIndex,
    videoAgentView.workspace?.sceneVideoProgress?.ok,
    videoAgentView.workspace?.scenes,
    supervisorRuntime,
  ]);
  // 刷新/热重载后：Workspace 仍有「重新生成中」/ generation_jobs 或 scene_video_progress 时恢复分镜视频进度板。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const progress = videoAgentView.workspace?.sceneVideoProgress;
    const scenes = videoAgentView.workspace?.scenes || [];
    let jobTotal = 0;
    let jobCompleted = 0;
    for (const scene of scenes) {
      const statuses = scene.generationJobStatuses || [];
      if (statuses.length === 0) continue;
      jobTotal += statuses.length;
      jobCompleted += statuses.filter((status) => status === "succeeded").length;
    }
    const generatingScenes = scenes.filter((scene) => (
      scene.editStatus === "重新生成中" || scene.editStatus === "等待版本审核"
    ));
    const derivedTotal = progress?.total || jobTotal || generatingScenes.length;
    if (derivedTotal <= 0) return;
    const derivedCompleted = progress?.completed
      ?? jobCompleted
      ?? scenes.filter((scene) => Boolean(scene.mediaUrl)).length;
    setAssetPackageProgressSteps([]);
    setSceneVideoProgressSteps((current) => applySceneVideoProgress(
      current.length > 0 ? current : createSceneVideoProgressSteps(derivedTotal),
      {
        completed: derivedCompleted,
        total: derivedTotal,
        scene_id: progress?.sceneId,
        scene_index: progress?.sceneIndex,
        ok: progress?.ok ?? true,
      },
    ));
  }, [
    orchestrationMode,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.sceneVideoProgress?.completed,
    videoAgentView.workspace?.sceneVideoProgress?.total,
    videoAgentView.workspace?.scenes,
  ]);
  // Workspace 分镜视频进度 → 底栏 x/y；并投影 early 预览卡。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const progress = videoAgentView.workspace?.sceneVideoProgress;
    if (!progress || progress.total <= 0) return;
    setAssetPackageProgressSteps([]);
    setSceneVideoProgressSteps((current) => applySceneVideoProgress(
      current.length > 0 ? current : createSceneVideoProgressSteps(progress.total),
      {
        completed: progress.completed,
        total: progress.total,
        scene_id: progress.sceneId,
        scene_index: progress.sceneIndex,
        ok: progress.ok,
      },
    ));
  }, [
    orchestrationMode,
    videoAgentView.workspace?.sceneVideoProgress?.completed,
    videoAgentView.workspace?.sceneVideoProgress?.total,
    videoAgentView.workspace?.sceneVideoProgress?.sceneId,
    videoAgentView.workspace?.sceneVideoProgress?.sceneIndex,
    videoAgentView.workspace?.sceneVideoProgress?.ok,
  ]);
  // 分镜视频生成中：定期拉 Snapshot，把已完成镜的 video_url 投影到预览卡。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const videosRunning = sceneVideoProgressSteps.some(
      (step) => step.id === "videos" && step.status === "running",
    );
    const progress = videoAgentView.workspace?.sceneVideoProgress;
    const incomplete = Boolean(
      progress
      && progress.total > 0
      && progress.completed < progress.total,
    );
    const shouldPoll = (
      nativeGenerateScenesToolSignal.status === "running"
      || videosRunning
      || incomplete
    );
    if (!shouldPoll) return;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      void supervisorRuntime.refreshSnapshot().catch(() => {});
    };
    tick();
    const timer = window.setInterval(tick, 3_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    orchestrationMode,
    nativeGenerateScenesToolSignal.status,
    sceneVideoProgressSteps,
    videoAgentView.workspace?.sceneVideoProgress?.completed,
    videoAgentView.workspace?.sceneVideoProgress?.total,
    supervisorRuntime,
  ]);
  // 历史乐观进度：Turn 已结束或假转超时，且从未出现 generate_scene_assets 工具事件时，收掉假「正在生图」。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const assetsRunning = assetPackageProgressSteps.some(
      (step) => step.id === "assets" && step.status === "running",
    );
    if (!assetsRunning) return;
    const hasAssetsTool = selectNativeAgentTurns(supervisorRuntime.nativeUiState)
      .flatMap((turn) => turn.tools)
      .some((tool) => tool.toolName === "generate_scene_assets");
    if (hasAssetsTool) return;

    const failFakeAssetsProgress = () => {
      setAssetPackageProgressSteps((current) => failAssetPackageProgressSteps(
        current,
        "参考图未真正启动（无 generate_scene_assets 记录），请重新选择生图模型",
      ));
    };

    const thinking = supervisorRuntime.state.agentThinking;
    if (thinking && thinking.status === "streaming") {
      const startedMs = Date.parse(String(thinking.startedAt || ""));
      const ageMs = Number.isFinite(startedMs) ? Date.now() - startedMs : 90_000;
      // 模型确认 Turn 若 90s 内仍无工具事件，视为假转（常见于模型 500 / 热重载）。
      if (ageMs < 90_000) {
        const timer = window.setTimeout(failFakeAssetsProgress, 90_000 - ageMs + 200);
        return () => window.clearTimeout(timer);
      }
    }
    failFakeAssetsProgress();
    return undefined;
  }, [
    orchestrationMode,
    assetPackageProgressSteps,
    supervisorRuntime.nativeUiState,
    supervisorRuntime.state.agentThinking,
  ]);
  useEffect(() => {
    if (!nativePrepareToolSignal.key) return;
    const packages = Array.isArray(videoAgentView.workspace?.scenePackages)
      ? videoAgentView.workspace.scenePackages
      : [];
    const jobStatus = String(videoAgentView.workspace?.scenePackageJob?.status || "").toLowerCase();
    const jobActive = Boolean(
      videoAgentView.workspace?.scenePackageJob
      && ["polling", "running", "start_paused_quota", "queued"].includes(jobStatus),
    );
    const packagesReady = packages.length > 0 && !jobActive;
    const hasImages = countGlobalAssetImageUrls(videoAgentView.workspace?.globalAssets) > 0;
    // 重新生成分镜包：仅在 Job 仍活跃或尚无包时重置；陈旧 native running 不得盖回第 2 步。
    if (nativePrepareToolSignal.status === "running") {
      if (packagesReady) {
        setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
          current.length > 0 ? current : createAssetPackageProgressSteps(),
          hasImages ? "completed" : "awaiting_image_model",
        ));
        return;
      }
      setAssetPackageProgressSteps(() => applyAssetPackageJobStage(
        createAssetPackageProgressSteps(),
        "prepare_scene_packages",
      ));
      return;
    }
    if (nativePrepareToolSignal.status === "failed") {
      setAssetPackageProgressSteps((current) => failAssetPackageProgressSteps(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        nativePrepareToolSignal.summary
          || "prepare_scene_packages · 分镜包重新生成失败，请重试",
      ));
      return;
    }
    if (nativePrepareToolSignal.status === "completed") {
      setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        hasImages ? "completed" : "awaiting_image_model",
      ));
    }
  }, [
    nativePrepareToolSignal,
    videoAgentView.workspace?.globalAssets,
    videoAgentView.workspace?.scenePackages,
    videoAgentView.workspace?.scenePackageJob?.jobId,
    videoAgentView.workspace?.scenePackageJob?.status,
  ]);
  // 硬刷新后 native tool 事件丢失：用 Workspace 事实恢复「执行规划 · 视频资产包」进度卡。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const workspace = videoAgentView.workspace;
    if (!workspace) return;
    const packages = Array.isArray(workspace.scenePackages) ? workspace.scenePackages : [];
    const imageCount = countGlobalAssetImageUrls(workspace.globalAssets);
    const jobStatus = String(workspace.scenePackageJob?.status || "").toLowerCase();
    const jobActive = Boolean(
      workspace.scenePackageJob
      && ["polling", "running", "start_paused_quota", "queued"].includes(jobStatus),
    );
    let stage: string | null = null;
    if (imageCount > 0 && packages.length > 0) {
      stage = "completed";
    } else if (packages.length > 0) {
      stage = "awaiting_image_model";
    } else if (jobActive || workspace.scriptPlanConfirmed) {
      stage = "prepare_scene_packages";
    }
    if (!stage) return;
    setAssetPackageProgressSteps((current) => {
      const packagesRunning = current.some((step) => step.id === "packages" && step.status === "running");
      const assetsRunning = current.some((step) => step.id === "assets" && step.status === "running");
      const hasFailed = current.some((step) => step.status === "failed");
      // 生图进行中/失败细节不要被 snapshot 冲掉。
      if (hasFailed || assetsRunning) {
        return current;
      }
      // 重拆进行中：仅在 job 仍活跃时保留 running，避免旧 packages 盖回 awaiting。
      // 按钮 confirm-script-plan 不发 native prepare 完成事件；包已落库且 job 非活跃时必须解卡。
      if (packagesRunning && (jobActive || packages.length === 0)) {
        return current;
      }
      return applyAssetPackageJobStage(
        current.length > 0 ? current : createAssetPackageProgressSteps(),
        stage,
      );
    });
  }, [
    orchestrationMode,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.scriptPlanConfirmed,
    videoAgentView.workspace?.scenePackageJob?.jobId,
    videoAgentView.workspace?.scenePackageJob?.status,
    videoAgentView.workspace?.scenePackages?.length,
    videoAgentView.workspace?.globalAssets,
  ]);
  // 兜底：Workspace 已有场景包且 prepare job 非活跃时，强制把 packages=running 解卡。
  // 覆盖 confirm 按钮乐观态、陈旧 native prepare running、upsert early-return 等组合。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2") return;
    const workspace = videoAgentView.workspace;
    if (!workspace) return;
    const packages = Array.isArray(workspace.scenePackages) ? workspace.scenePackages : [];
    if (packages.length === 0) return;
    const jobStatus = String(workspace.scenePackageJob?.status || "").toLowerCase();
    const jobActive = Boolean(
      workspace.scenePackageJob
      && ["polling", "running", "start_paused_quota", "queued"].includes(jobStatus),
    );
    if (jobActive) return;
    const hasImages = countGlobalAssetImageUrls(workspace.globalAssets) > 0;
    setAssetPackageProgressSteps((current) => {
      if (current.length === 0) {
        return applyAssetPackageJobStage(
          createAssetPackageProgressSteps(),
          hasImages ? "completed" : "awaiting_image_model",
        );
      }
      const packagesRunning = current.some((step) => step.id === "packages" && step.status === "running");
      const assetsRunning = current.some((step) => step.id === "assets" && step.status === "running");
      if (!packagesRunning || assetsRunning) return current;
      return applyAssetPackageJobStage(
        current,
        hasImages ? "completed" : "awaiting_image_model",
      );
    });
  }, [
    orchestrationMode,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.scenePackages?.length,
    videoAgentView.workspace?.scenePackageJob?.status,
    videoAgentView.workspace?.globalAssets,
  ]);
  useEffect(() => {
    if (!currentConversationId) {
      setVideoAgentPlanAnchors({});
      videoAgentPlanAnchorsRef.current = {};
      setVideoAgentPlanHistory(emptyVideoAgentPlanHistory());
      lastPlanAnchorUserMessageIdRef.current = "";
      // 新建对话 / 清空会话：必须收掉上一段的执行规划进度板。
      setAssetPackageProgressSteps([]);
      setSceneVideoProgressSteps([]);
      setOptimisticGeneratingSceneIds([]);
      optimisticGeneratingRevisionRef.current = {};
      return;
    }
    // 切会话先恢复热缓存；随后 restoreConversation 会用服务端 context / Snapshot 覆盖。
    let next: Record<string, string> = {};
    try {
      const raw = sessionStorage.getItem(`pixelflow:video-agent-plan-anchors:${currentConversationId}`);
      const parsed = raw ? JSON.parse(raw) as Record<string, string> : {};
      next = parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      next = {};
    }
    videoAgentPlanAnchorsRef.current = next;
    setVideoAgentPlanAnchors(next);
    // 先读热缓存，避免空历史抢写覆盖；权威仍以 Snapshot.plans 为准。
    setVideoAgentPlanHistory(loadVideoAgentPlanHistory(currentConversationId));
    lastPlanAnchorUserMessageIdRef.current = "";
    scriptPlanConfirmedRef.current = false;
    characterSupplementNoticeRef.current = "";
    durableScriptPlanMessageIdsRef.current = new Set();
    setScriptPreviewOpen(false);
    setAssetPackageProgressSteps([]);
    setSceneVideoProgressSteps([]);
    setOptimisticGeneratingSceneIds([]);
    optimisticGeneratingRevisionRef.current = {};
  }, [currentConversationId]);
  useEffect(() => {
    assetPackageAnchorMessageIdRef.current = assetPackageAnchorMessageId;
  }, [assetPackageAnchorMessageId]);
  useEffect(() => {
    assetPackageProgressStepsRef.current = assetPackageProgressSteps;
  }, [assetPackageProgressSteps]);
  useEffect(() => {
    videoAgentPlanAnchorsRef.current = videoAgentPlanAnchors;
  }, [videoAgentPlanAnchors]);
  useEffect(() => {
    if (!currentConversationId) return;
    setVideoAgentPlanHistory((previous) => {
      const runtimePlans = supervisorRuntime.state.videoAgentPlans;
      const runtimeOrder = supervisorRuntime.state.videoAgentPlanOrder;
      const runtimeEmpty = Object.keys(runtimePlans || {}).length === 0
        && !supervisorRuntime.state.videoAgentPlan;
      // Snapshot 尚未带回 plans 时，保留热缓存，避免空合并把执行方案历史冲掉。
      if (runtimeEmpty && previous.order.length > 0) {
        return previous;
      }
      const merged = mergeVideoAgentPlanHistory(
        previous,
        runtimePlans,
        runtimeOrder,
        supervisorRuntime.state.videoAgentPlan,
      );
      if (
        merged.order.length === previous.order.length
        && merged.order.every((planId, index) => planId === previous.order[index])
        && merged.order.every((planId) => merged.plans[planId] === previous.plans[planId])
      ) {
        return previous;
      }
      // 热缓存仅加速同页往返；权威来源是 agent-snapshot.plans（DB）。
      saveVideoAgentPlanHistory(currentConversationId, merged);
      return merged;
    });
  }, [
    currentConversationId,
    supervisorRuntime.state.videoAgentPlan,
    supervisorRuntime.state.videoAgentPlanOrder,
    supervisorRuntime.state.videoAgentPlans,
  ]);
  useEffect(() => {
    const order = videoAgentPlanHistory.order.length > 0
      ? videoAgentPlanHistory.order
      : supervisorRuntime.state.videoAgentPlanOrder;
    if (order.length === 0) return;
    const userMessages = messages.filter((message) => message.role === "user");
    if (userMessages.length === 0) return;
    setVideoAgentPlanAnchors((previous) => {
      let changed = false;
      const next = { ...previous };
      const latestUserId = userMessages[userMessages.length - 1]?.id || "";
      order.forEach((planId, planIndex) => {
        const existing = next[planId];
        const plan = videoAgentPlanHistory.plans[planId]
          || supervisorRuntime.state.videoAgentPlans[planId];
        const isLatestPlan = planIndex === order.length - 1;
        const planStatus = String(plan?.status || "").toLowerCase();
        const isActiveWork = (
          isLatestPlan
          || planStatus === "running"
          || planStatus === "awaiting_confirmation"
          || planStatus === "planning"
        );
        const preferredId = lastPlanAnchorUserMessageIdRef.current;
        // 进行中/最新方案必须跟最近用户轮次，禁止按 planIndex 挂到早期消息导致卡片「顶在中间」。
        const preferredUserId = (
          (isActiveWork && (preferredId || latestUserId))
          || (existing && userMessages.some((message) => message.id === existing) ? existing : "")
          || preferredId
          || latestUserId
          || ""
        );
        // 优先锚到用户消息后的「已收到创作请求…」回执，避免方案卡插在回执前面。
        const resolved = resolveVideoAgentPlanAnchorId({
          preferredUserMessageId: preferredUserId,
          messages,
        });
        if (!resolved) return;
        if (existing === resolved) return;
        next[planId] = resolved;
        changed = true;
      });
      return changed ? next : previous;
    });
  }, [
    messages,
    supervisorRuntime.state.videoAgentPlanOrder,
    supervisorRuntime.state.videoAgentPlans,
    videoAgentPlanHistory.order,
    videoAgentPlanHistory.plans,
  ]);
  useEffect(() => {
    if (!currentConversationId) return;
    try {
      sessionStorage.setItem(
        `pixelflow:video-agent-plan-anchors:${currentConversationId}`,
        JSON.stringify(videoAgentPlanAnchors),
      );
    } catch {
      // ignore quota / private mode
    }
    // 恢复会话期间不回写，避免热缓存抢写覆盖服务端锚点。
    if (restoringRef.current) return;
    // 写入会话 context，换设备/清缓存后仍可从服务端恢复锚点。
    const timer = window.setTimeout(() => {
      if (restoringRef.current) return;
      void updateConversationWithProgress(currentConversationId, {
        context: {
          ...makeSnapshot(currentConversationId),
          videoAgentPlanAnchors,
          video_agent_plan_anchors: videoAgentPlanAnchors,
        },
      }).catch(() => {});
    }, 800);
    return () => window.clearTimeout(timer);
  }, [currentConversationId, videoAgentPlanAnchors]);
  const activeSupervisorVideoTarget = useMemo<SupervisorVideoTarget | null>(() => {
    // V2.1 批次 D：关闭 V2 的 Workflow 影子 UI；进度与动作只走 VideoAgent Plan/Turn。
    if (orchestrationMode === "video_agent_v2") return null;
    if (
      !restoredSupervisorUi
      || supervisorRuntime.state.conversationId !== currentConversationId
    ) return null;
    const workflow = supervisorRuntime.state.workflows.find(
      (workflow) => workflow.workflow_id === restoredSupervisorUi.workflowId
        && workflow.conversation_id === currentConversationId
        && workflow.kind === "video"
        && workflow.current_stage === restoredSupervisorUi.stage,
    );
    if (!workflow) return null;
    if (
      restoredSupervisorUi.artifactRef
      && !workflow.latest_artifact_refs.includes(restoredSupervisorUi.artifactRef)
    ) return null;
    return {
      ui: restoredSupervisorUi,
      workflow,
      stage: restoredSupervisorUi.stage,
      artifactRef: restoredSupervisorUi.artifactRef,
    };
  }, [
    currentConversationId,
    orchestrationMode,
    restoredSupervisorUi,
    supervisorRuntime.state.conversationId,
    supervisorRuntime.state.workflows,
  ]);
  const activeSupervisorVideoMessage = useMemo<ChatMessage | null>(() => {
    if (!activeSupervisorVideoTarget) return null;
    const allowedTypes = activeSupervisorVideoTarget.ui.kind === "video_direction_review"
      ? new Set(["directions"])
      : activeSupervisorVideoTarget.ui.kind === "video_plan_review"
        ? new Set(["plan"])
        : activeSupervisorVideoTarget.ui.kind === "video_scene_package_review"
          ? new Set(["video_scene_packages"])
          : activeSupervisorVideoTarget.ui.kind === "video_result_review"
            ? new Set(["video_scene_packages", "video_quality_review", "video_result", "jianying_draft"])
            : new Set<string>();
    const projected = selectSupervisorArtifactMessage(
      supervisorRuntime.state.messages,
      {
        workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
        artifactRef: activeSupervisorVideoTarget.artifactRef,
        allowedTypes: [...allowedTypes],
      },
    );
    if (!projected) return null;
    return messages.find(
      (message) => message.id === projected.id
        && messageConversationId(message, currentConversationId) === currentConversationId,
    ) ?? null;
  }, [
    activeSupervisorVideoTarget,
    currentConversationId,
    messages,
    supervisorRuntime.state.messages,
  ]);
  const interactionPolicy = resolveWorkspaceInteractionPolicy({
    mode: orchestrationMode,
    conversationId: currentConversationId,
    orchestrationResolved,
    legacyBusy,
    dialogOpen,
    pendingPlanRevision: Boolean(pendingPlanRevisionChoice),
    supervisorConnection: primaryExecutionUnavailable
      ? "fatal"
      : supervisorRuntime.state.connection.status,
    supervisorRun: supervisorRuntime.state.run.status,
    supervisorCompression: supervisorRuntime.state.compression.status,
    pendingSupervisorTurns: pendingSupervisorTurns.length,
  });
  const runtimeNotice = (() => {
    const notice = resolveSupervisorRuntimeNotice({
      enabled: runtimePolicy.supervisorEnabled && !primaryExecutionUnavailable,
      runStatus: supervisorRuntime.state.run.status,
      runUpdatedAt: supervisorRuntime.state.run.updatedAt,
      compression: supervisorRuntime.state.compression,
      inputQueue: supervisorRuntime.state.inputQueue,
    });
    if (!notice || orchestrationMode !== "video_agent_v2") return notice;
    // Turn 组已展示思考/活动时，不再叠一层「正在处理中」气泡，避免顺序像「活动→结论」。
    const turns = selectNativeAgentTurns(supervisorRuntime.nativeUiState);
    const turnVisibleBusy = turns.some((turn) => (
      turn.reasoningStatus === "streaming"
      || turn.responseStatus === "streaming"
      || turn.tools.some((tool) => tool.status === "running")
      || (turn.tools.length > 0 && !turn.responseCompleted)
    ));
    if (turnVisibleBusy && notice.tone === "working") return null;
    return notice;
  })();

  // 接收来自 content-app 的用户消息（通过 postMessage + CustomEvent）
  useEffect(() => {
    // 先检查是否已有等待消费的消息
    if (window.__CONTENT_APP_USER_MESSAGE__) {
      const msg = window.__CONTENT_APP_USER_MESSAGE__;
      window.__CONTENT_APP_USER_MESSAGE__ = undefined;
      handleSend(msg);
      return;
    }
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string | AgentUserMessagePayload>).detail;
      if (detail) handleSend(detail);
    };
    window.addEventListener("contentAppUserMessage", handler);
    return () => window.removeEventListener("contentAppUserMessage", handler);
  }, [agentRuntimeMode, orchestrationMode]);

  // 运行中上下文：这些值主要给异步 SSE 回调读取，不需要每次变化都触发 React 重渲染。
  // 可以类比后端 Service 内部字段，保存当前 taskId、事件去重集合和取消订阅函数。
  const [currentTaskId, setCurrentTaskId] = useState("");
  const messagesRef = useRef<ChatMessage[]>([]);
  /** 已成功落库的 client_message_id；用于 upsert 时跳过无意义的首次 PATCH 404。 */
  const persistedChatMessageIdsRef = useRef<Set<string>>(new Set());
  const lastPlanAnchorUserMessageIdRef = useRef("");
  const scriptPlanConfirmedRef = useRef(false);
  const characterSupplementNoticeRef = useRef("");
  const creativeRevisePendingRef = useRef(false);
  const durableScriptPlanMessageIdsRef = useRef<Set<string>>(new Set());
  const pendingPlanMessagePersistenceIdsRef = useRef(new Set<string>());
  const conversationIdRef = useRef<string>("");
  const routeConversationIdRef = useRef<string>("");
  const taskIdRef = useRef<string>("");
  const briefConfirmedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<number>());
  const announcedPhasesRef = useRef(new Set<string>());
  const processedArtifactIdsRef = useRef(new Set<string>());
  const pendingDialogContextRef = useRef<PendingDialogContext | null>(null);
  const flowDraftRef = useRef<FlowDraft | null>(null);
  const pendingMessageJobRef = useRef<PendingMessageJob | null>(null);
  const activeMessageJobPollsRef = useRef(new Set<string>());
  const pendingIntakeJobRef = useRef<PendingIntakeJob | null>(null);
  const activeIntakeJobPollsRef = useRef(new Set<string>());
  const pendingDirectionJobRef = useRef<PendingDirectionJob | null>(null);
  const activeDirectionJobPollsRef = useRef(new Set<string>());
  const pendingPlanJobRef = useRef<PendingPlanJob | null>(null);
  const activePlanJobPollsRef = useRef(new Set<string>());
  const planJobResumeAttemptsRef = useRef(new Map<string, number>());
  const planJobRecoveryNoticesRef = useRef(new Set<string>());
  const planJobPersistenceAttemptsRef = useRef(new Map<string, number>());
  const planJobPersistenceNoticesRef = useRef(new Set<string>());
  const scheduledPlanJobPersistenceRef = useRef(new Set<string>());
  const pendingImageEditRequestRef = useRef<PendingImageEditRequest | null>(null);
  const imageEditConfirmedSelectionsRef = useRef<Record<string, ImageEditModelSelection>>({});
  const pendingImageJobRef = useRef<PendingImageJob | null>(null);
  const activeImageJobPollsRef = useRef(new Set<string>());
  const planRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pptOutlineRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const imageRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const videoRevisionArtifactRef = useRef<PendingConversationArtifact | null>(null);
  const pendingScenePackageJobRef = useRef<PendingScenePackageJob | null>(null);
  const [pendingScenePackageResumeVersion, setPendingScenePackageResumeVersion] = useState(0);
  const activeScenePackageJobPollsRef = useRef(new Set<string>());
  const pendingVideoJobRef = useRef<PendingVideoJob | null>(null);
  const activeVideoJobPollsRef = useRef(new Set<string>());
  const pendingPptJobRef = useRef<PendingPptJob | null>(null);
  const workflowProgressRef = useRef<WorkflowProgressSnapshot | null>(null);
  const workflowProgressConversationIdRef = useRef("");
  const activePptJobPollsRef = useRef(new Set<string>());
  const pendingJianyingDraftJobRef = useRef<PendingJianyingDraftJob | null>(null);
  const activeJianyingDraftJobPollsRef = useRef(new Set<string>());
  const jianyingDraftStartGuardRef = useRef(new JianyingDraftStartGuard());
  const jianyingDraftRecordsByConversationRef = useRef(new Map<string, JianyingDraftRecordMap>());
  const pptDoneConversationIdsRef = useRef(new Set<string>());
  const briefReadyShownRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const pageVisibleRef = useRef(true);
  const saveTimerRef = useRef<number | undefined>(undefined);
  const skipRouteRestoreConversationRef = useRef("");
  /** 会话恢复世代令牌：只允许最新一次 resume 写回消息，避免 StrictMode/快切串台。 */
  const restoreTokenRef = useRef("");
  const unsubRef = useRef<() => void>(() => {});
  routeConversationIdRef.current = conversationId || "";

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    pendingSupervisorTurnsRef.current = pendingSupervisorTurns;
    if (currentConversationId) {
      pendingSupervisorTurnsByConversationRef.current.set(
        currentConversationId,
        pendingSupervisorTurns,
      );
    }
  }, [currentConversationId, pendingSupervisorTurns]);

  useEffect(() => {
    if (
      !runtimePolicy.supervisorEnabled
      || supervisorRuntime.state.connection.status !== "connected"
      || supervisorRuntime.state.conversationId !== currentConversationId
    ) return;
    const projectedMessages = mergeSupervisorMessagesWithPending(
      supervisorRuntime.state.messages,
      pendingSupervisorTurns.map((pendingTurn) => ({
        id: pendingTurn.clientInputId,
        conversationId: pendingTurn.conversationId,
        content: pendingTurn.content,
        materials: pendingTurn.materials as JsonObject[],
      })),
      currentConversationId,
    ).map((message) => ({
      ...message,
      // R1/Supervisor 会在连接建立后覆盖 REST 恢复消息，必须在这一投影边界再次转换时间。
      time: formatMessageTime(message.time, "zh-CN", undefined, message.time),
    }));
    // video_agent_v2：脚本保存等 refreshSnapshot 不能抹掉本地回执/时间线旁的聊天卡片。
    const nextMessages = orchestrationModeRef.current === "video_agent_v2"
      ? mergeProjectedMessagesWithLocalCards(
        projectedMessages,
        messagesRef.current,
        currentConversationId,
      )
      : projectedMessages;
    messagesRef.current = nextMessages;
    setMessages(nextMessages);
    // V2.1 批次 D：不再用 workflows 影子进度驱动 V2 任务板。
  }, [
    currentConversationId,
    orchestrationMode,
    pendingSupervisorTurns,
    runtimePolicy.supervisorEnabled,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.conversationId,
    supervisorRuntime.state.messages,
  ]);

  useEffect(() => {
    setJianyingDraftCapability({ available: false, reason: "剪映草稿服务待接入", poll_interval_seconds: 2 });
  }, []);

  const isPptDoneForConversation = (targetConversationId: string) => {
    return Boolean(targetConversationId && pptDoneConversationIdsRef.current.has(targetConversationId));
  };

  const setPptDoneForConversation = (targetConversationId: string, value: boolean) => {
    if (!targetConversationId) return;
    const nextIds = new Set(pptDoneConversationIdsRef.current);
    if (value) nextIds.add(targetConversationId);
    else nextIds.delete(targetConversationId);
    pptDoneConversationIdsRef.current = nextIds;
  };

  const setActiveTaskId = (taskId: string) => {
    taskIdRef.current = taskId;
    setCurrentTaskId(taskId);
  };

  const setActiveConversationId = (id: string) => {
    const previousId = conversationIdRef.current;
    conversationIdRef.current = id;
    setCurrentConversationId(id);
    setActiveConversationIdForTrace(id || null);
    // 仅切换会话时清空资产包/分镜视频进度；同 id 重入会误抹掉正在展示的分步卡片。
    if (previousId !== id) {
      setAssetPackageAnchorMessageId("");
      setAssetPackageProgressSteps([]);
      setSceneVideoProgressSteps([]);
      setOptimisticGeneratingSceneIds([]);
      optimisticGeneratingRevisionRef.current = {};
      persistedChatMessageIdsRef.current = new Set();
    }
  };

  const setResolvedOrchestrationMode = (mode: OrchestrationMode) => {
    orchestrationModeRef.current = mode;
    setOrchestrationMode(mode);
    setOrchestrationResolved(true);
  };

  const setResolvedAgentRuntimeMode = (
    mode: WorkspaceAgentRuntimeMode,
  ) => {
    agentRuntimeModeRef.current = mode;
    setAgentRuntimeMode(mode);
  };

  const isCurrentConversation = (targetConversationId: string) => {
    const activeConversationId = conversationIdRef.current || routeConversationIdRef.current;
    return shouldApplyVisibleConversationSideEffect(activeConversationId, targetConversationId);
  };

  const isVisibleConversation = (targetConversationId: string) => {
    return pageVisibleRef.current && isCurrentConversation(targetConversationId);
  };

  const advanceWorkflowProgress = (
    targetConversationId: string,
    lastPhase: string,
    patch: Partial<Omit<WorkflowProgressSnapshot, "version" | "last_phase" | "updated_at">> = {},
  ): WorkflowProgressSnapshot | null => {
    if (!targetConversationId) return null;
    const belongsToTarget = workflowProgressConversationIdRef.current === targetConversationId;
    const current = belongsToTarget ? workflowProgressRef.current : null;
    const contextIntent = workflowIntentFromPhase(lastPhase);
    const inferredFlowKind: WorkflowFlowKind = lastPhase.startsWith("image_edit_") ? "direct_image_edit" : "standard";
    const next: WorkflowProgressSnapshot = {
      version: 1,
      intent: patch.intent !== undefined ? patch.intent : current?.intent || contextIntent,
      flow_kind: patch.flow_kind || current?.flow_kind || inferredFlowKind,
      source_message_id: patch.source_message_id !== undefined ? patch.source_message_id : current?.source_message_id || "",
      last_phase: lastPhase,
      scene_package_stage: patch.scene_package_stage !== undefined ? patch.scene_package_stage : current?.scene_package_stage || null,
      updated_at: new Date().toISOString(),
    };
    if (isCurrentConversation(targetConversationId)) {
      workflowProgressConversationIdRef.current = targetConversationId;
      workflowProgressRef.current = next;
      setWorkflowProgress(next);
    }
    return next;
  };

  const updateConversationWithProgress = async (
    targetConversationId: string,
    body: { title?: string; current_task_id?: string | null; last_phase?: string; context?: Record<string, unknown> },
    progressPatch: Partial<Omit<WorkflowProgressSnapshot, "version" | "last_phase" | "updated_at">> = {},
  ) => {
    const nextProgress = body.last_phase
      ? advanceWorkflowProgress(targetConversationId, body.last_phase, progressPatch)
      : workflowProgressConversationIdRef.current === targetConversationId
        ? workflowProgressRef.current
        : null;
    const updated = await api.updateConversation(targetConversationId, {
      ...body,
      context: body.context
        ? {
            ...body.context,
            workflowProgress: nextProgress,
            workflow_progress: nextProgress,
          }
        : body.context,
    });
    // 会话阶段写入成功后刷新左侧列表，避免异步 Job 完成后仍展示旧的运行态。
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    return updated;
  };

  const setBusyForConversation = (targetConversationId: string, value: boolean) => {
    if (!isCurrentConversation(targetConversationId)) return;
    if (value && !pageVisibleRef.current) return;
    setBusy(value);
  };

  const setCanvasOpenForConversation = (targetConversationId: string, value: boolean) => {
    if (isVisibleConversation(targetConversationId)) {
      setCanvasOpen(value);
      if (!value) setSelectedStoryboardMessageId("");
    }
  };

  const setCanvasForConversation = (
    targetConversationId: string,
    updater: CanvasState | ((current: CanvasState) => CanvasState),
  ) => {
    if (!isVisibleConversation(targetConversationId)) return;
    setCanvas(updater);
  };

  const beginArtifactAction = (
    msg: ChatMessage,
    targetConversationId: string,
    actionKey?: string,
  ): string => {
    const pendingMessageJob = pendingMessageJobRef.current;
    if (isPendingPlanSaveForConversation(pendingMessageJob, targetConversationId)) {
      return "";
    }
    const base = processedArtifactKey(msg, targetConversationId);
    // 单镜生成用独立 key，避免分镜 5 生成中锁死分镜 6/7 的「确认并生成」。
    const key = actionKey ? `${base}:${actionKey}` : base;
    if (processedArtifactIdsRef.current.has(key)) return "";
    processedArtifactIdsRef.current.add(key);
    return key;
  };

  const releaseArtifactAction = (key: string) => {
    if (key) processedArtifactIdsRef.current.delete(key);
  };

  const markImageResultAccepted = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "image_result") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            imageAccepted: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const markVideoResultAccepted = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "video_result") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            videoAccepted: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const recordArtifactDownload = async (msg: ChatMessage, url: string) => {
    if (!url || !msg.artifact) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const currentMessage = messagesRef.current.find(
      (message) => message.id === msg.id && messageConversationId(message, targetConversationId) === targetConversationId,
    ) || msg;
    if (!currentMessage.artifact || currentMessage.artifact.deliveryDownloadedAt) return;
    const artifact: ChatArtifact = {
      ...currentMessage.artifact,
      deliveryDownloadedAt: new Date().toISOString(),
      deliveryDownloadedUrl: url,
    };
    try {
      await api.updateConversationMessage(targetConversationId, currentMessage.id, {
        payload: {
          artifact,
          materials: currentMessage.materials || artifact.materials || [],
          client_message_id: currentMessage.id,
        } as unknown as Record<string, unknown>,
      });
      if (!isCurrentConversation(targetConversationId)) return;
      setMessages((items) => {
        const nextItems = items.map((message) =>
          message.id === currentMessage.id && messageConversationId(message, targetConversationId) === targetConversationId
            ? { ...message, artifact }
            : message,
        );
        messagesRef.current = nextItems;
        return nextItems;
      });
    } catch (err) {
      pushAssistant(`下载已开始，但交付状态保存失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    }
  };

  const hasPendingImageRevisionForResult = (msg: ChatMessage, targetConversationId: string): boolean => {
    const pendingRevision = imageRevisionArtifactRef.current;
    if (pendingRevision?.conversationId !== targetConversationId || !pendingRevision.artifact.imageResult || !msg.artifact?.imageResult) {
      return false;
    }
    const pendingImage = pendingRevision.artifact.imageResult;
    const currentImage = msg.artifact.imageResult;
    return Boolean(
      (pendingImage.task_id && pendingImage.task_id === currentImage.task_id) ||
        (pendingImage.images[0]?.url && pendingImage.images[0]?.url === currentImage.images[0]?.url),
    );
  };

  const shouldAutoAcceptImageResult = (msg: ChatMessage, targetConversationId: string): boolean => {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult) || msg.artifact.imageAccepted) return false;
    if (hasPendingImageRevisionForResult(msg, targetConversationId)) return false;
    return messagesRef.current.some((message) => {
      if (message.id !== msg.id || messageConversationId(message, targetConversationId) !== targetConversationId) return false;
      return message.artifact?.type === "image_result" && Boolean(message.artifact.imageResult && canAcceptImageResult(message.artifact.imageResult)) && !message.artifact.imageAccepted;
    });
  };

  const persistChatMessage = async (conversation: string, message: ChatMessage): Promise<ChatMessage> => {
    const request: ConversationMessageJobRequest = {
      role: message.role,
      content: message.content,
      payload: { artifact: message.artifact, materials: message.materials || [], client_message_id: message.id },
    };
    const started = await api.startConversationMessageJob(conversation, request);
    const saved = await api.pollConversationMessageJob(conversation, started.job_id);
    if (!saved) throw new Error("对话消息保存已暂停");
    return {
      ...message,
      id: message.id,
      conversationId: conversation,
      time: formatMessageTime(saved.created_at),
    };
  };

  /**
   * 同 client_message_id 的进度/结果卡必须 PATCH 更新。
   * 首次落库走 create（messages/start）；PATCH 仅在已知已落库时使用，避免固定 404 噪音。
   */
  const upsertPersistedChatMessage = async (
    message: ChatMessage,
    targetConversationId: string,
    options?: { insertBeforeId?: string | null },
  ): Promise<void> => {
    if (!targetConversationId) {
      appendOptimisticMessageForConversation(message, targetConversationId, options);
      return;
    }
    const optimisticMessage = appendOptimisticMessageForConversation(message, targetConversationId, options);
    const payload = {
      artifact: optimisticMessage.artifact,
      materials: optimisticMessage.materials || [],
      client_message_id: optimisticMessage.id,
    } as unknown as Record<string, unknown>;
    const knownPersisted = persistedChatMessageIdsRef.current.has(optimisticMessage.id);
    if (!knownPersisted) {
      try {
        await persistChatMessage(targetConversationId, optimisticMessage);
        persistedChatMessageIdsRef.current.add(optimisticMessage.id);
      } catch {
        // keep optimistic local copy
      }
      return;
    }
    try {
      await api.updateConversationMessage(targetConversationId, optimisticMessage.id, {
        content: optimisticMessage.content,
        payload,
      });
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      if (status !== 404) return;
      try {
        await persistChatMessage(targetConversationId, optimisticMessage);
        persistedChatMessageIdsRef.current.add(optimisticMessage.id);
      } catch {
        // keep optimistic local copy
      }
    }
  };

  const replaceOptimisticMessage = (
    optimisticMessageId: string,
    savedMessage: ChatMessage,
    targetConversationId: string,
    preferSavedArtifact = false,
  ) => {
    // 乐观消息落库后 id 会变；同步重映射进度卡 / 执行方案锚点，避免卡片漂到错误位置或变 orphan。
    if (optimisticMessageId && savedMessage.id && optimisticMessageId !== savedMessage.id) {
      if (assetPackageAnchorMessageIdRef.current === optimisticMessageId) {
        assetPackageAnchorMessageIdRef.current = savedMessage.id;
        setAssetPackageAnchorMessageId(savedMessage.id);
      }
      if (lastPlanAnchorUserMessageIdRef.current === optimisticMessageId) {
        lastPlanAnchorUserMessageIdRef.current = savedMessage.id;
      }
      const remappedAnchors = remapMessageAnchorId(
        videoAgentPlanAnchorsRef.current,
        optimisticMessageId,
        savedMessage.id,
      );
      if (remappedAnchors !== videoAgentPlanAnchorsRef.current) {
        videoAgentPlanAnchorsRef.current = remappedAnchors;
        setVideoAgentPlanAnchors(remappedAnchors);
      }
    }
    setMessages((items) => {
      const currentMessage = items.find((item) => item.id === optimisticMessageId);
      if (!currentMessage) {
        const nextItems = appendVisibleConversationMessage(items, {
          activeConversationId: conversationIdRef.current,
          targetConversationId,
          message: { ...savedMessage, conversationId: targetConversationId },
        });
        messagesRef.current = nextItems;
        return nextItems;
      }
      const nextItems = replaceMessageById(items, optimisticMessageId, {
        ...savedMessage,
        artifact: preferSavedArtifact ? savedMessage.artifact : currentMessage?.artifact || savedMessage.artifact,
        materials: preferSavedArtifact ? savedMessage.materials : currentMessage?.materials || savedMessage.materials,
        conversationId: targetConversationId,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const appendOptimisticMessageForConversation = (
    message: ChatMessage,
    targetConversationId: string,
    options?: { insertBeforeId?: string | null },
  ): ChatMessage => {
    const optimisticMessage = { ...message, conversationId: targetConversationId, time: message.time || now() };
    setMessages((items) => {
      const nextItems = appendVisibleConversationMessage(items, {
        activeConversationId: conversationIdRef.current,
        targetConversationId,
        message: optimisticMessage,
        insertBeforeId: options?.insertBeforeId,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    return optimisticMessage;
  };

  const ensurePendingSupervisorTurnVisible = (pendingTurn: PendingSupervisorTurn) => {
    const alreadyVisible = messagesRef.current.some(
      (item) => item.id === pendingTurn.clientInputId
        && messageConversationId(item, pendingTurn.conversationId) === pendingTurn.conversationId,
    );
    if (alreadyVisible) return;
    appendOptimisticMessageForConversation({
      id: pendingTurn.clientInputId,
      conversationId: pendingTurn.conversationId,
      role: "user",
      content: pendingTurn.content,
      materials: pendingTurn.materials,
      time: "",
    }, pendingTurn.conversationId);
  };

  const appendMessageForConversation = async (message: ChatMessage, targetConversationId: string): Promise<ChatMessage> => {
    if (targetConversationId) {
      const optimisticMessage = appendOptimisticMessageForConversation(message, targetConversationId);
      try {
        const savedMessage = await persistChatMessage(targetConversationId, optimisticMessage);
        replaceOptimisticMessage(optimisticMessage.id, savedMessage, targetConversationId);
        return savedMessage;
      } catch {
        return optimisticMessage;
      }
    }
    const localMessage = { ...message, time: message.time || now() };
    setMessages((items) => {
      const nextItems = [...items, localMessage];
      messagesRef.current = nextItems;
      return nextItems;
    });
    return localMessage;
  };

  const removeOptimisticMessage = (optimisticMessageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.filter(
        (item) => item.id !== optimisticMessageId || messageConversationId(item, targetConversationId) !== targetConversationId,
      );
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const schedulePendingMessageJobResume = (pendingMessageJob: PendingMessageJob, delayMs?: number) => {
    const delay = delayMs ?? planMessageResumeDelayMs(pendingMessageJob.restart_count);
    window.setTimeout(() => {
      const current = pendingMessageJobRef.current;
      if (!isSameMessageJobGeneration(current, pendingMessageJob)) return;
      if (!current) return;
      void resumePendingMessageJob(current).catch(() => {});
    }, delay);
  };

  const persistPlanArtifactForConversation = async (
    message: ChatMessage,
    targetConversationId: string,
    continuation: PendingPlanSaveMessageContinuation,
  ): Promise<void> => {
    pendingPlanMessagePersistenceIdsRef.current.add(message.id);
    try {
      if (!targetConversationId) throw new Error("无法保存 plan.md：对话 ID 不存在");
      const pendingMessageJob = await startConversationMessageJobForConversation(
        message,
        targetConversationId,
        continuation,
      );
      if (!pendingMessageJob) throw new Error("无法保存 plan.md：消息任务未启动");
      await resumePendingMessageJob(pendingMessageJob);
    } catch (err) {
      const pendingMessageJob = pendingMessageJobRef.current;
      if (
        pendingMessageJob?.source_message_id === message.id
        && pendingMessageJob.continue_after_save?.type === "plan_save"
      ) {
        schedulePendingMessageJobResume(pendingMessageJob, 0);
        return;
      }
      pendingPlanMessagePersistenceIdsRef.current.delete(message.id);
      removeOptimisticMessage(message.id, targetConversationId);
      throw err;
    }
  };

  const startConversationMessageJobForConversation = async (
    message: ChatMessage,
    targetConversationId: string,
    continuation?: PendingMessageJobContinuation,
  ): Promise<PendingMessageJob | null> => {
    const existingPendingMessageJob = pendingMessageJobRef.current;
    if (
      continuation?.type !== "plan_save"
      && existingPendingMessageJob
      && isPendingPlanSaveForConversation(existingPendingMessageJob, targetConversationId)
    ) {
      schedulePendingMessageJobResume(existingPendingMessageJob, 0);
      throw new Error("当前 plan.md 仍在保存，请等待保存完成后再发送新消息");
    }
    if (!targetConversationId) {
      appendOptimisticMessageForConversation(message, targetConversationId);
      return null;
    }
    const optimisticMessage = appendOptimisticMessageForConversation(message, targetConversationId);
    const request: ConversationMessageJobRequest = {
      role: optimisticMessage.role,
      content: optimisticMessage.content,
      payload: {
        artifact: optimisticMessage.artifact,
        materials: optimisticMessage.materials || [],
        client_message_id: optimisticMessage.id,
      },
    };
    const started = await api.startConversationMessageJob(targetConversationId, request);
    const pendingMessageJob: PendingMessageJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: optimisticMessage.id,
      kind: "conversation_message",
      started_at: new Date().toISOString(),
      request,
      message: optimisticMessage,
      continue_after_save: continuation,
    };
    if (continuation?.type === "handle_send") {
      advanceWorkflowProgress(targetConversationId, "message_save_running", {
        intent: null,
        flow_kind: "standard",
        source_message_id: optimisticMessage.id,
        scene_package_stage: null,
      });
    }
    await persistPendingMessageJob(
      pendingMessageJob,
      targetConversationId,
      continuation?.type === "plan_save" ? "plan_message_save_running" : "message_save_running",
    );
    return pendingMessageJob;
  };

  const pushAssistant = (content: string, targetConversationId = conversationIdRef.current) => {
    const message: ChatMessage = { id: uid(), conversationId: targetConversationId || undefined, role: "assistant", content, time: "" };
    void appendMessageForConversation(message, targetConversationId);
    return message.id;
  };

  /** 旧视频 Job HTTP 已删除：用户主动触发时提示走对话，恢复轮询时静默跳过。 */
  const notifyLegacyVideoJobBlocked = (
    targetConversationId: string,
    processedKey = "",
    tip = LEGACY_VIDEO_JOB_CONTINUE_TIP,
  ) => {
    if (processedKey) releaseArtifactAction(processedKey);
    pushAssistant(tip, targetConversationId);
  };

  const pushArtifact = (
    content: string,
    artifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
    messageId = uid(),
  ) => {
    const message: ChatMessage = { id: messageId, conversationId: targetConversationId || undefined, role: "assistant", content, time: "", artifact };
    void appendMessageForConversation(message, targetConversationId);
    return message;
  };

  const makeFlowDraft = (
    stage: FlowDraftStage,
    data: Omit<FlowDraft, "version" | "stage" | "updatedAt">,
  ): FlowDraft => ({
    version: 1,
    stage,
    ...data,
    updatedAt: new Date().toISOString(),
  });

  const hasDirectionsArtifactForDraft = (items: ChatMessage[], targetConversationId: string, draft: FlowDraft | null | undefined): boolean => {
    if (!draft?.creativeDirections?.length) return false;
    const draftFingerprint = creativeDirectionsFingerprint(draft.creativeDirections);
    return items.some((message) => {
      if (messageConversationId(message, targetConversationId) !== targetConversationId) return false;
      const artifact = message.artifact;
      if (artifact?.type !== "directions" || artifact.intent !== draft.intent) return false;
      return Boolean(draftFingerprint && creativeDirectionsFingerprint(artifact.directions) === draftFingerprint);
    });
  };

  const restoreFormDraft = (draft: FlowDraft, targetConversationId: string) => {
    if (!isCreationIntent(draft.intent)) return;
    const materials = draft.materials || [];
    const formValues = draft.form ? valuesFromForm(draft.form) : draft.formValues || {};
    setPendingCore(draft.coreMessage || "");
    setPendingIntent(draft.intent);
    setPendingFormValues(formValues);
    setPendingMaterials(materials);
    pendingDialogContextRef.current = {
      conversationId: targetConversationId,
      coreMessage: draft.coreMessage || "",
      materials,
      intakeContext: draft.intakeContext || draft.intakeIntent?.intake_context || {},
    };
    if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
  };

  const recordImageEditConfirmedSelection = (
    messageId: string,
    targetConversationId: string,
    selection: ImageEditModelSelection,
  ) => {
    imageEditConfirmedSelectionsRef.current = {
      ...imageEditConfirmedSelectionsRef.current,
      [messageId]: selection,
    };
    setMessages((items) =>
      items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "image_edit_options") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            imageEditConfirmedSelection: selection,
            imageEditRequest: {
              ...(message.artifact.imageEditRequest || {}),
              selection,
            },
          },
        };
      }),
    );
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const buildPptImagesMessageContent = (pptImages: PptImagesResult) =>
    pptImages.ok ? "PPT 页面图片已生成，请确认后生成 PPT 附件。" : "PPT 页面图片生成中，请查看每页状态。";

  const buildPptImagesArtifact = (
    pptImages: PptImagesResult,
    sourceArtifact: ChatArtifact,
    pptContentJson?: PptContentJsonResult,
  ): ChatArtifact => ({
    ...sourceArtifact,
    type: "ppt_images",
    title: "PPT页面图片",
    description: String(pptImages.message || `${pptImages.pages.length} 页 PPT 图片`),
    actionLabel: "生成附件",
    intent: "ppt",
    materials: sourceArtifact.materials || [],
    pptContentJson: pptContentJson || sourceArtifact.pptContentJson,
    pptImages,
    smartPptProjectId:
      pptProjectId(sourceArtifact) ?? numericValue(pptImages.smart_ppt_project_id) ?? numericValue(pptContentJson?.smart_ppt_project_id),
  });

  const updatePptImagesArtifactInMessage = (
    messageId: string,
    targetConversationId: string,
    pptImages: PptImagesResult,
    sourceArtifact?: ChatArtifact,
    pptContentJson?: PptContentJsonResult,
  ) => {
    const visibleMessage = messagesRef.current.find(
      (message) =>
        message.id === messageId &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        message.artifact?.type === "ppt_images",
    );
    const baseArtifact = visibleMessage?.artifact || sourceArtifact;
    if (!baseArtifact) return;

    const content = buildPptImagesMessageContent(pptImages);
    const artifact = buildPptImagesArtifact(pptImages, baseArtifact, pptContentJson);
    if (isVisibleConversation(targetConversationId)) {
      setMessages((items) => {
        const nextItems = items.map((message) => {
          if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "ppt_images") {
            return message;
          }
          return {
            ...message,
            content,
            artifact,
          };
        });
        messagesRef.current = nextItems;
        return nextItems;
      });
    }
    void api
      .updateConversationMessage(targetConversationId, messageId, {
        content,
        payload: {
          artifact,
          materials: artifact.materials || [],
          client_message_id: messageId,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const markPptFileDoneInMessage = (messageId: string, targetConversationId: string) => {
    setMessages((items) => {
      const nextItems = items.map((message) => {
        if (message.id !== messageId || messageConversationId(message, targetConversationId) !== targetConversationId || message.artifact?.type !== "ppt_file") {
          return message;
        }
        return {
          ...message,
          artifact: {
            ...message.artifact,
            pptDone: true,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const updateVideoScenePackagesInMessage = (
    messageId: string,
    updater: (scenePackages: ScenePackageRecord[]) => ScenePackageRecord[],
    editedSceneId?: string,
  ) => {
    let targetConversationId = "";
    let updatedPackages: PrepareScenePackagesResponse | undefined;
    let editedSceneIdsForContext: string[] = [];
    setMessages((items) => {
      const nextItems = items.map((message) => {
        const artifact = message.artifact;
        const videoScenePackages = artifact?.videoScenePackages;
        if (message.id !== messageId || !artifact || !videoScenePackages) return message;
        targetConversationId = messageConversationId(message, conversationIdRef.current);
        const editedSceneIds = new Set(artifact.videoScenePackageEditedSceneIds || []);
        if (editedSceneId) editedSceneIds.add(editedSceneId);
        const nextScenePackages = updater(videoScenePackages.scene_packages as ScenePackageRecord[]) as typeof videoScenePackages.scene_packages;
        editedSceneIdsForContext = Array.from(editedSceneIds);
        updatedPackages = {
          ...videoScenePackages,
          scene_packages: nextScenePackages,
        };
        return {
          ...message,
          artifact: {
            ...artifact,
            videoScenePackageEditedSceneIds: editedSceneIdsForContext,
            videoScenePackages: updatedPackages,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    if (targetConversationId && updatedPackages) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            global_assets: updatedPackages.global_assets,
            scene_packages: updatedPackages.scene_packages,
            video_scene_package_edited_scene_ids: editedSceneIdsForContext,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const updateVideoScenePackageArtifactInMessage = (
    messageId: string,
    updater: (videoScenePackages: PrepareScenePackagesResponse) => PrepareScenePackagesResponse,
  ): PrepareScenePackagesResponse | undefined => {
    let updatedPackages: PrepareScenePackagesResponse | undefined;
    setMessages((items) => {
      const nextItems = items.map((message) => {
        const artifact = message.artifact;
        const videoScenePackages = artifact?.videoScenePackages;
        if (message.id !== messageId || !artifact || !videoScenePackages) return message;
        updatedPackages = updater(videoScenePackages);
        return {
          ...message,
          artifact: {
            ...artifact,
            videoScenePackages: updatedPackages,
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    return updatedPackages;
  };

  const persistScenePackageSnapshot = (
    targetConversationId: string,
    packages: PrepareScenePackagesResponse,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    if (!targetConversationId) return;
    void updateConversationWithProgress(targetConversationId, {
        last_phase: lastPhase,
        context: {
          ...makeSnapshot(targetConversationId),
          global_assets: packages.global_assets,
          scene_packages: packages.scene_packages,
          ...extraContext,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const updateOriginalScenePackageMessageWithVideoResult = (
    sourceMessageId: string,
    targetConversationId: string,
    videoScenePackages: PrepareScenePackagesResponse,
    generatedSceneVideos: NonNullable<ChatArtifact["generatedSceneVideos"]>,
    mergedVideo: NonNullable<ChatArtifact["mergedVideo"]>,
  ) => {
    setMessages((items) => {
      const sourceIndex = items.findIndex(
        (message) =>
          message.id === sourceMessageId &&
          messageConversationId(message, targetConversationId) === targetConversationId &&
          message.artifact?.type === "video_scene_packages" &&
          Boolean(message.artifact.videoScenePackages),
      );
      const targetIndex = sourceIndex >= 0
        ? sourceIndex
        : items
            .map((message, index) => ({ message, index }))
            .reverse()
            .find(
              ({ message }) =>
                messageConversationId(message, targetConversationId) === targetConversationId &&
                message.artifact?.type === "video_scene_packages" &&
                Boolean(message.artifact.videoScenePackages),
            )?.index ?? -1;
      if (targetIndex < 0) {
        messagesRef.current = items;
        return items;
      }
      const nextItems = items.map((message, index) => {
        if (index !== targetIndex || !message.artifact?.videoScenePackages) return message;
        return {
          ...message,
          artifact: {
            ...message.artifact,
            videoScenePackages,
            generatedSceneVideos,
            mergedVideo,
            videoScenePackageEditedSceneIds: [],
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(),
            global_assets: videoScenePackages.global_assets,
            scene_packages: videoScenePackages.scene_packages,
            generated_scene_videos: generatedSceneVideos.scene_videos,
            merged_video: mergedVideo,
            video_scene_package_edited_scene_ids: [],
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const storyboardMessageHasGlobalAsset = (message: ChatMessage | undefined, reference: SceneGlobalAssetReference): boolean => {
    const globalAssets = message?.artifact?.videoScenePackages?.global_assets;
    const records = globalAssets?.[reference.asset_group];
    if (!Array.isArray(records)) return false;
    return records.some((record) => String(record.asset_id || record.id || "") === reference.asset_id);
  };

  const findStoryboardMessageForGlobalAsset = (
    reference: SceneGlobalAssetReference,
    targetConversationId: string,
    preferredMessageIds: string[] = [],
  ): ChatMessage | undefined => {
    const currentMessages = messagesRef.current;
    const preferredIds = [
      ...preferredMessageIds,
      reference.storyboard_message_id,
      selectedStoryboardMessageId,
    ].filter((id): id is string => Boolean(id));
    for (const id of preferredIds) {
      const candidate = currentMessages.find(
        (item) =>
          item.id === id &&
          messageConversationId(item, targetConversationId) === targetConversationId &&
          Boolean(item.artifact?.videoScenePackages),
      );
      if (storyboardMessageHasGlobalAsset(candidate, reference)) return candidate;
    }
    const selectedCandidate = selectedStoryboardMessageId
      ? currentMessages.find((item) => item.id === selectedStoryboardMessageId && item.artifact?.videoScenePackages)
      : undefined;
    if (storyboardMessageHasGlobalAsset(selectedCandidate, reference)) return selectedCandidate;
    const latestCandidate = [...currentMessages]
      .reverse()
      .find(
        (item) =>
          messageConversationId(item, targetConversationId) === targetConversationId &&
          Boolean(item.artifact?.videoScenePackages) &&
          storyboardMessageHasGlobalAsset(item, reference),
      );
    if (latestCandidate) return latestCandidate;
    const referencedCandidate = reference.storyboard_message_id
      ? currentMessages.find((item) => item.id === reference.storyboard_message_id && item.artifact?.videoScenePackages)
      : undefined;
    return storyboardMessageHasGlobalAsset(referencedCandidate, reference) ? referencedCandidate : undefined;
  };

  const handleUpdateVideoScenePackage = (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => {
    updateVideoScenePackagesInMessage(msg.id, (scenePackages) => updateScenePackageField(scenePackages, sceneId, patch), sceneId);
    // V2.1：仅在「保存」冲洗草稿时进入这里（StoryboardPanel deferSceneUpdates）。
    // 禁止在每次按键 / @ 选素材时发 Turn，否则会打断分镜编辑。
    if (orchestrationModeRef.current === "video_agent_v2") {
      const targetConversationId = messageConversationId(msg, conversationIdRef.current);
      if (!targetConversationId) return;
      const parts: string[] = [`修改分镜 ${sceneId}`];
      if (typeof patch.storyline === "string" && patch.storyline.trim()) {
        parts.push(`故事线：${patch.storyline.trim()}`);
      }
      if (typeof patch.prompt === "string" && patch.prompt.trim()) {
        parts.push(`提示词：${patch.prompt.trim()}`);
      }
      if (typeof patch.narration === "string") {
        parts.push(`旁白：${patch.narration}`);
      }
      if (typeof patch.transition === "string" && patch.transition.trim()) {
        parts.push(`转场：${patch.transition.trim()}`);
      }
      if (patch.duration_ms != null && String(patch.duration_ms).trim()) {
        parts.push(`时长毫秒：${String(patch.duration_ms).trim()}`);
      }
      const shotText = patch.shot_description && typeof patch.shot_description === "object"
        && typeof patch.shot_description.text === "string"
        ? patch.shot_description.text.trim()
        : "";
      if (shotText) parts.push(`镜头描述：${shotText}`);
      if (Array.isArray(patch.reference_asset_ids) && patch.reference_asset_ids.length > 0) {
        parts.push(`参考素材：${patch.reference_asset_ids.map((id) => String(id || "").trim()).filter(Boolean).join("、")}`);
      }
      void handleSupervisorTurn(
        {
          conversationId: targetConversationId,
          clientInputId: uid(),
          content: parts.join("\n"),
          materials: [],
          replyToMessageId: null,
          artifactRefs: [],
          interruptId: null,
          explicitAction: null,
          continueLegacy: false,
          registrationStatus: "pending",
        },
        supervisorRuntime.getContextVersion() ?? 0,
      );
    }
  };

  const handleReferenceGlobalAsset = (asset: SceneGlobalAssetReference) => {
    const material = selectedStoryboardMessageId
      ? { ...asset, conversation_id: currentConversationId, scene_global_asset_action: "edit" as const, storyboard_message_id: selectedStoryboardMessageId }
      : { ...asset, conversation_id: currentConversationId, scene_global_asset_action: "edit" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== material.asset_id);
      return [material, ...next].slice(0, 1);
    });
  };

  const handleDeleteGlobalAsset = (asset: SceneGlobalAssetReference) => {
    if (activeSupervisorVideoTarget?.ui.kind === "video_scene_package_review") {
      void submitSupervisorAction(
        "删除视频全局素材",
        buildSupervisorWorkflowAction({
          action: "modify_workflow",
          intent: "video",
          workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
          stage: activeSupervisorVideoTarget.stage,
          artifactRef: activeSupervisorVideoTarget.artifactRef,
          patch: {
            asset_action: "delete",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
          },
        }),
        {
          artifactRefs: activeSupervisorVideoTarget.artifactRef
            ? [activeSupervisorVideoTarget.artifactRef]
            : [],
        },
      );
      return;
    }
    const material = selectedStoryboardMessageId
      ? { ...asset, conversation_id: currentConversationId, storyboard_message_id: selectedStoryboardMessageId }
      : { ...asset, conversation_id: currentConversationId };
    const deleteMaterial = { ...material, scene_global_asset_action: "delete" as const };
    setReferencedMaterials((items) => {
      const next = items.filter((item) => item.asset_id !== deleteMaterial.asset_id);
      return [deleteMaterial, ...next].slice(0, 1);
    });
    setComposerPrefillRequest({ id: uid(), content: SCENE_GLOBAL_ASSET_DELETE_PROMPT(deleteMaterial.name) });
  };

  const startSceneGlobalAssetRevision = async (
    reference: SceneGlobalAssetReference,
    operation: "replace" | "delete",
    replacement?: SceneGlobalAssetReplacement,
    options: { processedKey?: string; reviewMessageId?: string; targetConversationId?: string } = {},
  ): Promise<boolean> => {
    const targetConversationId = options.targetConversationId || currentConversationId || conversationIdRef.current;
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      if (options.processedKey) releaseArtifactAction(options.processedKey);
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后重试。", targetConversationId);
      return false;
    }
    const request: ScenePackageAssetRevisionRequest = {
      operation,
      asset_id: reference.asset_id,
      asset_group: reference.asset_group,
      asset_name: replacement?.assetName || reference.name,
      source_image_url: reference.source_image_url,
      new_image_url: replacement?.displayImageUrl || null,
      generation_reference_url: replacement?.generationReferenceUrl || null,
      replacement_metadata: replacement
        ? {
            replacement_source: replacement.source,
            third_asset_id: replacement.thirdAssetId,
            replacement_asset_type: replacement.assetType,
            replacement_asset_id: replacement.contentAssetId,
            replacement_asset_name: replacement.assetName,
          }
        : {},
      global_assets: storyboardMessage.artifact.videoScenePackages.global_assets,
      scene_packages: storyboardMessage.artifact.videoScenePackages.scene_packages,
    };

    try {
      setBusyForConversation(targetConversationId, true);
      if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
        notifyLegacyVideoJobBlocked(targetConversationId, options.processedKey || "");
        return false;
      }
      pushAssistant(
        operation === "replace"
          ? "正在分析新素材，并同步更新受影响的分镜内容…"
          : "正在移除素材，并同步清理各分镜中的引用和相关描述…",
        targetConversationId,
      );
      const started = await api.startScenePackageAssetRevisionJob(request);
      const pendingScenePackageJob: PendingScenePackageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: storyboardMessage.id,
        kind: "scene_asset_revision",
        started_at: new Date().toISOString(),
        request,
        artifact: storyboardMessage.artifact,
        review_message_id: options.reviewMessageId,
      };
      await persistPendingScenePackageJob(pendingScenePackageJob, targetConversationId, "scene_asset_revision_running", {
        scene_global_asset_revision: {
          operation,
          asset_id: reference.asset_id,
          asset_group: reference.asset_group,
        },
      });
      await resumePendingScenePackageJob(pendingScenePackageJob, options.processedKey || "");
      return true;
    } catch (err) {
      if (options.processedKey) releaseArtifactAction(options.processedKey);
      pushAssistant(`分镜素材修改失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      return false;
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleReplaceGlobalAsset = (asset: SceneGlobalAssetReference, replacement: SceneGlobalAssetReplacement) => {
    const reference = selectedStoryboardMessageId ? { ...asset, storyboard_message_id: selectedStoryboardMessageId } : asset;
    void startSceneGlobalAssetRevision(reference, "replace", replacement);
  };

  const handleAddGlobalAsset = (
    msg: ChatMessage,
    assetGroup: GlobalSceneAssetGroup,
    replacement: SceneGlobalAssetReplacement,
  ) => {
    const targetConversationId = messageConversationId(msg, currentConversationId || conversationIdRef.current);
    const storyboardMessage = messagesRef.current.find(
      (item) =>
        item.id === msg.id &&
        messageConversationId(item, targetConversationId) === targetConversationId &&
        Boolean(item.artifact?.videoScenePackages),
    ) || msg;
    const artifact = storyboardMessage.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    if (!artifact || !videoScenePackages) {
      pushAssistant("当前没有找到可添加素材的场景包，请先打开最新的场景包卡片。", targetConversationId);
      return;
    }

    const added = addGlobalSceneAssetReference(videoScenePackages.global_assets, {
      assetGroup,
      manualId: uid(),
      replacement,
    });
    const updatedPackages: PrepareScenePackagesResponse = {
      ...videoScenePackages,
      global_assets: added.global_assets,
    };
    const updatedArtifact: ChatArtifact = {
      ...artifact,
      videoScenePackages: updatedPackages,
    };

    updateVideoScenePackageArtifactInMessage(storyboardMessage.id, () => updatedPackages);
    if (targetConversationId) {
      void api.updateConversationMessage(targetConversationId, storyboardMessage.id, {
        content: storyboardMessage.content,
        payload: {
          artifact: updatedArtifact,
          materials: updatedArtifact.materials || [],
          client_message_id: storyboardMessage.id,
        } as unknown as Record<string, unknown>,
      }).catch(() => {});
    }
    persistScenePackageSnapshot(targetConversationId, updatedPackages, "scene_global_asset_added", {
      scene_global_asset_addition: {
        asset_id: added.added_asset.asset_id,
        asset_group: assetGroup,
        name: added.added_asset.name,
        replacement_source: replacement.source,
        manual_added: true,
      },
    });
  };

  const handleRemoveReferencedMaterial = (key: string) => {
    setReferencedMaterials((items) => items.filter((item) => sceneGlobalAssetMaterialKey(item) !== key));
  };

  const sceneGlobalAssetEditRatio = async (
    reference: SceneGlobalAssetReference,
    videoScenePackages: PrepareScenePackagesResponse,
    modelConfigs: ImageModelParamConfig[],
  ): Promise<string> => {
    const options = imageModelOptions(preferredImageEditConfig(modelConfigs));
    const assetRecord = globalSceneAssetRecord(videoScenePackages, reference) || reference;
    const metadataRatio = globalSceneAssetRatioFromMetadata(assetRecord, options.ratios);
    if (metadataRatio) return metadataRatio;
    const fallbackRatio = inferGlobalSceneAssetRatioFromMetadata(assetRecord, reference.asset_group, options.ratios);
    const naturalSize = await imageNaturalSize(reference.source_image_url);
    if (naturalSize) return nearestSupportedAspectRatio(naturalSize.width, naturalSize.height, options.ratios, fallbackRatio);
    return fallbackRatio;
  };

  const pushSceneGlobalAssetEditOptions = async (
    reference: SceneGlobalAssetReference,
    prompt: string,
    targetConversationId: string,
    materials: Array<Record<string, unknown>> = [],
  ): Promise<boolean> => {
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId);
    const videoScenePackages = storyboardMessage?.artifact?.videoScenePackages;
    if (!storyboardMessage?.artifact || !videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再编辑。", targetConversationId);
      return true;
    }
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      pushAssistant("已引用素材，请在输入框里写清楚要怎么修改这张图片。", targetConversationId);
      return true;
    }

    const uploadedReferences = uploadedReferenceMaterials(materials);
    const mode = uploadedReferences.length >= 1 ? "scene_global_asset_fusion" : "scene_global_asset_edit";
    setReferencedMaterials((items) => items.filter((item) => item.asset_id !== reference.asset_id));
    setBusyForConversation(targetConversationId, true);
    pushAssistant("已引用场景包素材，正在读取可用图片模型和参数配置…", targetConversationId);
    let modelConfigs: ImageModelParamConfig[] = [];
    try {
      modelConfigs = await api.listImageGenerateModelConfigs();
    } catch (err) {
      modelConfigs = [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
      pushAssistant(`图片模型配置读取失败，已使用默认模型 image-2。${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }

    const normalizedConfigs = modelConfigs.length > 0 ? modelConfigs : [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
    const preferredConfig = preferredImageEditConfig(normalizedConfigs);
    const options = imageModelOptions(preferredConfig);
    const selection: ImageEditModelSelection = {
      model: imageModelType(preferredConfig) || "gpt-image-2",
      ratio: await sceneGlobalAssetEditRatio(reference, videoScenePackages, normalizedConfigs),
      size: options.sizes[0] || "4K",
    };
    const request: PendingImageEditRequest = {
      conversationId: targetConversationId,
      prompt: cleanPrompt,
      formValues: {
        image_goal: reference.name,
        image_operation: "image_edit",
        image_model: selection.model,
        image_size: selection.ratio,
        image_quality: selection.size,
      },
      intakeContext: {
        image_operation: "image_edit",
        image_model: selection.model,
        image_size: selection.ratio,
        image_quality: selection.size,
        scene_global_asset_reference: reference,
      },
      materials: uploadedReferences,
      selection,
      mode,
      sceneGlobalAssetReference: reference,
      storyboardMessageId: storyboardMessage.id,
    };
    pendingImageEditRequestRef.current = request;
    pushArtifact("全局素材图片编辑参数已准备好，请确认后开始编辑。", {
      type: "image_edit_options",
      title: mode === "scene_global_asset_fusion" ? "全局素材融合参数确认" : "全局素材编辑参数确认",
      description: "选择图片编辑模型、原图比例和清晰度。编辑结果成功后，需要你确认才会替换场景包素材。",
      actionLabel: "确认",
      intent: "image",
      formValues: request.formValues,
      intakeContext: request.intakeContext,
      materials: [{ ...reference, source_image_url: reference.source_image_url, url: reference.source_image_url }, ...uploadedReferences],
      imageEditRequest: request as unknown as Record<string, unknown>,
      imageEditModelConfigs: normalizedConfigs,
      imageEditRequestedParams: {
        ratio: selection.ratio,
        size: selection.size,
      },
    }, targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "scene_global_asset_edit_model_pending",
          context: {
            ...makeSnapshot(),
            pendingImageEditRequest: request,
            pending_image_edit_request: request,
            scene_global_asset_reference: reference,
            scene_global_asset_edit_prompt: cleanPrompt,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
    return true;
  };

  const handleEditReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    prompt: string,
    targetConversationId: string,
    materials: Array<Record<string, unknown>> = [],
  ): Promise<boolean> => {
    return pushSceneGlobalAssetEditOptions(reference, prompt, targetConversationId, materials);
  };

  const executeSceneGlobalAssetEdit = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const reference = request.sceneGlobalAssetReference;
    if (!reference) return;
    const storyboardMessage = findStoryboardMessageForGlobalAsset(reference, targetConversationId, [request.storyboardMessageId || ""]);
    if (!storyboardMessage?.artifact?.videoScenePackages) {
      pushAssistant("当前没有找到包含这个全局素材的场景包，请先打开对应的场景包卡片后再编辑。", targetConversationId);
      return;
    }
    const uploadedReferences = uploadedReferenceMaterials(request.materials);
    const shouldFuseAsset = request.mode === "scene_global_asset_fusion" || uploadedReferences.length >= 1;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(
      shouldFuseAsset
        ? `正在融合引用素材和 ${uploadedReferences.length} 张上传图片，生成「${reference.name}」的候选新图…`
        : `正在调用图片编辑接口生成「${reference.name}」的候选新图…`,
      targetConversationId,
    );
    try {
      const jobRequest: ImageAssetEditJobRequest | ImageAssetFusionJobRequest = {
        asset_id: reference.asset_id,
        asset_name: reference.name,
        asset_group: reference.asset_group,
        source_image_url: reference.source_image_url,
        prompt: request.prompt,
        materials: uploadedReferences,
        ratio: request.selection?.ratio,
        size: request.selection?.size,
        model: request.selection?.model,
        conversation_id: targetConversationId,
      };
      const started = shouldFuseAsset ? await api.startImageAssetFusionJob(jobRequest) : await api.startImageAssetEditJob(jobRequest);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: storyboardMessage.id,
        kind: shouldFuseAsset ? "scene_global_asset_fusion" : "scene_global_asset_edit",
        job_api: shouldFuseAsset ? "fuse_asset" : "edit_asset",
        started_at: new Date().toISOString(),
        request: jobRequest,
        artifact: storyboardMessage.artifact,
        sceneGlobalAssetReference: reference,
        storyboard_message_id: storyboardMessage.id,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, shouldFuseAsset ? "scene_global_asset_fusion_running" : "scene_global_asset_edit_running", {
        scene_global_asset_reference: reference,
        scene_global_asset_edit_prompt: request.prompt,
        scene_global_asset_fusion: shouldFuseAsset,
        pendingImageEditRequest: null,
        pending_image_edit_request: null,
      });
      pendingImageEditRequestRef.current = null;
      await resumePendingImageJob(pendingImageJob);
    } catch (err) {
      pushAssistant(`全局素材图片编辑失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleDeleteReferencedGlobalAsset = async (
    reference: SceneGlobalAssetReference,
    targetConversationId: string,
  ): Promise<boolean> => {
    return startSceneGlobalAssetRevision(reference, "delete", undefined, { targetConversationId });
  };

  const showImageEditOptions = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const flowMaterials = request.materials || [];
    if (!hasImageMaterial(flowMaterials)) {
      pendingImageEditRequestRef.current = request;
      pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "image_edit_waiting_source_image",
            context: {
              ...makeSnapshot(),
              intent: "image",
              pendingImageEditRequest: pendingImageEditRequestRef.current,
              pending_image_edit_request: pendingImageEditRequestRef.current,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      return;
    }

    setBusyForConversation(targetConversationId, true);
    pushAssistant("已识别为图片编辑需求，正在读取可用图片模型和参数配置…", targetConversationId);
    let modelConfigs: ImageModelParamConfig[] = [];
    try {
      modelConfigs = await api.listImageGenerateModelConfigs();
    } catch (err) {
      modelConfigs = [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
      pushAssistant(`图片模型配置读取失败，已使用默认模型 image-2。${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
    const normalizedConfigs = modelConfigs.length > 0 ? modelConfigs : [DEFAULT_IMAGE_EDIT_MODEL_CONFIG];
    pendingImageEditRequestRef.current = request;
    pushArtifact("图片编辑模型和参数已准备好，请确认后开始编辑。", {
      type: "image_edit_options",
      title: "图片编辑参数确认",
      description: "选择图片编辑模型。若需求里明确了尺寸或清晰度，所选模型必须支持后才能提交。",
      actionLabel: "确认",
      intent: "image",
      formValues: request.formValues,
      intakeContext: request.intakeContext,
      materials: flowMaterials,
      imageEditRequest: request as unknown as Record<string, unknown>,
      imageEditModelConfigs: normalizedConfigs,
      imageEditRequestedParams: {
        ratio: request.selection?.ratio || request.formValues.image_size || request.intakeContext.image_size || "",
        size: request.selection?.size || request.formValues.image_quality || request.intakeContext.image_quality || "",
      },
    }, targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "image_edit_model_pending",
          context: {
            ...makeSnapshot(),
            intent: "image",
            materials: flowMaterials,
            pendingImageEditRequest: pendingImageEditRequestRef.current,
            pending_image_edit_request: pendingImageEditRequestRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const executeDirectImageEdit = async (request: PendingImageEditRequest): Promise<void> => {
    const targetConversationId = request.conversationId;
    const flowMaterials = request.materials || [];
    const formValues = directImageEditFormValues(request.prompt, request.formValues, request.intakeContext, request.selection);
    const intakeContext = {
      ...request.intakeContext,
      image_operation: "image_edit",
      image_model: request.selection?.model || request.intakeContext.image_model || formValues.image_model,
      image_size: request.selection?.ratio || request.intakeContext.image_size || formValues.image_size,
      image_quality: request.selection?.size || request.intakeContext.image_quality || formValues.image_quality,
      requested_output_count: request.intakeContext.requested_output_count || formValues.image_count || 1,
    };
    const executableRequest: PendingImageEditRequest = { ...request, formValues, intakeContext };
    pendingImageEditRequestRef.current = executableRequest;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "image_edit_generation_running", {
      intent: "image",
      flow_kind: "direct_image_edit",
    });
    pushAssistant("已识别为图片编辑需求，正在直接调用图片编辑接口生成结果…", targetConversationId);
    try {
      const imagePrepare = await api.prepareImageGeneration({
        form_values: formValues,
        plan_markdown: "",
        selected_direction: {
          title: "图片编辑",
          description: request.prompt,
          operation: "image_edit",
        },
        materials: flowMaterials,
        intake_context: intakeContext,
      });
      if (!imagePrepare.ok) {
        pendingImageEditRequestRef.current = { ...request, formValues, intakeContext };
        pushArtifact("图片编辑需要先补充原图，请上传后我会继续。", {
          type: "image_prepare",
          title: "图片编辑准备",
          description: imagePrepare.message,
          actionLabel: "查看",
          imagePrepare,
          intent: "image",
          formValues,
          intakeContext,
          materials: flowMaterials,
          selectedDirection: {
            direction_id: "image_edit",
            title: "图片编辑",
            description: request.prompt,
            recommended: true,
            tags: ["图片编辑"],
            data: { operation: "image_edit" },
          },
        }, targetConversationId);
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "image_edit_waiting_source_image",
            context: {
              ...makeSnapshot(),
              intent: "image",
              pendingImageEditRequest: pendingImageEditRequestRef.current,
              pending_image_edit_request: pendingImageEditRequestRef.current,
              image_prepare: imagePrepare,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
        return;
      }
      pushAssistant(`正在调用 ${imagePrepare.endpoint} 编辑图片…`, targetConversationId);
      const jobRequest: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
        conversation_id: targetConversationId,
      };
      const selectedDirection = {
        direction_id: "image_edit",
        title: "图片编辑",
        description: request.prompt,
        recommended: true,
        tags: ["图片编辑"],
        data: { operation: "image_edit" },
      };
      const started = await api.startImageGenerationJob(jobRequest);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: "",
        kind: "direct_image_edit",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request: jobRequest,
        imagePrepare,
        artifact: {
          type: "image_result",
          title: "图片编辑结果",
          description: "图片编辑生成中。",
          actionLabel: "查看",
          imagePrepare,
          imageEditRequest: executableRequest as unknown as Record<string, unknown>,
          imageEditConfirmedSelection: request.selection,
          intent: "image",
          formValues,
          intakeContext,
          materials: flowMaterials,
          selectedDirection,
        },
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_edit_generation_running", {
        intent: "image",
        image_edit_request: executableRequest,
        intake_context: intakeContext,
        materials: flowMaterials,
        image_prepare: imagePrepare,
        pendingImageEditRequest: pendingImageEditRequestRef.current,
        pending_image_edit_request: pendingImageEditRequestRef.current,
      });
      await resumePendingImageJob(pendingImageJob);
    } catch (err) {
      pendingImageEditRequestRef.current = null;
      pushAssistant(`图片编辑失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleConfirmImageEditOptions = async (msg: ChatMessage, selection: ImageEditModelSelection) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "image_edit_options" || !artifact.imageEditRequest) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const storedRequest = artifact.imageEditRequest as Partial<PendingImageEditRequest>;
    const request: PendingImageEditRequest = {
      conversationId: targetConversationId,
      prompt: String(storedRequest.prompt || artifact.coreMessage || msg.content || ""),
      formValues: (storedRequest.formValues || artifact.formValues || {}) as Record<string, unknown>,
      intakeContext: (storedRequest.intakeContext || artifact.intakeContext || {}) as Record<string, unknown>,
      materials: (storedRequest.materials || artifact.materials || []) as Array<Record<string, unknown>>,
      selection,
      mode: storedRequest.mode,
      sceneGlobalAssetReference: storedRequest.sceneGlobalAssetReference as SceneGlobalAssetReference | undefined,
      storyboardMessageId: typeof storedRequest.storyboardMessageId === "string" ? storedRequest.storyboardMessageId : undefined,
    };
    recordImageEditConfirmedSelection(msg.id, targetConversationId, selection);
    pendingImageEditRequestRef.current = null;
    if (request.mode === "scene_global_asset_edit" || request.mode === "scene_global_asset_fusion") {
      await executeSceneGlobalAssetEdit(request);
      return;
    }
    await executeDirectImageEdit(request);
  };

  const pushDirectionsArtifact = (
    directions: CreativeDirectionResponse[],
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
  ) => {
    return pushArtifact("已根据表单生成 3 个创意方向，请选择一个进入 plan.md 策划。", {
      type: "directions",
      title: "创意方向",
      description: `${directions.length} 个方向，第一项为推荐方向`,
      actionLabel: "查看",
      directions,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    }, targetConversationId);
  };

  const createPlanArtifactMessage = (
    plan: PlanMarkdownResponse,
    selectedDirection: CreativeDirectionResponse,
    context: {
      intent: CreationIntent;
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
    },
    targetConversationId = conversationIdRef.current,
    planJobId?: string,
  ) => ({
    id: uid(),
    conversationId: targetConversationId || undefined,
    role: "assistant" as const,
    content: "plan.md 创作方案已生成，请审核后点击「同意方案」继续。",
    time: "",
    artifact: {
      type: "plan" as const,
      title: "plan.md 创作方案",
      description: `基于「${selectedDirection.title}」生成，当前版本 v${plan.plan_version || 1}`,
      actionLabel: "审核",
      plan,
      planVersion: plan.plan_version || 1,
      planHistory: plan.plan_history || [],
      planJobId,
      creationContract: plan.creation_contract || {},
      restoredFromVersion: plan.restored_from_version,
      selectedDirection,
      intent: context.intent,
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
    },
  });

  const pushPptOutlineArtifact = (
    pptSummary: PptSummaryResult,
    context: {
      formValues: Record<string, unknown>;
      coreMessage: string;
      materials?: Array<Record<string, unknown>>;
      intakeContext?: Record<string, unknown>;
      pptStyle: string;
    },
    targetConversationId = conversationIdRef.current,
  ) => {
    const projectId = numericValue(pptSummary.smart_ppt_project_id);
    return pushArtifact(pptSummary.ok ? "PPT 大纲已生成，请确认是否需要修改。" : "PPT 大纲生成失败，请查看错误信息。", {
      type: "ppt_outline",
      title: "PPT大纲",
      description: String(pptSummary.message || (pptSummary.ok ? "确认后将生成 PPT 页面结构和页面图片。" : "可充值或调整附件后继续。")),
      actionLabel: "审核",
      intent: "ppt",
      formValues: context.formValues,
      intakeContext: context.intakeContext,
      materials: context.materials || [],
      coreMessage: context.coreMessage,
      pptSummary,
      pptStyle: context.pptStyle,
      smartPptProjectId: projectId,
    }, targetConversationId);
  };

  const pushPptImagesArtifact = (
    pptImages: PptImagesResult,
    pptContentJson: PptContentJsonResult,
    sourceArtifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
  ) =>
    pushArtifact(buildPptImagesMessageContent(pptImages), buildPptImagesArtifact(pptImages, sourceArtifact, pptContentJson), targetConversationId);

  const pushPptFileArtifact = (
    pptFile: PptFileResult,
    sourceArtifact: ChatArtifact,
    targetConversationId = conversationIdRef.current,
  ) =>
    pushArtifact(pptFile.ok ? "PPT 附件已生成，请下载确认。" : "PPT 附件生成失败，请查看原因。", {
      type: "ppt_file",
      title: "PPT附件",
      description: String(pptFile.message || (pptFile.ok ? "文件生成完成。" : "文件生成失败。")),
      actionLabel: "下载",
      intent: "ppt",
      formValues: sourceArtifact.formValues,
      intakeContext: sourceArtifact.intakeContext,
      materials: sourceArtifact.materials || [],
      coreMessage: sourceArtifact.coreMessage,
      pptSummary: sourceArtifact.pptSummary,
      pptContentJson: sourceArtifact.pptContentJson,
      pptImages: sourceArtifact.pptImages,
      pptFile,
      pptStyle: sourceArtifact.pptStyle,
      smartPptProjectId: pptProjectId(sourceArtifact) ?? numericValue(pptFile.smart_ppt_project_id),
    }, targetConversationId);

  const persistPendingSupervisorTurns = async (
    updater: (
      current: PendingSupervisorTurn[],
    ) => PendingSupervisorTurn[],
    targetConversationId: string,
  ) => {
    await pendingSupervisorTurnWritesRef.current.run(
      targetConversationId,
      async () => {
        const current = (
          pendingSupervisorTurnsByConversationRef.current.get(
            targetConversationId,
          )
          || pendingSupervisorTurnsRef.current.filter(
            (item) => item.conversationId === targetConversationId,
          )
        );
        const normalized = updater([...current]).filter(
          (item) => item.conversationId === targetConversationId,
        );
        const completedLegacyTurn = current.find(
          (item) => item.conversationId === targetConversationId
            && item.continueLegacy
            && item.registrationStatus === "registered"
            && !normalized.some((next) => next.clientInputId === item.clientInputId),
        );
        await updateConversationWithProgress(targetConversationId, {
          context: {
            ...makeSnapshot(targetConversationId),
            pendingAgentRuntimeTurns: normalized,
            pending_agent_runtime_turns: normalized,
            ...(completedLegacyTurn
              ? {
                  legacy_handoff: {
                    source: "frontend_v2",
                    client_input_id: completedLegacyTurn.clientInputId,
                  },
                }
              : {}),
          } as unknown as Record<string, unknown>,
        });
        // 只有服务端恢复上下文落库后才向注册 effect 暴露 Turn；
        // PUT 失败时保持原 ref/state，避免出现已注册但刷新不可恢复的半状态。
        pendingSupervisorTurnsByConversationRef.current.set(
          targetConversationId,
          normalized,
        );
        if (isCurrentConversation(targetConversationId)) {
          pendingSupervisorTurnsRef.current = normalized;
          setPendingSupervisorTurns(normalized);
        }
      },
    );
  };

  const pendingSupervisorTurnsForConversation = (
    targetConversationId: string,
  ): PendingSupervisorTurn[] => (
    pendingSupervisorTurnsByConversationRef.current.get(
      targetConversationId,
    )
    || pendingSupervisorTurnsRef.current.filter(
      (item) => item.conversationId === targetConversationId,
    )
  );

  const persistPendingMessageJob = async (
    pendingMessageJob: PendingMessageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    if (!targetConversationId) {
      pendingMessageJobRef.current = pendingMessageJob;
      return;
    }
    await pendingSupervisorTurnWritesRef.current.run(
      targetConversationId,
      async () => {
        const currentRuntimeTurns = pendingSupervisorTurnsForConversation(
          targetConversationId,
        );
        const runtimeTurns = (
          pendingMessageJob?.continue_after_save?.type === "handle_send"
            ? currentRuntimeTurns.filter(
                (item) => (
                  item.clientInputId
                  !== pendingMessageJob.source_message_id
                ),
              )
            : currentRuntimeTurns
        );
        await updateConversationWithProgress(targetConversationId, {
          last_phase: lastPhase,
          context: {
            ...makeSnapshot(targetConversationId),
            ...extraContext,
            pendingMessageJob,
            pending_message_job: pendingMessageJob,
            pendingAgentRuntimeTurns: runtimeTurns,
            pending_agent_runtime_turns: runtimeTurns,
          } as unknown as Record<string, unknown>,
        });
        pendingMessageJobRef.current = pendingMessageJob;
        pendingSupervisorTurnsByConversationRef.current.set(
          targetConversationId,
          runtimeTurns,
        );
        if (isCurrentConversation(targetConversationId)) {
          pendingSupervisorTurnsRef.current = runtimeTurns;
          setPendingSupervisorTurns(runtimeTurns);
        }
      },
    );
  };

  const clearPendingMessageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingMessageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingIntakeJob = async (
    pendingIntakeJob: PendingIntakeJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingIntakeJobRef.current = pendingIntakeJob;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingIntakeJob,
        pending_intake_job: pendingIntakeJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingIntakeJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingIntakeJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingImageJob = async (
    pendingImageJob: PendingImageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingImageJobRef.current = pendingImageJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingImageJob,
        pending_image_job: pendingImageJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingImageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingImageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingVideoJob = async (
    pendingVideoJob: PendingVideoJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingVideoJobRef.current = pendingVideoJob;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingVideoJob,
        pending_video_job: pendingVideoJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingVideoJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingVideoJob(null, targetConversationId, lastPhase, extraContext);
  };

  const jianyingDraftRecordsForConversation = (targetConversationId: string): JianyingDraftRecordMap =>
    jianyingDraftRecordsByConversationRef.current.get(targetConversationId) || {};

  const setJianyingDraftRecordsForConversation = (targetConversationId: string, records: JianyingDraftRecordMap) => {
    if (!targetConversationId) return;
    jianyingDraftRecordsByConversationRef.current.set(targetConversationId, records);
    setJianyingDraftUiRevision((revision) => revision + 1);
  };

  const patchJianyingDraftConversationContextForTarget = async (
    targetConversationId: string,
    lastPhase: string,
    expectedJobId: string,
    pendingJianyingDraftJob: PendingJianyingDraftJob | null,
    jianyingDraftRecordUpdates: JianyingDraftRecordMap,
    jianyingDraftJobResumeError?: string | null,
  ) => {
    let resolvedPendingJob = pendingJianyingDraftJob;
    let resolvedRecords = {
      ...jianyingDraftRecordsForConversation(targetConversationId),
      ...jianyingDraftRecordUpdates,
    };
    await patchJianyingDraftTargetConversation({
      targetConversationId,
      expectedJobId,
      isCurrentConversation: (conversationId) => conversationIdRef.current === conversationId,
      syncCurrentConversation: () => {
        pendingJianyingDraftJobRef.current = resolvedPendingJob;
        setJianyingDraftRecordsForConversation(targetConversationId, resolvedRecords);
      },
      patchTargetConversation: async (conversationId, expectedJobId) => {
        const updated = await api.patchJianyingDraftConversationContext(conversationId, {
          last_phase: lastPhase,
          expected_job_id: expectedJobId,
          pendingJianyingDraftJob,
          jianyingDraftRecords: jianyingDraftRecordUpdates,
          ...(jianyingDraftJobResumeError === undefined
            ? {}
            : { jianying_draft_job_resume_error: jianyingDraftJobResumeError }),
        });
        const context = updated.context as Partial<WorkspaceSnapshot>;
        resolvedPendingJob = context.pendingJianyingDraftJob ?? context.pending_jianying_draft_job ?? null;
        resolvedRecords = context.jianyingDraftRecords || context.jianying_draft_records || {};
        setJianyingDraftRecordsForConversation(conversationId, resolvedRecords);
        return updated;
      },
    });
  };

  const persistPendingJianyingDraftJob = async (
    pendingJianyingDraftJob: PendingJianyingDraftJob | null,
    targetConversationId: string,
    lastPhase: string,
    expectedJobId: string,
    jianyingDraftRecordUpdates: JianyingDraftRecordMap = {},
    jianyingDraftJobResumeError?: string | null,
  ) => {
    if (pendingJianyingDraftJob && pendingJianyingDraftJob.conversation_id !== targetConversationId) return;
    if (!targetConversationId) return;
    if (conversationIdRef.current === targetConversationId) {
      pendingJianyingDraftJobRef.current = pendingJianyingDraftJob;
      setJianyingDraftUiRevision((revision) => revision + 1);
    }
    await patchJianyingDraftConversationContextForTarget(
      targetConversationId,
      lastPhase,
      expectedJobId,
      pendingJianyingDraftJob,
      jianyingDraftRecordUpdates,
      pendingJianyingDraftJob ? null : jianyingDraftJobResumeError,
    );
  };

  const persistPendingScenePackageJob = async (
    pendingScenePackageJob: PendingScenePackageJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    // 启动时立即暴露恢复句柄；结束时必须等权威 context 成功落库后再清空，
    // 防止消息/画布自动保存拿着旧 running 阶段覆盖已经完成的任务。
    if (pendingScenePackageJob) pendingScenePackageJobRef.current = pendingScenePackageJob;
    if (!targetConversationId) {
      if (!pendingScenePackageJob) pendingScenePackageJobRef.current = null;
      return;
    }
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingScenePackageJob,
        pending_scene_package_job: pendingScenePackageJob,
      } as unknown as Record<string, unknown>,
    });
    if (
      !pendingScenePackageJob
      && pendingScenePackageJobRef.current?.conversation_id === targetConversationId
    ) {
      pendingScenePackageJobRef.current = null;
    }
  };

  const clearPendingScenePackageJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingScenePackageJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingPptJob = async (
    pendingPptJob: PendingPptJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingPptJobRef.current = pendingPptJob;
    if (!targetConversationId) return;
    const baseSnapshot = makeSnapshot(targetConversationId);
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...baseSnapshot,
        ...extraContext,
        pendingPptJob,
        pending_ppt_job: pendingPptJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingPptJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingPptJob(null, targetConversationId, lastPhase, extraContext);
  };

  const persistPendingDirectionJob = async (
    pendingDirectionJob: PendingDirectionJob | null,
    targetConversationId: string,
    lastPhase: string,
    flowDraft: FlowDraft | null = flowDraftRef.current,
    extraContext: Record<string, unknown> = {},
  ) => {
    pendingDirectionJobRef.current = pendingDirectionJob;
    flowDraftRef.current = flowDraft;
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        flowDraft,
        pendingDirectionJob,
        pending_direction_job: pendingDirectionJob,
      } as unknown as Record<string, unknown>,
    });
  };

  const clearPendingDirectionJob = async (
    targetConversationId: string,
    lastPhase: string,
    flowDraft: FlowDraft | null = flowDraftRef.current,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingDirectionJob(null, targetConversationId, lastPhase, flowDraft, extraContext);
  };

  const planJobPersistenceKey = (pendingPlanJob: PendingPlanJob) =>
    `${pendingPlanJob.conversation_id}:${pendingPlanJob.kind}:${pendingPlanJob.job_id}`;

  const clearPlanJobPersistenceState = (pendingPlanJob: PendingPlanJob) => {
    const key = planJobPersistenceKey(pendingPlanJob);
    planJobPersistenceAttemptsRef.current.delete(key);
    planJobPersistenceNoticesRef.current.delete(key);
    scheduledPlanJobPersistenceRef.current.delete(key);
  };

  const notifyPlanJobPersistenceRecovery = (pendingPlanJob: PendingPlanJob) => {
    const key = planJobPersistenceKey(pendingPlanJob);
    if (planJobPersistenceNoticesRef.current.has(key)) return;
    planJobPersistenceNoticesRef.current.add(key);
    pushAssistant(
      "Plan 任务已经启动，但恢复句柄暂未同步到对话；正在继续查询原任务并重试同步，不会重复生成。",
      pendingPlanJob.conversation_id,
    );
  };

  const persistPendingPlanJob = async (
    pendingPlanJob: PendingPlanJob | null,
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    const previousPendingPlanJob = pendingPlanJobRef.current;
    pendingPlanJobRef.current = pendingPlanJob;
    if (
      !pendingPlanJob
      && previousPendingPlanJob?.conversation_id === targetConversationId
    ) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        previousPendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(previousPendingPlanJob);
    }
    if (!targetConversationId) return;
    await updateConversationWithProgress(targetConversationId, {
      last_phase: lastPhase,
      context: {
        ...makeSnapshot(targetConversationId),
        ...extraContext,
        pendingPlanJob,
        pending_plan_job: pendingPlanJob,
      } as unknown as Record<string, unknown>,
    });
    if (pendingPlanJob) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    }
  };

  const schedulePendingPlanJobPersistence = (
    pendingPlanJob: PendingPlanJob,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ): void => {
    if (!shouldRetryPlanJobPersistence({
      hidden: !pageVisibleRef.current,
      startedAt: pendingPlanJob.started_at,
    })) {
      return;
    }
    const key = planJobPersistenceKey(pendingPlanJob);
    if (scheduledPlanJobPersistenceRef.current.has(key)) return;
    const attempt = planJobPersistenceAttemptsRef.current.get(key) || 0;
    planJobPersistenceAttemptsRef.current.set(key, attempt + 1);
    scheduledPlanJobPersistenceRef.current.add(key);
    window.setTimeout(() => {
      scheduledPlanJobPersistenceRef.current.delete(key);
      if (!shouldRetryPlanJobPersistence({
        hidden: !pageVisibleRef.current,
        startedAt: pendingPlanJob.started_at,
      })) {
        return;
      }
      const current = pendingPlanJobRef.current;
      if (
        current?.job_id !== pendingPlanJob.job_id
        || current.conversation_id !== pendingPlanJob.conversation_id
      ) {
        return;
      }
      void persistPendingPlanJob(
        pendingPlanJob,
        pendingPlanJob.conversation_id,
        lastPhase,
        extraContext,
      ).catch(() => {
        schedulePendingPlanJobPersistence(pendingPlanJob, lastPhase, extraContext);
      });
    }, planJobResumeDelayMs(attempt));
  };

  const clearPendingPlanJob = async (
    targetConversationId: string,
    lastPhase: string,
    extraContext: Record<string, unknown> = {},
  ) => {
    await persistPendingPlanJob(null, targetConversationId, lastPhase, extraContext);
  };

  const handleCompletedDirectionJob = async (
    pendingDirectionJob: PendingDirectionJob,
    directionResult: CreativeDirectionsResponse,
  ) => {
    const targetConversationId = pendingDirectionJob.conversation_id;
    const context = pendingDirectionJob.context;
    if (!directionResult.validation.is_complete) {
      pushAssistant(directionResult.validation.message || "表单信息还不完整，请补充后再提交。", targetConversationId);
      const draft = makeFlowDraft("form_pending", {
        intent: context.intent,
        coreMessage: context.coreMessage,
        materials: context.materials || [],
        intakeContext: context.intakeContext,
        formValues: context.formValues,
        form: context.form,
      });
      await clearPendingDirectionJob(targetConversationId, "intake_form_pending", draft, {
        intent: context.intent,
        form_values: context.formValues,
        intake_context: context.intakeContext,
        materials: context.materials || [],
      }).catch(() => {});
      return;
    }
    if (hasPostDirectionArtifactForContext(messagesRef.current, targetConversationId, context)) {
      await clearPendingDirectionJob(targetConversationId, "plan_review", null, {
        intent: context.intent,
        form_values: context.formValues,
        intake_context: context.intakeContext,
        materials: context.materials || [],
      }).catch(() => {});
      return;
    }
    const intakeContext = directionResult.intake_context || context.intakeContext || {};
    const draft = makeFlowDraft("directions_ready", {
      intent: context.intent,
      coreMessage: context.coreMessage,
      materials: context.materials || [],
      intakeContext,
      formValues: context.formValues,
      form: context.form,
      creativeDirections: directionResult.creative_directions,
    });
    if (context.revisionFeedback || !hasDirectionsArtifactForDraft(messagesRef.current, targetConversationId, draft)) {
      pushDirectionsArtifact(directionResult.creative_directions, {
        intent: context.intent,
        formValues: context.formValues,
        materials: context.materials || [],
        coreMessage: context.coreMessage,
        intakeContext,
      }, targetConversationId);
    }
    await clearPendingDirectionJob(targetConversationId, context.revisionFeedback ? "creative_directions_revised" : `${context.intent}_directions`, draft, {
      intent: context.intent,
      [`${context.intent}_form`]: context.form,
      creative_directions: directionResult.creative_directions,
      form_values: context.formValues,
      intake_context: intakeContext,
      materials: context.materials || [],
      revision_feedback: context.revisionFeedback,
    }).catch(() => {});
  };

  const resumePendingDirectionJob = async (pendingDirectionJob: PendingDirectionJob) => {
    const pollKey = `${pendingDirectionJob.conversation_id}:${pendingDirectionJob.job_id}`;
    if (activeDirectionJobPollsRef.current.has(pollKey)) return;
    activeDirectionJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingDirectionJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingDirectionJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      const status = await api.getCreativeDirectionsJob(pendingDirectionJob.job_id);
      if (stopIfHidden()) return;
      const result =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollCreativeDirectionsJob(pendingDirectionJob.job_id, shouldContinuePolling);
      if (!result || stopIfHidden()) return;
      await handleCompletedDirectionJob(pendingDirectionJob, result);
    } catch (err) {
      if (stopIfHidden()) return;
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的创意方向生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从当前表单手动继续生成方向。"
          : `继续查询创意方向生成任务失败:${message}`,
        pendingDirectionJob.conversation_id,
      );
      const draft = flowDraftRef.current || makeFlowDraft("form_pending", {
        intent: pendingDirectionJob.context.intent,
        coreMessage: pendingDirectionJob.context.coreMessage,
        materials: pendingDirectionJob.context.materials || [],
        intakeContext: pendingDirectionJob.context.intakeContext,
        formValues: pendingDirectionJob.context.formValues,
        form: pendingDirectionJob.context.form,
      });
      await clearPendingDirectionJob(pendingDirectionJob.conversation_id, "direction_job_resume_failed", draft, {
        direction_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeDirectionJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingDirectionJob.conversation_id, false);
      void pausedForHiddenConversation;
    }
  };

  const startDirectionJob = async (
    targetConversationId: string,
    request: CreativeDirectionsJobRequest,
    context: PendingDirectionJobContext,
    lastPhase = "directions_running",
    sourceMessageId = "",
  ) => {
    advanceWorkflowProgress(targetConversationId, lastPhase, {
      intent: context.intent,
      flow_kind: "standard",
    });
    const flowDraft = makeFlowDraft("directions_running", {
      intent: context.intent,
      coreMessage: context.coreMessage,
      materials: context.materials || [],
      intakeContext: context.intakeContext,
      formValues: context.formValues,
      form: context.form,
    });
    const started = await api.startCreativeDirectionsJob(request);
    const pendingDirectionJob: PendingDirectionJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: sourceMessageId,
      kind: "creative_directions",
      started_at: new Date().toISOString(),
      request,
      context,
    };
    await persistPendingDirectionJob(pendingDirectionJob, targetConversationId, lastPhase, flowDraft, {
      intent: context.intent,
      form_values: context.formValues,
      intake_context: context.intakeContext,
      materials: context.materials || [],
      revision_feedback: context.revisionFeedback,
    });
    await resumePendingDirectionJob(pendingDirectionJob);
  };

  const handleCompletedIntakeJob = async (pendingIntakeJob: PendingIntakeJob, intake: IntakeIntentResponse) => {
    const targetConversationId = pendingIntakeJob.conversation_id;
    const text = pendingIntakeJob.request.prompt;
    const materials = pendingIntakeJob.request.materials || [];
    if (intake.intent === "video_analysis") {
      advanceWorkflowProgress(targetConversationId, "video_analysis_running", { intent: null });
      if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
        pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
        await clearPendingIntakeJob(targetConversationId, "video_analysis_failed", {
          video_analysis_blocked: true,
        }).catch(() => {});
        return;
      }
      pushAssistant("已识别为视频分析/拆解需求，正在识别媒体链接并调用视频分析 Skill…", targetConversationId);
      const videoAnalysis = await api.analyzeStoryboards({ prompt: text, materials });
      pushArtifact(videoAnalysis.ok ? "视频分析已完成，结果如下。" : "视频分析未完成，请查看原因后补充视频链接。", {
        type: "video_analysis_result",
        title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
        description: videoAnalysis.ok
          ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
          : videoAnalysis.message,
        actionLabel: "查看",
        intent: "video_analysis",
        coreMessage: text,
        materials,
        videoAnalysis,
      }, targetConversationId);
      await clearPendingIntakeJob(targetConversationId, videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed", {
        intent: "video_analysis",
        materials,
        intake_intent: intake,
        video_analysis: videoAnalysis,
      }).catch(() => {});
      return;
    }
    if (intake.intent === "ppt") {
      advanceWorkflowProgress(targetConversationId, "ppt_form_pending", { intent: "ppt", flow_kind: "standard" });
      const flowDraft = makeFlowDraft("form_pending", {
        intent: "ppt",
        coreMessage: text,
        materials,
        intakeIntent: intake,
        intakeContext: intake.intake_context,
        formValues: initialValuesFromIntake(intake),
      });
      flowDraftRef.current = flowDraft;
      if (isVisibleConversation(targetConversationId)) {
        setPendingCore(text);
        setPendingIntent("ppt");
        setPendingFormValues(initialValuesFromIntake(intake));
        setPendingMaterials(materials);
        pendingDialogContextRef.current = {
          conversationId: targetConversationId,
          coreMessage: text,
          materials,
          intakeContext: intake.intake_context,
        };
      }
      pushAssistant("采集 Agent 判断这是PPT制作需求，已把能识别的信息自动填进表单。请补充确认并上传 Word、Excel 或 PDF 附件。", targetConversationId);
      await clearPendingIntakeJob(targetConversationId, "ppt_form_pending", {
        flowDraft,
        intent: "ppt",
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
      return;
    }
    if (intake.intent === "image" && isImageEditIntake(intake, text)) {
      advanceWorkflowProgress(targetConversationId, "image_edit_options_pending", {
        intent: "image",
        flow_kind: "direct_image_edit",
      });
      const imageEditRequest: PendingImageEditRequest = {
        conversationId: targetConversationId,
        prompt: text,
        formValues: intake.values || {},
        intakeContext: intake.intake_context || {},
        materials,
      };
      if (!hasImageMaterial(materials)) {
        pendingImageEditRequestRef.current = imageEditRequest;
        pushAssistant("我识别到这是图片编辑需求，请上传需要编辑的图片后提交，我会先让你确认图片编辑模型和参数。", targetConversationId);
        await clearPendingIntakeJob(targetConversationId, "image_edit_waiting_source_image", {
          intent: "image",
          materials,
          intake_intent: intake,
          intake_context: intake.intake_context,
          pendingImageEditRequest: imageEditRequest,
          pending_image_edit_request: imageEditRequest,
        }).catch(() => {});
        return;
      }
      await clearPendingIntakeJob(targetConversationId, "image_edit_options_pending", {
        intent: "image",
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      await showImageEditOptions(imageEditRequest);
      return;
    }
    if (isCreationIntent(intake.intent)) {
      advanceWorkflowProgress(targetConversationId, "intake_form_pending", {
        intent: intake.intent,
        flow_kind: "standard",
      });
      const flowDraft = makeFlowDraft("form_pending", {
        intent: intake.intent,
        coreMessage: text,
        materials,
        intakeIntent: intake,
        intakeContext: intake.intake_context,
        formValues: initialValuesFromIntake(intake),
      });
      flowDraftRef.current = flowDraft;
      if (isVisibleConversation(targetConversationId)) {
        setPendingCore(text);
        setPendingIntent(intake.intent);
        setPendingFormValues(initialValuesFromIntake(intake));
        setPendingMaterials(materials);
        pendingDialogContextRef.current = {
          conversationId: targetConversationId,
          coreMessage: text,
          materials,
          intakeContext: intake.intake_context,
        };
      }
      pushAssistant(`采集 Agent 判断这是${intake.intent === "video" ? "视频生成" : "图片生成"}需求，已把能识别的信息自动填进表单。请补充确认。`, targetConversationId);
      await clearPendingIntakeJob(targetConversationId, "intake_form_pending", {
        flowDraft,
        intent: intake.intent,
        materials,
        intake_intent: intake,
        intake_context: intake.intake_context,
      }).catch(() => {});
      if (isVisibleConversation(targetConversationId)) setDialogOpen(true);
      return;
    }
    pushAssistant(intake.reason || "我可以帮你生成图片、生成电商带货短视频，或分析已有视频。请再描述一下需求。", targetConversationId);
    advanceWorkflowProgress(targetConversationId, "intake_unknown", { intent: null });
    await clearPendingIntakeJob(targetConversationId, "intake_unknown", {
      intake_intent: intake,
    }).catch(() => {});
  };

  const resumePendingIntakeJob = async (pendingIntakeJob: PendingIntakeJob) => {
    const pollKey = `${pendingIntakeJob.conversation_id}:${pendingIntakeJob.job_id}`;
    if (activeIntakeJobPollsRef.current.has(pollKey)) return;
    activeIntakeJobPollsRef.current.add(pollKey);
    const shouldContinuePolling = () => isVisibleConversation(pendingIntakeJob.conversation_id);
    const stopIfHidden = () => !shouldContinuePolling();
    setBusyForConversation(pendingIntakeJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      const status = await api.getIntakeAnalyzeJob(pendingIntakeJob.job_id);
      if (stopIfHidden()) return;
      const result =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollIntakeAnalyzeJob(pendingIntakeJob.job_id, shouldContinuePolling);
      if (!result || stopIfHidden()) return;
      await handleCompletedIntakeJob(pendingIntakeJob, result);
    } catch (err) {
      if (stopIfHidden()) return;
      if (isTransientFetchAbort(err)) return;
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的采集意图识别任务不存在或已过期。为避免重复推进流程，我没有自动重启任务，请重新发送需求。"
          : `继续查询采集意图识别任务失败:${message}`,
        pendingIntakeJob.conversation_id,
      );
      await clearPendingIntakeJob(pendingIntakeJob.conversation_id, "intake_job_resume_failed", {
        intake_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeIntakeJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingIntakeJob.conversation_id, false);
    }
  };

  const startIntakeAnalyzeJob = async (
    targetConversationId: string,
    request: IntakeAnalyzeJobRequest,
    sourceMessageId = "",
    autoResume = true,
  ): Promise<PendingIntakeJob> => {
    advanceWorkflowProgress(targetConversationId, "intake_analyze_running", {
      intent: null,
      flow_kind: "standard",
      source_message_id: sourceMessageId,
      scene_package_stage: null,
    });
    const started = await api.startIntakeAnalyzeJob(request);
    const pendingIntakeJob: PendingIntakeJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: sourceMessageId,
      kind: "intake_analyze",
      started_at: new Date().toISOString(),
      request,
    };
    await persistPendingIntakeJob(pendingIntakeJob, targetConversationId, "intake_analyze_running", {
      materials: request.materials || [],
      intake_prompt: request.prompt,
    });
    if (autoResume) await resumePendingIntakeJob(pendingIntakeJob);
    return pendingIntakeJob;
  };

  const resumePendingMessageJob = async (pendingMessageJob: PendingMessageJob) => {
    const pollKey = `${pendingMessageJob.conversation_id}:${pendingMessageJob.job_id}`;
    if (activeMessageJobPollsRef.current.has(pollKey)) return;
    activeMessageJobPollsRef.current.add(pollKey);
    const shouldContinuePolling = () => isVisibleConversation(pendingMessageJob.conversation_id);
    const stopIfHidden = () => !shouldContinuePolling();
    const planContinuation =
      pendingMessageJob.continue_after_save?.type === "plan_save"
        ? pendingMessageJob.continue_after_save
        : null;
    setBusyForConversation(pendingMessageJob.conversation_id, true);
    if (planContinuation) {
      const failPendingPlanMessage = async (error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        pendingPlanMessagePersistenceIdsRef.current.delete(pendingMessageJob.source_message_id);
        removeOptimisticMessage(pendingMessageJob.source_message_id, pendingMessageJob.conversation_id);
        if (planContinuation.processed_key) releaseArtifactAction(planContinuation.processed_key);
        await clearPendingMessageJob(pendingMessageJob.conversation_id, "plan_message_save_failed", {
          message_job_resume_error: message,
        }).catch(() => {});
        pushAssistant(`plan.md 消息保存失败:${message}`, pendingMessageJob.conversation_id);
      };
      try {
        await persistPendingMessageJob(
          pendingMessageJob,
          pendingMessageJob.conversation_id,
          "plan_message_save_running",
        ).catch(() => {});
        const step = await resumePlanMessageJobStep(pendingMessageJob, {
          shouldContinue: shouldContinuePolling,
          getStatus: () =>
            api.getConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id),
          pollStatus: async (onStatus) => {
            let latestStatus: ConversationMessageJobStatusResponse | null = null;
            const saved = await api.pollConversationMessageJob(
              pendingMessageJob.conversation_id,
              pendingMessageJob.job_id,
              shouldContinuePolling,
              (status) => {
                latestStatus = status;
                onStatus(status);
              },
            );
            if (saved) {
              return {
                ok: true,
                job_id: pendingMessageJob.job_id,
                status: "completed",
                result: saved,
                error: null,
                message: "Plan 消息已保存",
              };
            }
            return latestStatus;
          },
          restart: (request: ConversationMessageJobRequest) =>
            api.startConversationMessageJob(pendingMessageJob.conversation_id, request),
        });
        if (step.kind === "pending") {
          if (step.pending.job_id !== pendingMessageJob.job_id) {
            const restartedPendingMessageJob: PendingMessageJob = {
              ...step.pending,
              restart_count: (pendingMessageJob.restart_count || 0) + 1,
            };
            try {
              await persistPendingMessageJob(
                restartedPendingMessageJob,
                restartedPendingMessageJob.conversation_id,
                "plan_message_save_running",
              );
            } catch {
              pendingMessageJobRef.current = restartedPendingMessageJob;
            }
            if (shouldContinuePolling()) schedulePendingMessageJobResume(restartedPendingMessageJob);
            return;
          }
          if (!shouldContinuePolling()) return;
          const retryPendingMessageJob: PendingMessageJob = {
            ...step.pending,
            restart_count: (pendingMessageJob.restart_count || 0) + 1,
          };
          try {
            await persistPendingMessageJob(
              retryPendingMessageJob,
              retryPendingMessageJob.conversation_id,
              "plan_message_save_running",
            );
          } catch {
            pendingMessageJobRef.current = retryPendingMessageJob;
          }
          schedulePendingMessageJobResume(retryPendingMessageJob);
          return;
        }
        if (step.kind === "failed") {
          await failPendingPlanMessage(step.error);
          return;
        }

        const savedMessage = messageFromResponse(step.result, pendingMessageJob.conversation_id);
        if (!savedMessage) {
          await failPendingPlanMessage(new Error("服务端 Plan 消息无法恢复"));
          return;
        }
        let authoritativeContext: Record<string, unknown>;
        try {
          authoritativeContext = planContextFromSavedMessage(savedMessage, planContinuation.context);
        } catch (protocolError) {
          await failPendingPlanMessage(protocolError);
          return;
        }
        pendingPlanMessagePersistenceIdsRef.current.delete(pendingMessageJob.source_message_id);
        replaceOptimisticMessage(
          pendingMessageJob.source_message_id,
          savedMessage,
          pendingMessageJob.conversation_id,
          true,
        );
        try {
          await updateConversationWithProgress(pendingMessageJob.conversation_id, {
            last_phase: planContinuation.last_phase,
            context: {
              ...makeSnapshot(pendingMessageJob.conversation_id),
              ...authoritativeContext,
              pendingMessageJob: null,
              pending_message_job: null,
            } as unknown as Record<string, unknown>,
          });
        } catch (contextError) {
          const contextSyncPendingMessageJob: PendingMessageJob = {
            ...pendingMessageJob,
            restart_count: (pendingMessageJob.restart_count || 0) + 1,
          };
          try {
            await persistPendingMessageJob(
              contextSyncPendingMessageJob,
              contextSyncPendingMessageJob.conversation_id,
              "plan_context_sync_pending",
              {
                message_job_resume_error:
                  contextError instanceof Error ? contextError.message : String(contextError),
              },
            );
          } catch {
            pendingMessageJobRef.current = contextSyncPendingMessageJob;
          }
          if (shouldContinuePolling()) schedulePendingMessageJobResume(contextSyncPendingMessageJob);
          return;
        }
        pendingMessageJobRef.current = null;
        if (authoritativeContext.flowDraft === null) flowDraftRef.current = null;
        if (authoritativeContext.pendingDirectionJob === null) pendingDirectionJobRef.current = null;
        if (authoritativeContext.pendingPlanJob === null) pendingPlanJobRef.current = null;
        if (authoritativeContext.pendingPlanRevisionChoice === null) setPendingPlanRevisionChoice(null);
        if (planContinuation.success_message) {
          pushAssistant(planContinuation.success_message, pendingMessageJob.conversation_id);
        }
      } finally {
        activeMessageJobPollsRef.current.delete(pollKey);
        setBusyForConversation(pendingMessageJob.conversation_id, false);
      }
      return;
    }

    try {
      if (stopIfHidden()) return;
      const status = await api.getConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id);
      if (stopIfHidden()) return;
      if (status.status === "failed") {
        throw new Error(status.error || status.message || "对话消息保存失败");
      }
      const saved =
        status.status === "completed" && status.result
          ? status.result
          : await api.pollConversationMessageJob(pendingMessageJob.conversation_id, pendingMessageJob.job_id, shouldContinuePolling);
      if (!saved || stopIfHidden()) return;
      const savedMessage = messageFromResponse(saved, pendingMessageJob.conversation_id);
      if (savedMessage) replaceOptimisticMessage(pendingMessageJob.source_message_id, savedMessage, pendingMessageJob.conversation_id);
      let nextIntakeJob =
        pendingIntakeJobRef.current?.conversation_id === pendingMessageJob.conversation_id &&
        pendingIntakeJobRef.current.source_message_id === pendingMessageJob.source_message_id
          ? pendingIntakeJobRef.current
          : null;
      if (pendingMessageJob.continue_after_save?.type === "handle_send" && !nextIntakeJob) {
        pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", pendingMessageJob.conversation_id);
        nextIntakeJob = await startIntakeAnalyzeJob(
          pendingMessageJob.conversation_id,
          {
            prompt: pendingMessageJob.continue_after_save.content,
            materials: pendingMessageJob.continue_after_save.materials,
          },
          pendingMessageJob.source_message_id,
          false,
        );
      }
      await clearPendingMessageJob(pendingMessageJob.conversation_id, nextIntakeJob ? "intake_analyze_running" : "message_saved", {
        user_message_saved: true,
      }).catch(() => {});
      if (nextIntakeJob) await resumePendingIntakeJob(nextIntakeJob);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (stopIfHidden()) return;
      if (isTransientFetchAbort(err)) return;
      pushAssistant(
        message.includes("404")
          ? "之前的对话消息保存任务不存在或已过期。请确认历史对话里是否已保存该消息，必要时重新发送。"
          : `继续查询对话消息保存任务失败:${message}`,
        pendingMessageJob.conversation_id,
      );
      await clearPendingMessageJob(pendingMessageJob.conversation_id, "message_job_resume_failed", {
        message_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activeMessageJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingMessageJob.conversation_id, false);
    }
  };

  const resumePendingPlanJob = async (pendingPlanJob: PendingPlanJob) => {
    const pollKey = `${pendingPlanJob.conversation_id}:${pendingPlanJob.kind}:${pendingPlanJob.job_id}`;
    if (activePlanJobPollsRef.current.has(pollKey)) return;
    activePlanJobPollsRef.current.add(pollKey);
    const targetConversationId = pendingPlanJob.conversation_id;
    const shouldContinuePolling = () => isVisibleConversation(targetConversationId);
    const stopIfHidden = () => !shouldContinuePolling();
    const clearRecoveryState = () => {
      planJobResumeAttemptsRef.current.delete(pollKey);
      planJobRecoveryNoticesRef.current.delete(pollKey);
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        targetConversationId,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    };
    setBusyForConversation(targetConversationId, true);
    try {
      if (stopIfHidden()) return;
      if (hasMaterializedPlanJob(messagesRef.current, targetConversationId, pendingPlanJob)) {
        clearRecoveryState();
        await clearPendingPlanJob(targetConversationId, "plan_review").catch(() => {});
        if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
        return;
      }
      const status = pendingPlanJob.kind === "plan_manual_edit"
        ? await api.getPlanManualEditJob(pendingPlanJob.job_id)
        : pendingPlanJob.kind === "plan_revision"
          ? await api.getPlanRevisionJob(pendingPlanJob.job_id)
          : await api.getPlanMarkdownJob(pendingPlanJob.job_id);
      if (stopIfHidden()) return;
      const statusAction = classifyPlanJobResume({
        status: status.status,
        hasResult: Boolean(status.result),
      });
      if (statusAction === "clear_failed") {
        throw new ApiError(
          status.status === "failed" ? 409 : 422,
          status.error || status.message || "Plan job failed",
        );
      }
      const plan = statusAction === "complete"
        ? status.result!
        : pendingPlanJob.kind === "plan_manual_edit"
          ? await api.pollPlanManualEditJob(pendingPlanJob.job_id, shouldContinuePolling)
          : pendingPlanJob.kind === "plan_revision"
            ? await api.pollPlanRevisionJob(pendingPlanJob.job_id, shouldContinuePolling)
            : await api.pollPlanMarkdownJob(pendingPlanJob.job_id, shouldContinuePolling);
      if (!plan || stopIfHidden()) return;
      if (plan.error) {
        clearRecoveryState();
        if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
        const isRevision = pendingPlanJob.kind === "plan_revision";
        const isManualEdit = pendingPlanJob.kind === "plan_manual_edit";
        await clearPendingPlanJob(
          targetConversationId,
          isManualEdit ? "plan_manual_edit_failed" : isRevision ? "plan_revision_failed" : "plan_generation_failed",
          {
            plan_job_resume_error: plan.error,
          },
        ).catch(() => {});
        pushAssistant(
          isManualEdit
            ? `plan.md 编辑发布失败，已保留当前版本：${plan.error}`
            : isRevision
            ? `plan.md 修改失败，已保留当前版本：${plan.error}`
            : `plan.md 生成失败，未展示不符合创作合同的候选方案：${plan.error}`,
          targetConversationId,
        );
        return;
      }
      await persistPlanArtifactForConversation(
        createPlanArtifactMessage(
          plan,
          pendingPlanJob.context.selectedDirection,
          {
            intent: pendingPlanJob.context.intent,
            formValues: pendingPlanJob.context.formValues,
            materials: pendingPlanJob.context.materials,
            coreMessage: pendingPlanJob.context.coreMessage,
            intakeContext: pendingPlanJob.context.intakeContext,
          },
          targetConversationId,
          pendingPlanJob.job_id,
        ),
        targetConversationId,
        {
          type: "plan_save",
          last_phase: "plan_review",
          processed_key: pendingPlanJob.context.processedKey,
          success_message: pendingPlanJob.kind === "plan_manual_edit"
            ? `用户编辑内容已发布为 plan.md v${plan.plan_version}，请确认后继续。`
            : undefined,
          context: {
            flowDraft: null,
            pendingDirectionJob: null,
            pending_direction_job: null,
            pendingPlanJob: null,
            pending_plan_job: null,
            pendingPlanRevisionChoice: null,
            pending_plan_revision_choice: null,
            intent: pendingPlanJob.context.intent,
            form_values: pendingPlanJob.context.formValues,
            intake_context: pendingPlanJob.context.intakeContext,
            materials: pendingPlanJob.context.materials,
            ...(pendingPlanJob.kind === "plan_manual_edit" ? { plan_approved: false } : {}),
          },
        },
      );
      if (pendingPlanJob.kind === "plan_manual_edit" && isVisibleConversation(targetConversationId)) {
        setCanvasOpen(false);
        setSelectedPlanEditorMessageId("");
        setSavingPlanEdit(false);
      }
      clearRecoveryState();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const action = classifyPlanJobResume({
        hidden: stopIfHidden(),
        errorStatus: err instanceof ApiError ? err.status : undefined,
      });
      if (action === "retain_pending") {
        if (!stopIfHidden()) {
          const attempt = planJobResumeAttemptsRef.current.get(pollKey) || 0;
          planJobResumeAttemptsRef.current.set(pollKey, attempt + 1);
          if (!planJobRecoveryNoticesRef.current.has(pollKey)) {
            planJobRecoveryNoticesRef.current.add(pollKey);
            pushAssistant("Plan 查询暂时中断，正在使用原任务继续恢复…", targetConversationId);
          }
          window.setTimeout(() => {
            const current = pendingPlanJobRef.current;
            if (
              !document.hidden &&
              current?.job_id === pendingPlanJob.job_id &&
              current.conversation_id === targetConversationId
            ) {
              void resumePendingPlanJob(pendingPlanJob);
            }
          }, planJobResumeDelayMs(attempt));
        }
        return;
      }
      clearRecoveryState();
      if (pendingPlanJob.context.processedKey) releaseArtifactAction(pendingPlanJob.context.processedKey);
      const isManualEdit = pendingPlanJob.kind === "plan_manual_edit";
      pushAssistant(
        action === "clear_not_found"
          ? "之前的 plan.md 任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新创意方向或 Plan 卡片手动重试。"
          : isManualEdit
            ? `plan.md 编辑发布失败，已保留当前版本：${message}`
            : `继续查询 plan.md 任务失败：${message}`,
        targetConversationId,
      );
      await clearPendingPlanJob(targetConversationId, isManualEdit ? "plan_manual_edit_failed" : "plan_job_resume_failed", {
        plan_job_resume_error: message,
      }).catch(() => {});
    } finally {
      activePlanJobPollsRef.current.delete(pollKey);
      if (pendingPlanJob.kind === "plan_manual_edit") setSavingPlanEdit(false);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const scenePackageContext = (
    artifact: ChatArtifact,
    videoScenePackages: PrepareScenePackagesResponse,
    sceneAssetFailures: Array<Record<string, unknown>>,
  ) => ({
    form_values: artifact.formValues,
    intake_context: artifact.intakeContext,
    materials: artifact.materials || [],
    selected_direction: artifact.selectedDirection,
    plan_markdown: artifact.plan?.plan_markdown,
    plan_approved: true,
    global_assets: videoScenePackages.global_assets,
    scene_packages: videoScenePackages.scene_packages,
    scene_asset_failures: sceneAssetFailures,
    creation_contract: videoScenePackages.creation_contract,
  });

  const upsertEarlyScenePackageCard = (
    pendingScenePackageJob: PendingScenePackageJob,
    videoScenePackages: PrepareScenePackagesResponse,
    options: {
      generating: boolean;
      awaitingModel?: boolean;
      sceneAssetFailures?: Array<Record<string, unknown>>;
      tip?: string;
    },
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    const awaitingModel = Boolean(options.awaitingModel);
    const tip = options.tip
      || (options.generating
        ? "场景包结构已就绪，参考图生成中。可先打开卡片查看设定与分镜。"
        : awaitingModel
          ? "场景包结构已就绪。请先选择生图模型，再生成角色/场景/道具参考图。"
          : "视频场景包和参考图已准备好，请确认后生成视频。");
    const message: ChatMessage = {
      id: scenePackageJobMessageId(pendingScenePackageJob),
      conversationId: targetConversationId || undefined,
      role: "assistant",
      content: tip,
      time: "",
      artifact: {
        type: "video_scene_packages",
        title: "视频场景包",
        description: options.generating
          ? `${videoScenePackages.scene_packages.length} 个场景片段，参考图生成中，可先查看结构。`
          : awaitingModel
            ? `${videoScenePackages.scene_packages.length} 个场景片段，结构已就绪，请选择生图模型后生成参考图。`
            : `${videoScenePackages.scene_packages.length} 个场景片段，生成视频前必须确认。`,
        actionLabel: options.generating || awaitingModel ? "查看" : "确认",
        videoScenePackages,
        originalVideoScenePackages: videoScenePackages,
        sceneAssetFailures: options.sceneAssetFailures || [],
        sceneAssetsGenerating: options.generating,
        sceneAssetsAwaitingModel: awaitingModel,
        intent: "video",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
      },
    };
    void upsertPersistedChatMessage(message, targetConversationId);
  };

  // V2 prepare 成功后：把 workspace 里的完整资产包投影成旧工作流同款卡片（角色/场景/道具/提示词）。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2" || !currentConversationId) return;
    const workspace = videoAgentView.workspace;
    if (!Array.isArray(workspace?.scenePackages) || workspace.scenePackages.length === 0) {
      return;
    }
    const packages = workspace.scenePackages as PrepareScenePackagesResponse["scene_packages"];
    const globalAssets = (workspace.globalAssets || {
      characters: [],
      scenes: [],
      props: [],
      visual_style: {},
    }) as PrepareScenePackagesResponse["global_assets"];
    const stableMessageId = `video-agent-workspace-scene-packages:${workspace.workspaceId}`;
    const conversationMessages = messagesRef.current.filter((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
    ));
    // 优先更新时间线上最早的场景包卡，避免重投跑到对话末尾或另开一张。
    const existingAny = conversationMessages.find((message) => message.artifact?.type === "video_scene_packages");
    const existingStable = conversationMessages.find((message) => message.id === stableMessageId);
    const targetMessageId = existingAny?.id || existingStable?.id || stableMessageId;
    const existing = conversationMessages.find((message) => message.id === targetMessageId);
    const existingPackages = existing?.artifact?.type === "video_scene_packages"
      ? existing.artifact.videoScenePackages
      : null;
    const nextImageCount = countGlobalAssetImageUrls(globalAssets);
    const prevImageCount = countGlobalAssetImageUrls(existingPackages?.global_assets);
    const hasImages = nextImageCount > 0;
    // 仅「选择生图模型」卡已确认才算选模完成；合同里预填的 image_model 不能当成已确认，
    // 否则会假显示「参考图生成中」并跳过选模卡。
    const modelConfirmed = conversationMessages.some((message) => (
      message.artifact?.type === "scene_asset_model_options"
      && Boolean(message.artifact.sceneAssetModelConfirmed)
    ));
    const assetsStepRunning = assetPackageProgressStepsRef.current.some(
      (step) => step.id === "assets" && step.status === "running",
    );
    // 生图中：真实进度 running，或用户已确认模型且卡上仍标 generating（工具启动瞬间）。
    const sceneAssetsGenerating = Boolean(
      !hasImages && (assetsStepRunning || (modelConfirmed && Boolean(existing?.artifact?.sceneAssetsGenerating))),
    );
    const sceneAssetsAwaitingModel = Boolean(
      !hasImages && !sceneAssetsGenerating && !modelConfirmed,
    );
    // 卡已在时间线且结构/图量/状态未变则跳过重投；但进度若仍卡在 packages=running 必须解卡
    //（二次确认 / 复用旧包时 upsert 无变更，命令路径又不发 native prepare 完成事件）。
    const packagesStepStuckRunning = assetPackageProgressStepsRef.current.some(
      (step) => step.id === "packages" && step.status === "running",
    );
    // 合并成片 URL：优先用 Workspace 权威投影，便于资产包底部「查看合并后的视频」。
    const nextMergedVideo = workspace.mergedVideoUrl
      ? ({
          ok: true,
          endpoint: existing?.artifact?.mergedVideo?.endpoint || "compose_or_export_video",
          merged_video_url: workspace.mergedVideoUrl,
          task_id: existing?.artifact?.mergedVideo?.task_id || null,
          scene_videos: existing?.artifact?.mergedVideo?.scene_videos || [],
          error: null,
          message: existing?.artifact?.mergedVideo?.message || "MP4成片已生成",
          raw: existing?.artifact?.mergedVideo?.raw || {},
        } satisfies MergeSceneVideosResponse)
      : existing?.artifact?.mergedVideo;
    const existingMergedUrl = String(existing?.artifact?.mergedVideo?.merged_video_url || "").trim();
    const nextMergedUrl = String(nextMergedVideo?.merged_video_url || "").trim();
    if (
      existing
      && existingPackages
      && existingPackages.scene_packages.length === packages.length
      && existingPackages.target_duration_ms === (workspace.targetDurationMs || existingPackages.target_duration_ms)
      && Boolean(existingPackages.global_assets)
      && nextImageCount === prevImageCount
      && Boolean(existing.artifact?.sceneAssetsGenerating) === sceneAssetsGenerating
      && Boolean(existing.artifact?.sceneAssetsAwaitingModel) === sceneAssetsAwaitingModel
      && existingMergedUrl === nextMergedUrl
    ) {
      if (packagesStepStuckRunning && !assetsStepRunning) {
        setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
          current.length > 0 ? current : createAssetPackageProgressSteps(),
          hasImages ? "completed" : "awaiting_image_model",
        ));
      }
      return;
    }
    const videoScenePackages: PrepareScenePackagesResponse = {
      ok: true,
      message: hasImages
        ? `已生成 ${packages.length} 个分镜资产包，参考图已写入`
        : `已生成 ${packages.length} 个分镜资产包`,
      requires_confirmation: true,
      review_timeout_sec: null,
      target_duration_ms: workspace.targetDurationMs || DEFAULT_TARGET_DURATION_MS,
      global_assets: globalAssets,
      scene_packages: packages,
      creation_contract: (workspace.creationContract || existingPackages?.creation_contract || null) as VideoCreationContract | null,
    };
    const modelOptionsId = conversationMessages.find((message) => (
      message.artifact?.type === "scene_asset_model_options"
    ))?.id;
    void upsertPersistedChatMessage(
      {
        id: targetMessageId,
        conversationId: currentConversationId,
        role: "assistant",
        content: hasImages
          ? "角色、场景与道具参考图已更新到视频场景包，请打开卡片查看。"
          : "已根据脚本预览中的角色、场景、道具与分镜生成视频场景包。请打开卡片查看详情。",
        time: existing?.time || "",
        artifact: {
          type: "video_scene_packages",
          title: "视频场景包",
          description: hasImages
            ? `${packages.length} 个场景片段，参考图已生成。`
            : sceneAssetsGenerating
              ? `${packages.length} 个场景片段，参考图生成中，可先查看分镜。`
              : `${packages.length} 个场景片段，结构已就绪。请选择生图模型后生成参考图。`,
          actionLabel: hasImages ? "确认" : "查看",
          videoScenePackages,
          originalVideoScenePackages: existing?.artifact?.originalVideoScenePackages || videoScenePackages,
          sceneAssetFailures: existing?.artifact?.sceneAssetFailures || [],
          sceneAssetsGenerating,
          sceneAssetsAwaitingModel,
          intent: "video",
          generatedSceneVideos: existing?.artifact?.generatedSceneVideos,
          mergedVideo: nextMergedVideo,
          videoScenePackageEditedSceneIds: existing?.artifact?.videoScenePackageEditedSceneIds || [],
        },
      },
      currentConversationId,
      { insertBeforeId: existing ? null : (modelOptionsId || null) },
    );
    // 同会话只保留一张场景包卡，去掉重投产生的重复副本。
    setMessages((items) => {
      const nextItems = items.filter((message) => {
        if (messageConversationId(message, currentConversationId) !== currentConversationId) return true;
        if (message.artifact?.type !== "video_scene_packages") return true;
        return message.id === targetMessageId;
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    // 结构就绪：进度停在选模型；只有 assets 步骤真正 running 才切到生图中，避免假「参考图生成中」。
    setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
      current.length > 0 ? current : createAssetPackageProgressSteps(),
      hasImages ? "completed" : (assetsStepRunning ? "generate_scene_assets" : "awaiting_image_model"),
    ));
    if (!assetPackageAnchorMessageIdRef.current) {
      assetPackageAnchorMessageIdRef.current = targetMessageId;
      setAssetPackageAnchorMessageId(targetMessageId);
    }
  }, [
    currentConversationId,
    orchestrationMode,
    videoAgentView.workspace?.creationContract,
    videoAgentView.workspace?.globalAssets,
    videoAgentView.workspace?.mergedVideoUrl,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.scenePackages,
    videoAgentView.workspace?.targetDurationMs,
    videoAgentView.workspace?.workspaceId,
    workspaceScenePackageReprojectEpoch,
  ]);

  const sceneAssetModelOptionsMessageId = (jobId: string) => `scene-asset-model-options:${jobId}`;

  const pushSceneAssetModelOptionsCard = async (
    pendingScenePackageJob: Pick<PendingScenePackageJob, "conversation_id" | "job_id" | "artifact">,
    videoScenePackages: PrepareScenePackagesResponse,
    options?: { messageId?: string },
  ) => {
    const targetConversationId = pendingScenePackageJob.conversation_id;
    const artifact = pendingScenePackageJob.artifact;
    let configs: ImageModelParamConfig[] = [];
    try {
      const listed = await api.listImageGenerateModelConfigs();
      configs = Array.isArray(listed) ? listed : [];
    } catch {
      configs = [];
    }
    const preferredModels = [...SCENE_ASSET_PREFERRED_MODELS];
    const filtered = preferredModels
      .map((model) => configs.find((config) => {
        const record = config as unknown as Record<string, unknown>;
        const modelType = String(record.modelType || record.model_type || record.model || "");
        return modelType === model && record.isEnabled !== false;
      }))
      .filter((config): config is ImageModelParamConfig => Boolean(config));
    const modelConfigs = filtered.length > 0
      ? filtered
      : preferredModels.map((modelType) => ({
          modelType,
          modelCategoryType: "image_generate",
          paramConfig: {
            sizeList: modelType === "gpt-image-2" ? ["4K", "2K", "1080p"] : ["2K", "4K", "1080p"],
            aspectRatioList: ["1:1", "9:16", "16:9"],
          },
          isEnabled: true,
        } as ImageModelParamConfig));
    const messageId = options?.messageId || sceneAssetModelOptionsMessageId(pendingScenePackageJob.job_id);
    pushArtifact("场景包结构已就绪，请选择生图模型后再生成参考图。", {
      type: "scene_asset_model_options",
      title: "选择生图模型",
      description: "推荐 image-2 或 Seedream 5.0。确认后开始生成角色/场景/道具参考图。",
      actionLabel: "确认",
      intent: "video",
      formValues: artifact.formValues,
      intakeContext: artifact.intakeContext,
      materials: artifact.materials || [],
      selectedDirection: artifact.selectedDirection,
      plan: artifact.plan,
      videoScenePackages,
      originalVideoScenePackages: videoScenePackages,
      sceneAssetModelConfigs: modelConfigs,
      sceneAssetModelConfirmed: false,
      creationContract: videoScenePackages.creation_contract || artifact.plan?.creation_contract,
    }, targetConversationId, messageId);
  };

  // 选模型卡：结构就绪后自动弹出；「没有参考图」仅作补弹兜底。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2" || !currentConversationId) return;
    const workspace = videoAgentView.workspace;
    if (!workspace || !Array.isArray(workspace.scenePackages) || workspace.scenePackages.length === 0) {
      return;
    }
    if (countGlobalAssetImageUrls(workspace.globalAssets) > 0) return;
    const conversationMessages = messagesRef.current.filter((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
    ));
    const modelConfirmed = conversationMessages.some((message) => (
      message.artifact?.type === "scene_asset_model_options"
      && Boolean(message.artifact.sceneAssetModelConfirmed)
    ));
    const hasModelOptionsCard = conversationMessages.some((message) => (
      message.artifact?.type === "scene_asset_model_options"
    ));
    if (modelConfirmed || hasModelOptionsCard) return;
    const packages = workspace.scenePackages as PrepareScenePackagesResponse["scene_packages"];
    const globalAssets = (workspace.globalAssets || {
      characters: [],
      scenes: [],
      props: [],
      visual_style: {},
    }) as PrepareScenePackagesResponse["global_assets"];
    const videoScenePackages: PrepareScenePackagesResponse = {
      ok: true,
      message: `已生成 ${packages.length} 个分镜资产包`,
      requires_confirmation: true,
      review_timeout_sec: null,
      target_duration_ms: workspace.targetDurationMs || DEFAULT_TARGET_DURATION_MS,
      global_assets: globalAssets,
      scene_packages: packages,
      creation_contract: (workspace.creationContract || null) as VideoCreationContract | null,
    };
    // 资产包结构就绪后立刻弹选模卡；用户确认后才启动 generate_scene_assets。
    void pushSceneAssetModelOptionsCard(
      {
        conversation_id: currentConversationId,
        job_id: String(workspace.workspaceId || "v2-scene-assets"),
        artifact: {
          type: "video_scene_packages",
          title: "视频场景包",
          formValues: {},
          materials: [],
          videoScenePackages,
        } as ChatArtifact,
      },
      videoScenePackages,
      { messageId: `scene-asset-model-options:${workspace.workspaceId}` },
    );
  }, [
    currentConversationId,
    orchestrationMode,
    videoAgentView.workspace?.creationContract,
    videoAgentView.workspace?.globalAssets,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.scenePackages,
    videoAgentView.workspace?.targetDurationMs,
    videoAgentView.workspace?.workspaceId,
    workspaceScenePackageReprojectEpoch,
  ]);

  const handleConfirmSceneAssetModel = async (
    msg: ChatMessage,
    selection: ImageEditModelSelection,
  ) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "scene_asset_model_options") return;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages?.ok) {
      pushAssistant("缺少可用的场景包结构，无法开始生图。", messageConversationId(msg, conversationIdRef.current));
      return;
    }
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    // 已确认且已有参考图：忽略重复点击；无图时允许再确认（网关热重载/僵尸 job 后常见）。
    if (artifact.sceneAssetModelConfirmed) {
      const hasImages = (
        countGlobalAssetImageUrls(videoAgentView.workspace?.globalAssets) > 0
        || scenePackageHasGeneratedImages(videoScenePackages)
      );
      if (hasImages) return;
    }
    // V2 走 Turn + generate_scene_assets，不再依赖旧 pendingScenePackageJob HTTP 轮询。
    // 残留 pending 会误拦「再选模型」，导致永远不发 Turn、分镜一直无图。
    if (orchestrationModeRef.current !== "video_agent_v2") {
      const existing = pendingScenePackageJobRef.current;
      if (existing?.conversation_id === targetConversationId) {
        pushAssistant("参考图仍在生成中，请稍候…", targetConversationId);
        return;
      }
    }
    const model = String(selection.model || "").trim() || "gpt-image-2";
    const selectedConfig = (artifact.sceneAssetModelConfigs || []).find((config) => {
      const record = config as unknown as Record<string, unknown>;
      return String(record.modelType || record.model_type || record.model || "") === model;
    });
    const sizeOptions = (() => {
      const record = (selectedConfig || {}) as unknown as Record<string, unknown>;
      const params = (record.paramConfig || record.param_config || {}) as Record<string, unknown>;
      const list = params.sizeList || params.size_list;
      return Array.isArray(list) ? list.map((item) => String(item || "")).filter(Boolean) : [];
    })();
    const imageSize = preferredSceneAssetImageSize(model, sizeOptions);
    const imageRatio = String(
      (videoScenePackages.creation_contract as Record<string, unknown> | undefined)?.scene_image_ratio
      || artifact.formValues?.scene_image_ratio
      || "9:16",
    );
    const previousContract = (videoScenePackages.creation_contract || artifact.creationContract || {}) as Record<string, unknown>;
    const creationContract = {
      ...previousContract,
      image_model: model,
      scene_image_size: imageSize,
      scene_image_ratio: imageRatio,
    } as unknown as VideoCreationContract;
    const nextPackages: PrepareScenePackagesResponse = {
      ...videoScenePackages,
      creation_contract: creationContract as unknown as PrepareScenePackagesResponse["creation_contract"],
    };
    setMessages((items) => {
      const nextItems = items.map((item) => {
        if (item.id !== msg.id || messageConversationId(item, targetConversationId) !== targetConversationId) return item;
        return {
          ...item,
          artifact: {
            ...item.artifact!,
            sceneAssetModelConfirmed: true,
            videoScenePackages: nextPackages,
            creationContract: creationContract as unknown as Record<string, unknown>,
            sceneAssetReferenceMaterials: selection.referenceMaterials || [],
            sceneAssetReferenceBrief: selection.referenceBrief || "",
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    const confirmedMessage = messagesRef.current.find(
      (item) => item.id === msg.id && messageConversationId(item, targetConversationId) === targetConversationId,
    );
    if (confirmedMessage?.artifact) {
      void api.updateConversationMessage(targetConversationId, msg.id, {
        content: confirmedMessage.content,
        payload: {
          artifact: confirmedMessage.artifact,
          materials: confirmedMessage.materials || confirmedMessage.artifact.materials || [],
          client_message_id: msg.id,
        } as unknown as Record<string, unknown>,
      }).catch(() => {});
    }
    pushAssistant(`已选择 ${sceneAssetModelLabel(model)}，开始生成场景参考图…`, targetConversationId);
    // 同步场景包卡：离开 awaitingModel，进入 generating，并露出后续「确认并生成视频」路径所需状态位。
    setMessages((items) => {
      const nextItems = items.map((item) => {
        if (
          messageConversationId(item, targetConversationId) !== targetConversationId
          || item.artifact?.type !== "video_scene_packages"
          || !item.artifact.videoScenePackages
        ) {
          return item;
        }
        return {
          ...item,
          artifact: {
            ...item.artifact,
            sceneAssetsAwaitingModel: false,
            sceneAssetsGenerating: true,
            actionLabel: "查看",
            videoScenePackages: {
              ...item.artifact.videoScenePackages,
              creation_contract: creationContract as unknown as PrepareScenePackagesResponse["creation_contract"],
            },
          },
        };
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
    setWorkspaceScenePackageReprojectEpoch((value) => value + 1);
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED && orchestrationModeRef.current !== "video_agent_v2") {
      notifyLegacyVideoJobBlocked(targetConversationId);
      return;
    }
    // V2.1 批次 B：模型确认后提交 Turn，由 generate_scene_assets Tool 执行。
    // 进度卡改由 native tool 信号驱动；禁止提前乐观切到「正在生图」，
    // 避免 Turn 模型 500 / 热重载失败后进度永久假转。
    if (orchestrationModeRef.current === "video_agent_v2") {
      try {
        sessionStorage.setItem(
          `pixelflow:last-scene-asset-model:${targetConversationId}`,
          JSON.stringify({
            model,
            imageSize,
            imageRatio,
            referenceBrief: selection.referenceBrief || "",
            materials: selection.referenceMaterials || [],
          }),
        );
      } catch {
        // ignore
      }
      const brief = String(selection.referenceBrief || "").trim();
      try {
        await handleSupervisorTurn(
          {
            conversationId: targetConversationId,
            clientInputId: uid(),
            content: (
              `确认生图模型 ${model}，比例 ${imageRatio}，清晰度 ${imageSize}，开始生成参考图`
              + (brief ? `。用途：${brief}` : "")
            ),
            materials: [
              ...(Array.isArray(artifact.materials) ? artifact.materials : []),
              ...(selection.referenceMaterials || []),
            ],
            replyToMessageId: null,
            artifactRefs: [],
            interruptId: null,
            explicitAction: null,
            continueLegacy: false,
            registrationStatus: "pending",
          },
          supervisorRuntime.getContextVersion() ?? 0,
        );
      } catch (error) {
        setAssetPackageProgressSteps((current) => failAssetPackageProgressSteps(
          current.length > 0 ? current : createAssetPackageProgressSteps(),
          error instanceof Error
            ? `启动参考图失败：${error.message}`
            : "启动参考图失败，请重新选择生图模型",
        ));
        pushAssistant(
          error instanceof Error
            ? `启动参考图失败：${error.message}`
            : "启动参考图失败，请重新选择生图模型",
          targetConversationId,
        );
      }
      return;
    }
  };

  const pushSceneAssetProgressTip = (
    pendingScenePackageJob: PendingScenePackageJob,
    progress: NonNullable<PrepareScenePackagesJobStatusResponse["asset_progress"]>,
  ) => {
    const typeLabel = progress.asset_type === "character"
      ? "角色"
      : progress.asset_type === "scene_image"
        ? "场景"
        : progress.asset_type === "prop_image"
          ? "道具"
          : "素材";
    const assetName = (progress.asset_name || "参考图").trim() || "参考图";
    const statusText = progress.ok === false ? "失败" : "已完成";
    const content = `参考图 ${progress.completed}/${progress.total}：${typeLabel}「${assetName}」${statusText}`;
    void upsertPersistedChatMessage(
      {
        id: `scene-package-asset-tip:${pendingScenePackageJob.job_id}`,
        conversationId: pendingScenePackageJob.conversation_id,
        role: "assistant",
        content,
        time: "",
      },
      pendingScenePackageJob.conversation_id,
    );
  };

  const sceneVideoProgressMessageId = (jobId: string) => mediaResultClientMessageId("scene_videos", jobId);

  const pushSceneVideoProgressTip = (
    pendingVideoJob: PendingVideoJob,
    progress: NonNullable<GenerateSceneVideosJobStatusResponse["video_progress"]>,
  ) => {
    if (!progress.total || progress.completed <= 0) return;
    const sceneLabel = progress.scene_index
      ? `第 ${progress.scene_index} 镜`
      : (progress.scene_id || "分镜");
    const statusText = progress.ok === false ? "失败" : "已完成，可预览";
    const content = `分镜视频 ${progress.completed}/${progress.total}：${sceneLabel}${statusText}`;
    void appendMessageForConversation(
      {
        id: `scene-video-progress-tip:${pendingVideoJob.job_id}`,
        conversationId: pendingVideoJob.conversation_id,
        role: "assistant",
        content,
        time: "",
      },
      pendingVideoJob.conversation_id,
    );
  };

  const upsertEarlySceneVideoCard = (
    pendingVideoJob: PendingVideoJob,
    partial: GenerateSceneVideosResponse,
    options?: { tip?: string },
  ) => {
    const targetConversationId = pendingVideoJob.conversation_id;
    const artifact = pendingVideoJob.artifact;
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages) return;
    const merged = mergePartialGeneratedSceneVideos(
      artifact.generatedSceneVideos,
      partial,
      pendingVideoJob.affected_scene_ids,
    );
    const requestScenes = "scenes" in pendingVideoJob.request ? pendingVideoJob.request.scenes : [];
    const total = requestScenes.length || videoScenePackages.scene_packages.length;
    const completed = merged.scene_videos.length;
    const tip = options?.tip
      || (completed > 0
        ? `分镜视频生成中（已完成 ${completed}/${total}），可先预览已生成片段。`
        : "场景包已确认，正在生成场景视频…完成后可在卡片中预览每段。");
    void upsertPersistedChatMessage(
      {
        id: sceneVideoProgressMessageId(pendingVideoJob.job_id),
        conversationId: targetConversationId || undefined,
        role: "assistant",
        content: tip,
        time: "",
        artifact: {
          type: "video_result",
          title: "分镜视频",
          description: completed > 0
            ? `已生成 ${completed} 段分镜视频，其余仍在生成中。`
            : `${videoScenePackages.scene_packages.length} 个分镜，视频生成中。`,
          actionLabel: "查看",
          videoScenePackages,
          originalVideoScenePackages: artifact.originalVideoScenePackages || videoScenePackages,
          generatedSceneVideos: merged,
          sceneVideosGenerating: true,
          intent: "video",
          formValues: artifact.formValues,
          intakeContext: artifact.intakeContext,
          materials: artifact.materials || [],
          selectedDirection: artifact.selectedDirection,
          plan: artifact.plan,
        },
      },
      targetConversationId,
    );
  };

  /** V2：从 Workspace 已回填的 variants 投影 early 分镜视频预览卡（不依赖旧 Job HTTP）。 */
  const upsertNativeSceneVideoPreviewFromWorkspace = (
    targetConversationId: string,
  ) => {
    const workspace = videoAgentView.workspace;
    if (!workspace || workspace.conversationId !== targetConversationId) return;
    const progress = workspace.sceneVideoProgress;
    const videosRunning = sceneVideoProgressSteps.some(
      (step) => step.id === "videos" && step.status === "running",
    );
    const conversationMessages = messagesRef.current.filter((message) => (
      messageConversationId(message, targetConversationId) === targetConversationId
    ));
    const preferredPackagesMessage = [...conversationMessages].reverse().find((message) => (
      message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages
    )) || [...conversationMessages].reverse().find((message) => (
      message.artifact?.type === "video_result" && message.artifact.videoScenePackages
    ));
    const videoScenePackages = preferredPackagesMessage?.artifact?.videoScenePackages
      || (
        Array.isArray(workspace.scenePackages) && workspace.scenePackages.length > 0
          ? {
              ok: true,
              global_assets: (workspace.globalAssets || {}) as PrepareScenePackagesResponse["global_assets"],
              scene_packages: workspace.scenePackages as PrepareScenePackagesResponse["scene_packages"],
              creation_contract: (workspace.creationContract || undefined) as PrepareScenePackagesResponse["creation_contract"],
              message: "视频场景包",
              target_duration_ms: workspace.targetDurationMs || undefined,
            }
          : null
      );
    if (!videoScenePackages) return;

    const sceneVideos = workspace.scenes
      .map((scene) => {
        const videoUrl = scene.mediaUrl
          || scene.variants.find((variant) => variant.videoUrl)?.videoUrl
          || null;
        if (!videoUrl) return null;
        return {
          scene_id: scene.sceneId,
          scene_index: scene.sceneIndex,
          duration_ms: 5_000,
          video_url: videoUrl,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null)
      .sort((left, right) => left.scene_index - right.scene_index);

    const failedScenes = workspace.scenes
      .flatMap((scene) => {
        const packageScene = videoScenePackages.scene_packages.find(
          (item) => item.scene_id === scene.sceneId,
        );
        const sceneMeta = {
          title: String(packageScene?.title || scene.title || "").trim() || null,
          storyline: String(packageScene?.storyline || "").trim() || null,
        };
        if (scene.generationFailures.length > 0) {
          return scene.generationFailures.map((failure) => enrichFailedSceneForDisplay({
            scene_id: scene.sceneId,
            scene_index: scene.sceneIndex,
            status: failure.status,
            error: failure.error,
            reason_code: failure.reasonCode || failure.status,
            job_id: failure.jobId,
            attempts: 1,
          }, sceneMeta));
        }
        // 无 URL 且 job 仍标失败：按失败展示，避免「少了几镜却不说原因」。
        const hasVideo = Boolean(
          scene.mediaUrl || scene.variants.some((variant) => Boolean(variant.videoUrl)),
        );
        const looksFailed = scene.editStatus === "重新生成失败"
          || scene.generationJobStatuses.some((status) => (
            ["failed", "timeout", "expired", "error"].includes(status.toLowerCase())
          ));
        if (!hasVideo && looksFailed) {
          return [enrichFailedSceneForDisplay({
            scene_id: scene.sceneId,
            scene_index: scene.sceneIndex,
            status: "failed",
            error: "分镜视频生成失败",
            reason_code: "provider_business_failed",
            attempts: 1,
          }, sceneMeta)];
        }
        return [];
      })
      .sort((left, right) => Number(left.scene_index) - Number(right.scene_index));

    const stillGenerating = Boolean(
      videosRunning
      || nativeGenerateScenesToolSignal.status === "running"
      || (progress && progress.total > 0 && progress.completed < progress.total),
    );
    // 已有 URL 时即使进度元数据缺失也要出卡，否则「视频生成完了却不能预览」。
    if (
      !stillGenerating
      && sceneVideos.length === 0
      && failedScenes.length === 0
      && !(progress && progress.completed > 0)
    ) {
      return;
    }

    const durationBySceneId = new Map(
      videoScenePackages.scene_packages.map((scene) => [
        scene.scene_id,
        Number(scene.duration_ms) || 0,
      ]),
    );
    const withDuration = sceneVideos.map((scene) => ({
      ...scene,
      duration_ms: durationBySceneId.get(scene.scene_id) || scene.duration_ms,
    }));

    // 按 scene_id 合并：Workspace 新 URL 覆盖；未出现在本轮 Workspace 的旧成片保留，
    // 避免单镜重生时短暂缺 URL 把场景包顶栏/面板已有视频清空。
    const mergedBySceneId = new Map<string, (typeof withDuration)[number]>();
    for (const scene of preferredPackagesMessage?.artifact?.generatedSceneVideos?.scene_videos || []) {
      const sceneId = String(scene.scene_id || "").trim();
      const videoUrl = String(scene.video_url || "").trim();
      if (!sceneId || !videoUrl) continue;
      mergedBySceneId.set(sceneId, {
        scene_id: sceneId,
        scene_index: Number(scene.scene_index) || 0,
        duration_ms: Number(scene.duration_ms) || durationBySceneId.get(sceneId) || 5_000,
        video_url: videoUrl,
      });
    }
    for (const scene of withDuration) {
      mergedBySceneId.set(scene.scene_id, scene);
    }
    const mergedSceneVideos = [...mergedBySceneId.values()]
      .sort((left, right) => left.scene_index - right.scene_index);

    // 本批总数：progress / generation_jobs；禁止用场景包全量长度（单镜会误报 14）。
    let jobTotal = 0;
    for (const scene of workspace.scenes) {
      jobTotal += (scene.generationJobStatuses || []).length;
    }
    const finishedCount = mergedSceneVideos.length + failedScenes.length;
    const total = resolveNativeSceneVideoBatchTotal({
      progressTotal: progress?.total,
      jobTotal,
      finishedCount,
      generatingFallback: stillGenerating ? 1 : null,
    });
    const completed = progress?.completed ?? finishedCount;
    const generating = stillGenerating && completed < total;
    const tip = generating
      ? (completed > 0
        ? `分镜视频生成中（已完成 ${completed}/${total}），可先在「视频场景包」预览已生成片段。`
        : `已启动 ${total} 个分镜视频生成，完成后会回填到「视频场景包」。`)
      : (failedScenes.length > 0
        ? `分镜视频完成 ${mergedSceneVideos.length}/${total}，失败 ${failedScenes.length} 个，可查看失败原因后重试。`
        : `分镜视频已完成 ${completed}/${Math.max(total, mergedSceneVideos.length)}，可在「视频场景包」直接预览。`);

    const generatedSceneVideos = {
      ok: !generating && mergedSceneVideos.length > 0 && failedScenes.length === 0,
      endpoint: "generate_scenes",
      scene_videos: mergedSceneVideos,
      failed_scenes: failedScenes,
      message: tip,
    };

    // 不再落独立「分镜视频」预览卡：成片预览回填场景包卡，避免对话区重复大卡。
    if (preferredPackagesMessage?.id && preferredPackagesMessage.artifact?.videoScenePackages) {
      void upsertPersistedChatMessage(
        {
          ...preferredPackagesMessage,
          conversationId: targetConversationId,
          artifact: {
            ...preferredPackagesMessage.artifact,
            generatedSceneVideos,
            sceneVideosGenerating: generating,
          },
        },
        targetConversationId,
      );
    }
  };

  // Workspace 分镜视频事实 → early 预览卡（生成中即可播已完成片段；完成后也强制出卡）。
  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2" || !currentConversationId) return;
    const hasVideos = (videoAgentView.workspace?.scenes || []).some((scene) => (
      Boolean(scene.mediaUrl)
      || scene.variants.some((variant) => Boolean(variant.videoUrl))
    ));
    if (
      !hasVideos
      && !videoAgentView.workspace?.sceneVideoProgress
      && nativeGenerateScenesToolSignal.status !== "running"
      && nativeGenerateScenesToolSignal.status !== "completed"
      && sceneVideoProgressSteps.length === 0
    ) {
      return;
    }
    upsertNativeSceneVideoPreviewFromWorkspace(currentConversationId);
  }, [
    orchestrationMode,
    currentConversationId,
    nativeGenerateScenesToolSignal.status,
    sceneVideoProgressSteps,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.sceneVideoProgress?.completed,
    videoAgentView.workspace?.sceneVideoProgress?.total,
    videoAgentView.workspace?.scenes,
  ]);

  /** V2：compose 成功后把成片投影为对话区 video_result 卡，并同步回填场景包卡。 */
  const upsertNativeMergedVideoResultFromWorkspace = (
    targetConversationId: string,
  ) => {
    const workspace = videoAgentView.workspace;
    const mergedUrl = typeof workspace?.mergedVideoUrl === "string"
      ? workspace.mergedVideoUrl.trim()
      : "";
    if (!workspace || workspace.conversationId !== targetConversationId) return;
    if (!mergedUrl.toLowerCase().startsWith("https://")) return;

    const conversationMessages = messagesRef.current.filter((message) => (
      messageConversationId(message, targetConversationId) === targetConversationId
    ));
    const packagesMessage = [...conversationMessages].reverse().find((message) => (
      message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages
    ));
    const existingResult = [...conversationMessages].reverse().find((message) => (
      message.artifact?.type === "video_result" && message.artifact.mergedVideo?.ok
    ));
    const videoScenePackages = packagesMessage?.artifact?.videoScenePackages
      || existingResult?.artifact?.videoScenePackages
      || null;
    const generatedSceneVideos = packagesMessage?.artifact?.generatedSceneVideos
      || existingResult?.artifact?.generatedSceneVideos
      || {
        ok: true,
        endpoint: "generate_scenes",
        scene_videos: (workspace.scenes || [])
          .map((scene) => {
            const videoUrl = scene.mediaUrl
              || scene.variants.find((variant) => variant.videoUrl)?.videoUrl
              || null;
            if (!videoUrl) return null;
            return {
              scene_id: scene.sceneId,
              scene_index: scene.sceneIndex,
              duration_ms: 5_000,
              video_url: videoUrl,
            };
          })
          .filter((item): item is NonNullable<typeof item> => item !== null),
        failed_scenes: [],
        message: "分镜视频已就绪",
      };
    const mergedVideo: MergeSceneVideosResponse = {
      ok: true,
      endpoint: "compose_or_export_video",
      merged_video_url: mergedUrl,
      task_id: existingResult?.artifact?.mergedVideo?.task_id || `merged:${workspace.workspaceId}`,
      scene_videos: generatedSceneVideos.scene_videos || [],
      error: null,
      message: "MP4成片已生成",
      raw: {},
    };

    if (packagesMessage?.id && packagesMessage.artifact) {
      const existingUrl = String(packagesMessage.artifact.mergedVideo?.merged_video_url || "").trim();
      if (existingUrl !== mergedUrl) {
        void upsertPersistedChatMessage(
          {
            ...packagesMessage,
            conversationId: targetConversationId,
            artifact: {
              ...packagesMessage.artifact,
              mergedVideo,
              generatedSceneVideos,
            },
          },
          targetConversationId,
        );
      }
    }

    const resultMessageId = existingResult?.id
      || `video-agent-merged-result:${workspace.workspaceId}`;
    const existingResultUrl = String(existingResult?.artifact?.mergedVideo?.merged_video_url || "").trim();
    if (existingResult && existingResultUrl === mergedUrl) return;

    void upsertPersistedChatMessage(
      {
        id: resultMessageId,
        conversationId: targetConversationId,
        role: "assistant",
        content: "成片已合并完成，可在下方预览或下载。",
        time: existingResult?.time || "",
        artifact: {
          type: "video_result",
          title: "成品视频",
          description: "分镜已按顺序合并为最终 MP4。",
          actionLabel: "查看",
          intent: "video",
          videoScenePackages: videoScenePackages || undefined,
          originalVideoScenePackages: packagesMessage?.artifact?.originalVideoScenePackages
            || existingResult?.artifact?.originalVideoScenePackages
            || videoScenePackages
            || undefined,
          generatedSceneVideos,
          mergedVideo,
          formValues: packagesMessage?.artifact?.formValues || existingResult?.artifact?.formValues,
          intakeContext: packagesMessage?.artifact?.intakeContext || existingResult?.artifact?.intakeContext,
          materials: packagesMessage?.artifact?.materials || existingResult?.artifact?.materials || [],
          selectedDirection: packagesMessage?.artifact?.selectedDirection
            || existingResult?.artifact?.selectedDirection,
          plan: packagesMessage?.artifact?.plan || existingResult?.artifact?.plan,
        },
      },
      targetConversationId,
    );
  };

  useEffect(() => {
    if (orchestrationMode !== "video_agent_v2" || !currentConversationId) return;
    if (!videoAgentView.workspace?.mergedVideoUrl) return;
    upsertNativeMergedVideoResultFromWorkspace(currentConversationId);
  }, [
    orchestrationMode,
    currentConversationId,
    videoAgentView.workspace?.mergedVideoUrl,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.workspaceId,
  ]);

  const imageResultTitleForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? "图片编辑结果"
      : pendingImageJob.kind === "image_regeneration"
        ? "图片重新生成结果"
        : "图片生成结果";

  const imageResultSuccessContentForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? `图片编辑完成，请确认是否满意。${AUTO_CONFIRM_TIMEOUT_SECONDS} 秒未操作将默认满意并结束流程。`
      : pendingImageJob.kind === "image_regeneration"
        ? "图片已按修改意见重新生成，请查看结果。"
        : "图片生成完成，请查看结果。";

  const imageResultFailureContentForJob = (pendingImageJob: PendingImageJob): string =>
    pendingImageJob.kind === "direct_image_edit"
      ? "图片编辑失败，请查看错误信息。"
      : pendingImageJob.kind === "image_regeneration"
        ? "图片重新生成失败，请查看错误信息。"
        : "图片生成失败，请查看错误信息。";

  const imageResultLastPhaseForJob = (pendingImageJob: PendingImageJob, imageResult: ImageGenerateResponse): string => {
    const quotaPaused = isQuotaInsufficientPayload(imageResult);
    if (pendingImageJob.kind === "direct_image_edit") {
      return imageResult.ok ? "image_edit_done" : quotaPaused ? "image_edit_quota_paused" : "image_edit_failed";
    }
    if (pendingImageJob.kind === "image_regeneration") {
      return imageResult.ok ? "image_regenerated" : quotaPaused ? "image_regeneration_quota_paused" : "image_regeneration_failed";
    }
    return imageResult.ok ? "image_generated" : quotaPaused ? "image_generation_quota_paused" : "image_generation_failed";
  };

  const handleCompletedImageGenerationJob = async (
    pendingImageJob: PendingImageJob,
    imageResult: ImageGenerateResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingImageJob.conversation_id;
    const artifact = pendingImageJob.artifact;
    const imagePrepare = pendingImageJob.imagePrepare || artifact.imagePrepare;
    if (!imagePrepare) {
      releaseArtifactAction(processedKey);
      pushAssistant("图片生成任务完成，但没有找到对应的图片参数，请从最新卡片手动重试。", targetConversationId);
      await clearPendingImageJob(targetConversationId, "image_job_resume_failed", {
        image_job_resume_error: "缺少 imagePrepare",
      }).catch(() => {});
      return;
    }
    const imageQuotaInsufficient = isQuotaInsufficientPayload(imageResult);
    if (!imageResult.ok) releaseArtifactAction(processedKey);
    if (pendingImageJob.kind === "direct_image_edit") {
      pendingImageEditRequestRef.current = null;
    }
    const imageReviewStartedAt = Date.now();
    const imageReviewRequestedAt = new Date(imageReviewStartedAt).toISOString();
    const imageReviewExpiresAt = reviewExpiresAt(imageReviewStartedAt, AUTO_CONFIRM_TIMEOUT_MS);
    const imageResultMessageId = mediaResultClientMessageId(
      pendingImageJob.kind === "direct_image_edit" ? "image_generate" : "image_generate",
      pendingImageJob.job_id,
    );
    let imageResultMessage: ChatMessage | undefined;
    if (hasMediaResultMessage(messagesRef.current, "image_generate", pendingImageJob.job_id)) {
      imageResultMessage = messagesRef.current.find((message) => message.id === imageResultMessageId);
    } else {
      imageResultMessage = pushArtifact(imageResult.ok ? imageResultSuccessContentForJob(pendingImageJob) : imageResultFailureContentForJob(pendingImageJob), {
        type: "image_result",
        title: imageResultTitleForJob(pendingImageJob),
        description: imageQuotaInsufficient
          ? quotaMessage(imageResult.message || (pendingImageJob.kind === "image_regeneration" ? "图片重新生成额度不足。" : "图片生成额度不足。"))
          : imageResultSummary(imageResult),
        actionLabel: "查看",
        imageResult,
        imagePrepare,
        imageEditRequest: artifact.imageEditRequest,
        imageEditConfirmedSelection: artifact.imageEditConfirmedSelection,
        imageRevisionFeedback: pendingImageJob.revision_feedback || artifact.imageRevisionFeedback,
        intent: "image",
        formValues: artifact.formValues,
        intakeContext: artifact.intakeContext,
        materials: artifact.materials || [],
        selectedDirection: artifact.selectedDirection,
        plan: artifact.plan,
        reviewRequestedAt: imageResult.ok ? imageReviewRequestedAt : undefined,
        reviewExpiresAt: canAcceptImageResult(imageResult) ? imageReviewExpiresAt : undefined,
      }, targetConversationId, imageResult.ok ? imageResultMessageId : uid());
    }
    if (imageResultMessage && canAcceptImageResult(imageResult)) {
      window.setTimeout(() => {
        if (shouldAutoAcceptImageResult(imageResultMessage, targetConversationId)) {
          void handleAcceptImageResult(imageResultMessage, true);
        }
      }, AUTO_CONFIRM_TIMEOUT_MS);
    }
    await clearPendingImageJob(targetConversationId, imageResultLastPhaseForJob(pendingImageJob, imageResult), {
      plan_approved: pendingImageJob.kind === "image_generation" ? true : undefined,
      plan_markdown: artifact.plan?.plan_markdown,
      image_revision_feedback: pendingImageJob.revision_feedback || artifact.imageRevisionFeedback,
      image_edit_done: pendingImageJob.kind === "direct_image_edit" ? imageResult.ok : undefined,
      intake_context: artifact.intakeContext,
      materials: artifact.materials || [],
      image_prepare: imagePrepare,
      image_result: imageResult,
      image_edit_request: artifact.imageEditRequest,
      pendingImageEditRequest: pendingImageEditRequestRef.current,
      pending_image_edit_request: pendingImageEditRequestRef.current,
    }).catch(() => {});
  };

  const handleCompletedImageAssetEditJob = async (
    pendingImageJob: PendingImageJob,
    editResult: ImageAssetEditResponse | ImageAssetFusionResponse,
    processedKey: string,
  ) => {
    const targetConversationId = pendingImageJob.conversation_id;
    const reference = pendingImageJob.sceneGlobalAssetReference;
    const storyboardMessage = reference
      ? findStoryboardMessageForGlobalAsset(reference, targetConversationId, [
          pendingImageJob.source_message_id,
          pendingImageJob.storyboard_message_id || "",
        ])
      : undefined;
    const fallbackArtifact = pendingImageJob.artifact?.videoScenePackages ? pendingImageJob.artifact : undefined;
    const baseArtifact = storyboardMessage?.artifact?.videoScenePackages ? storyboardMessage.artifact : fallbackArtifact;
    const baseVideoScenePackages = baseArtifact?.videoScenePackages;
    if (!reference || !baseArtifact || !baseVideoScenePackages) {
      releaseArtifactAction(processedKey);
      pushAssistant("素材图片编辑任务完成，但没有找到对应的场景包卡片，请从当前场景包手动重试。", targetConversationId);
      await clearPendingImageJob(targetConversationId, "scene_global_asset_edit_failed", {
        scene_global_asset_edit_error: "缺少对应的场景包 artifact",
      }).catch(() => {});
      return;
    }
    const isFusion = "fused_image" in editResult;
    const nextUrl = assetUpdateImageUrl(editResult);
    const quotaInsufficient = isQuotaInsufficientPayload(editResult);
    if (!editResult.ok || !nextUrl) {
      releaseArtifactAction(processedKey);
      const failedRequest = pendingImageJob.request as ImageAssetEditJobRequest;
      const retryMaterials = [
        {
          ...reference,
          source_image_url: reference.source_image_url,
          url: reference.source_image_url,
          storyboard_message_id: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
        },
        ...uploadedReferenceMaterials(failedRequest.materials || []),
      ];
      const retrySelection: ImageEditModelSelection = {
        model: String(failedRequest.model || "gpt-image-2"),
        ratio: String(failedRequest.ratio || defaultGlobalSceneAssetRatio(reference.asset_group)),
        size: String(failedRequest.size || "4K"),
      };
      const retryRequest: PendingImageEditRequest = {
        conversationId: targetConversationId,
        prompt: String(failedRequest.prompt || ""),
        formValues: {
          image_goal: reference.name,
          image_operation: "image_edit",
          image_model: retrySelection.model,
          image_size: retrySelection.ratio,
          image_quality: retrySelection.size,
        },
        intakeContext: {
          image_operation: "image_edit",
          image_model: retrySelection.model,
          image_size: retrySelection.ratio,
          image_quality: retrySelection.size,
          scene_global_asset_reference: reference,
        },
        materials: retryMaterials,
        selection: retrySelection,
        mode: isFusion ? "scene_global_asset_fusion" : "scene_global_asset_edit",
        sceneGlobalAssetReference: reference,
        storyboardMessageId: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
      };
      pushArtifact("全局素材图片编辑失败，请查看错误信息。", {
        type: "image_result",
        title: "全局素材图片编辑结果",
        description: quotaInsufficient ? quotaMessage(editResult.message || "图片编辑额度不足。") : editResult.message,
        actionLabel: "查看",
        imageResult: {
          ok: false,
          method: editResult.method,
          endpoint: editResult.endpoint,
          task_id: null,
          images: nextUrl ? [assetUpdateImage(editResult)] : [],
          error: editResult.message,
          message: editResult.message,
          quota_insufficient: quotaInsufficient,
          raw: editResult.raw,
        },
        intent: "image",
        materials: retryMaterials,
        imageEditRequest: retryRequest as unknown as Record<string, unknown>,
        imageEditConfirmedSelection: retrySelection,
        imageRevisionFeedback: retryRequest.prompt,
      }, targetConversationId);
      await clearPendingImageJob(
        targetConversationId,
        quotaInsufficient
          ? isFusion
            ? "scene_global_asset_fusion_quota_paused"
            : "scene_global_asset_edit_quota_paused"
          : isFusion
            ? "scene_global_asset_fusion_failed"
            : "scene_global_asset_edit_failed",
        {
        scene_global_asset_edit: editResult,
        scene_global_asset_fusion: isFusion ? editResult : undefined,
        },
      ).catch(() => {});
      return;
    }

    const originalImageUrl = String(
      (reference as Record<string, unknown>).original_image_url ||
        (reference as Record<string, unknown>).originalImageUrl ||
        reference.source_image_url,
    );
    const review: SceneGlobalAssetEditReview = {
      asset_id: reference.asset_id,
      asset_group: reference.asset_group,
      asset_name: reference.name,
      original_image_url: originalImageUrl,
      source_image_url: reference.source_image_url,
      edited_image_url: nextUrl,
      source_message_id: storyboardMessage?.id || pendingImageJob.source_message_id,
      storyboard_message_id: storyboardMessage?.id || pendingImageJob.storyboard_message_id,
      videoScenePackages: baseVideoScenePackages,
      originalVideoScenePackages: baseArtifact.originalVideoScenePackages || baseVideoScenePackages,
      editResult,
      request: pendingImageJob.request as unknown as Record<string, unknown>,
      selection: {
        model: String((pendingImageJob.request as ImageAssetEditJobRequest).model || "gpt-image-2"),
        ratio: String((pendingImageJob.request as ImageAssetEditJobRequest).ratio || defaultGlobalSceneAssetRatio(reference.asset_group)),
        size: String((pendingImageJob.request as ImageAssetEditJobRequest).size || "4K"),
      },
      prompt: String((pendingImageJob.request as ImageAssetEditJobRequest).prompt || ""),
      is_fusion: isFusion,
    };
    pushArtifact("全局素材候选图已生成，请确认是否替换到当前场景包。", {
      type: "image_result",
      title: isFusion ? "全局素材图片融合结果" : "全局素材图片编辑结果",
      description: `候选新图已生成。确认后才会替换「${reference.name}」并同步更新分镜引用。`,
      actionLabel: "查看",
      imageResult: {
        ok: true,
        method: editResult.method,
        endpoint: editResult.endpoint,
        task_id: null,
        images: [assetUpdateImage(editResult)],
        error: null,
        message: editResult.message,
        quota_insufficient: false,
        raw: editResult.raw,
      },
      intent: "image",
      materials: [{
        ...reference,
        url: nextUrl,
        source_image_url: nextUrl,
        original_image_url: originalImageUrl,
        storyboard_message_id: review.storyboard_message_id,
      }],
      imageRevisionFeedback: review.prompt,
      imageEditConfirmedSelection: review.selection,
      sceneGlobalAssetEditReview: review,
      reviewRequestedAt: new Date().toISOString(),
    }, targetConversationId);

    await clearPendingImageJob(targetConversationId, isFusion ? "scene_global_asset_fusion_review_pending" : "scene_global_asset_edit_review_pending", {
      scene_global_asset_edit: editResult,
      scene_global_asset_fusion: isFusion ? editResult : undefined,
      scene_global_asset_edit_review: review,
      pendingImageEditRequest: null,
      pending_image_edit_request: null,
    }).catch(() => {});
    return;
  };

  const startAndResumeVideoMergeJob = async ({
    targetConversationId,
    processedKey,
  }: {
    targetConversationId: string;
    processedKey: string;
    sourceMessageId?: string;
    artifact?: ChatArtifact;
    videoScenePackages?: PrepareScenePackagesResponse;
    generatedSceneVideos?: NonNullable<ChatArtifact["generatedSceneVideos"]>;
    originalVideoScenePackages?: PrepareScenePackagesResponse;
    mergePurpose?: "generation" | "regeneration";
    affectedSceneIds?: string[];
  }) => {
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      notifyLegacyVideoJobBlocked(targetConversationId, processedKey);
    }
  };

  const resumePendingVideoJob = async (_pendingVideoJob: PendingVideoJob, _processedKey = "") => {
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) return;
  };

  const resumePendingJianyingDraftJob = async (_pendingJianyingDraftJob: PendingJianyingDraftJob) => {
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) return;
  };

  const resumePendingImageJob = async (pendingImageJob: PendingImageJob, processedKey = "") => {
    const pollKey = `${pendingImageJob.conversation_id}:${pendingImageJob.job_api}:${pendingImageJob.job_id}`;
    if (activeImageJobPollsRef.current.has(pollKey)) return;
    activeImageJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingImageJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingImageJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      if (pendingImageJob.job_api === "edit_asset") {
        const status = await api.getImageAssetEditJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const editResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageAssetEditJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!editResult || stopIfHidden()) return;
        await handleCompletedImageAssetEditJob(pendingImageJob, editResult, processedKey);
      } else if (pendingImageJob.job_api === "fuse_asset") {
        const status = await api.getImageAssetFusionJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const fusionResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageAssetFusionJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!fusionResult || stopIfHidden()) return;
        await handleCompletedImageAssetEditJob(pendingImageJob, fusionResult, processedKey);
      } else {
        const status = await api.getImageGenerationJob(pendingImageJob.job_id);
        if (stopIfHidden()) return;
        const imageResult =
          (status.status === "completed" || status.status === "quota_paused") && status.result
            ? status.result
            : await api.pollImageGenerationJob(pendingImageJob.job_id, shouldContinuePolling);
        if (!imageResult || stopIfHidden()) return;
        await handleCompletedImageGenerationJob(pendingImageJob, imageResult, processedKey);
      }
    } catch (err) {
      if (stopIfHidden()) return;
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的图片生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新图片卡片手动重试。"
          : `继续查询图片生成任务失败:${message}`,
        pendingImageJob.conversation_id,
      );
      await clearPendingImageJob(pendingImageJob.conversation_id, "image_job_resume_failed", {
        image_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activeImageJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingImageJob.conversation_id, false);
    }
  };

  const resumePendingScenePackageJob = async (_pendingScenePackageJob: PendingScenePackageJob, _processedKey = "") => {
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) return;
  };

  const pollPptJobResult = async <T extends Record<string, unknown>>(
    pendingPptJob: PendingPptJob,
    onStatus?: (status: PptJobStatusResponse) => void,
    shouldContinue: () => boolean = () => true,
  ): Promise<T | null> => {
    if (!shouldContinue()) return null;
    const status = await api.getPptJob(pendingPptJob.job_id);
    if (!shouldContinue()) return null;
    onStatus?.(status);
    if ((status.status === "completed" || status.status === "quota_paused") && status.result) return status.result as T;
    if (status.status === "failed") {
      return {
        ok: false,
        message: status.error || status.message || "PPT 生成失败",
        error: status.error || status.message || "PPT 生成失败",
      } as unknown as T;
    }
    return api.pollPptJob<T>(pendingPptJob.job_id, onStatus, shouldContinue);
  };

  const completedPptLastPhase = (pendingPptJob: PendingPptJob, ok: boolean): string => {
    const failed = !ok;
    if (pendingPptJob.kind === "summary_generation") return failed ? "ppt_outline_failed" : "ppt_outline_review";
    if (pendingPptJob.kind === "summary_update") return failed ? "ppt_outline_update_failed" : "ppt_outline_updated";
    if (pendingPptJob.kind === "content_json_generation") return failed ? "ppt_content_json_failed" : "ppt_content_json_ready";
    if (pendingPptJob.kind === "image_generation" || pendingPptJob.kind === "image_regeneration") return failed ? "ppt_images_failed" : "ppt_images_ready";
    if (pendingPptJob.kind === "file_generation" || pendingPptJob.kind === "file_regeneration") return failed ? "ppt_file_failed" : "ppt_file_ready";
    return failed ? "ppt_job_failed" : "ppt_job_completed";
  };

  const findLatestPptImagesMessageId = (targetConversationId: string): string => {
    const message = [...messages]
      .reverse()
      .find((item) => messageConversationId(item, targetConversationId) === targetConversationId && item.artifact?.type === "ppt_images");
    return message?.id || "";
  };

  const handleCompletedPptSummaryJob = async (pendingPptJob: PendingPptJob, result: PptSummaryResult) => {
    const context = pendingPptJob.context;
    if (!context) {
      throw new Error("PPT 大纲任务缺少恢复上下文");
    }
    const targetConversationId = pendingPptJob.conversation_id;
    pushPptOutlineArtifact(result, context, targetConversationId);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, result.ok), {
      intent: "ppt",
      form_values: context.formValues,
      intake_context: context.intakeContext,
      materials: context.materials || [],
      ppt_summary: result,
      ppt_outline_feedback: pendingPptJob.kind === "summary_update"
        ? (pendingPptJob.request as PptSummaryUpdateJobRequest).modification_opinion
        : undefined,
    }).catch(() => {});
  };

  const startPptImagesFromContentJson = async (
    contentJson: PptContentJsonResult,
    sourceArtifact: ChatArtifact,
    pendingPptJob: PendingPptJob,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const projectId =
      numericValue(contentJson.smart_ppt_project_id)
      || numericValue((pendingPptJob.request as PptContentJsonJobRequest).smart_ppt_project_id)
      || pptProjectId(sourceArtifact);
    if (!projectId) {
      releaseArtifactAction(processedKey);
      pushAssistant("当前 PPT 项目 ID 缺失，无法继续生成页面图片。", targetConversationId);
      await clearPendingPptJob(targetConversationId, "ppt_images_failed", {
        intent: "ppt",
        ppt_content_json: contentJson,
      }).catch(() => {});
      return;
    }
    const imageArtifactSource = { ...sourceArtifact, pptContentJson: contentJson };
    const pendingImages = pendingPptImagesFromContentJson(contentJson, projectId);
    const imageMessage = pushPptImagesArtifact(pendingImages, contentJson, imageArtifactSource, targetConversationId);
    const request: PptImagesJobRequest = {
      content_json: contentJson.content_json || contentJson.pages || [],
      smart_ppt_project_id: projectId,
    };
    const started = await api.createPptImagesJob(request);
    const nextPendingPptJob: PendingPptJob = {
      job_id: started.job_id,
      conversation_id: targetConversationId,
      source_message_id: pendingPptJob.source_message_id,
      kind: "image_generation",
      started_at: new Date().toISOString(),
      request,
      artifact: imageArtifactSource,
      image_message_id: imageMessage.id,
    };
    await persistPendingPptJob(nextPendingPptJob, targetConversationId, "ppt_images_running", {
      intent: "ppt",
      ppt_content_json: contentJson,
      ppt_images: pendingImages,
    });
    await resumePendingPptJob(nextPendingPptJob, processedKey);
  };

  const handleCompletedPptContentJsonJob = async (
    pendingPptJob: PendingPptJob,
    contentJson: PptContentJsonResult,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const sourceArtifact = pendingPptJob.artifact;
    if (!sourceArtifact) {
      throw new Error("PPT 页面结构任务缺少来源卡片");
    }
    if (!contentJson.ok) {
      releaseArtifactAction(processedKey);
      const projectId = pptProjectId(sourceArtifact) || numericValue((pendingPptJob.request as PptContentJsonJobRequest).smart_ppt_project_id) || 0;
      const failedImages: PptImagesResult = {
        ok: false,
        smart_ppt_project_id: projectId || null,
        pages: [],
        message: String(contentJson.message || contentJson.error || "PPT 页面 JSON 生成失败。"),
        quota_insufficient: Boolean(contentJson.quota_insufficient),
      };
      pushPptImagesArtifact(failedImages, contentJson, sourceArtifact, targetConversationId);
      await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, false), {
        intent: "ppt",
        ppt_content_json: contentJson,
        ppt_images: failedImages,
      }).catch(() => {});
      return;
    }
    pushAssistant("PPT 页面结构已生成，正在并行生成每页 PPT 图片…", targetConversationId);
    await startPptImagesFromContentJson(contentJson, sourceArtifact, pendingPptJob, processedKey);
  };

  const handleCompletedPptImagesJob = async (
    pendingPptJob: PendingPptJob,
    pptImages: PptImagesResult,
    processedKey = "",
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(targetConversationId);
    if (imageMessageId) updatePptImagesArtifactInMessage(imageMessageId, targetConversationId, pptImages, pendingPptJob.artifact);
    if (!pptImages.ok) releaseArtifactAction(processedKey);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, pptImages.ok), {
      intent: "ppt",
      ppt_content_json: pendingPptJob.artifact?.pptContentJson,
      ppt_images: pptImages,
    }).catch(() => {});
  };

  const handleCompletedPptImageRegenerationJob = async (
    pendingPptJob: PendingPptJob,
    result: Record<string, unknown>,
  ) => {
    const targetConversationId = pendingPptJob.conversation_id;
    const sourceArtifact = pendingPptJob.artifact;
    const pageIndex = (pendingPptJob.request as PptImageRegenerationJobRequest).page_index;
    if (!sourceArtifact?.pptImages) {
      throw new Error("PPT 单页重生任务缺少来源页面图片");
    }
    const nextPage = result.page as PptPageImage | undefined;
    const nextPages = sourceArtifact.pptImages.pages.map((page) => {
      if (page.page_index !== pageIndex) return page;
      if (!nextPage) {
        return {
          ...page,
          status: "failed",
          image_url: null,
          error: String(result.message || result.error || "本页图片重新生成失败。"),
        };
      }
      return {
        ...nextPage,
        page_index: pageIndex,
        json_content: nextPage.json_content || page.json_content || (pendingPptJob.request as PptImageRegenerationJobRequest).page_json,
        status: nextPage.status || (nextPage.image_url ? "completed" : "failed"),
      };
    });
    const nextImages: PptImagesResult = {
      ...sourceArtifact.pptImages,
      ok: nextPages.length > 0 && nextPages.every((page) => page.status === "completed" && Boolean(page.image_url)),
      pages: nextPages,
      message: String(result.message || "PPT 页面图片已重新生成。"),
      quota_insufficient: Boolean(result.quota_insufficient),
    };
    const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(targetConversationId);
    if (imageMessageId) updatePptImagesArtifactInMessage(imageMessageId, targetConversationId, nextImages, sourceArtifact);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, nextImages.ok), {
      intent: "ppt",
      ppt_content_json: sourceArtifact.pptContentJson,
      ppt_images: nextImages,
    }).catch(() => {});
  };

  const handleCompletedPptFileJob = async (pendingPptJob: PendingPptJob, pptFile: PptFileResult, processedKey = "") => {
    const sourceArtifact = pendingPptJob.artifact;
    if (!sourceArtifact) {
      throw new Error("PPT 附件任务缺少来源卡片");
    }
    const targetConversationId = pendingPptJob.conversation_id;
    if (!pptFile.ok) releaseArtifactAction(processedKey);
    pushPptFileArtifact(pptFile, sourceArtifact, targetConversationId);
    await clearPendingPptJob(targetConversationId, completedPptLastPhase(pendingPptJob, pptFile.ok), {
      intent: "ppt",
      ppt_content_json: sourceArtifact.pptContentJson,
      ppt_images: sourceArtifact.pptImages,
      ppt_file: pptFile,
    }).catch(() => {});
  };

  const resumePendingPptJob = async (pendingPptJob: PendingPptJob, processedKey = "") => {
    const pollKey = `${pendingPptJob.conversation_id}:${pendingPptJob.kind}:${pendingPptJob.job_id}`;
    if (activePptJobPollsRef.current.has(pollKey)) return;
    activePptJobPollsRef.current.add(pollKey);
    let pausedForHiddenConversation = false;
    const shouldContinuePolling = () => isVisibleConversation(pendingPptJob.conversation_id);
    const stopIfHidden = () => {
      if (shouldContinuePolling()) return false;
      pausedForHiddenConversation = true;
      return true;
    };
    setBusyForConversation(pendingPptJob.conversation_id, true);
    try {
      if (stopIfHidden()) return;
      if (pendingPptJob.kind === "summary_generation" || pendingPptJob.kind === "summary_update") {
        const result = await pollPptJobResult<PptSummaryResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptSummaryJob(pendingPptJob, result);
      } else if (pendingPptJob.kind === "content_json_generation") {
        const result = await pollPptJobResult<PptContentJsonResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptContentJsonJob(pendingPptJob, result, processedKey);
      } else if (pendingPptJob.kind === "image_generation") {
        const imageMessageId = pendingPptJob.image_message_id || findLatestPptImagesMessageId(pendingPptJob.conversation_id);
        const result = await pollPptJobResult<PptImagesResult>(pendingPptJob, (status) => {
          const partialImages = status.result as PptImagesResult | null;
          if (partialImages?.pages && imageMessageId) {
            updatePptImagesArtifactInMessage(imageMessageId, pendingPptJob.conversation_id, partialImages, pendingPptJob.artifact);
          }
        }, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptImagesJob(pendingPptJob, result, processedKey);
      } else if (pendingPptJob.kind === "image_regeneration") {
        const result = await pollPptJobResult<Record<string, unknown>>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptImageRegenerationJob(pendingPptJob, result);
      } else {
        const result = await pollPptJobResult<PptFileResult>(pendingPptJob, undefined, shouldContinuePolling);
        if (!result || stopIfHidden()) return;
        await handleCompletedPptFileJob(pendingPptJob, result, processedKey);
      }
    } catch (err) {
      if (stopIfHidden()) return;
      releaseArtifactAction(processedKey);
      const message = err instanceof Error ? err.message : String(err);
      pushAssistant(
        message.includes("404")
          ? "之前的 PPT 生成任务不存在或已过期。为避免重复生成，我没有自动重启任务，请从最新 PPT 卡片手动重试。"
          : `继续查询 PPT 生成任务失败:${message}`,
        pendingPptJob.conversation_id,
      );
      await clearPendingPptJob(pendingPptJob.conversation_id, "ppt_job_resume_failed", {
        ppt_job_resume_error: message,
      }).catch(() => {});
    } finally {
      if (pausedForHiddenConversation) releaseArtifactAction(processedKey);
      activePptJobPollsRef.current.delete(pollKey);
      setBusyForConversation(pendingPptJob.conversation_id, false);
    }
  };

  const pushReviewArtifact = (phase: TaskPhase) => {
    const artifact = REVIEW_ARTIFACT[phase];
    if (!artifact) return;
    const key = `${phase}:artifact`;
    if (announcedPhasesRef.current.has(key)) return;
    announcedPhasesRef.current.add(key);
    pushArtifact(PHASE_MSG[phase] || "请在画布确认。", artifact);
  };

  const appendTimelineEvent = (event: TaskEvent) => {
    const entry = toTimelineEntry(event);
    if (!entry) return;
    setCanvas((c) => {
      const timeline = c.timeline || [];
      if (timeline.some((item) => item.id === entry.id)) return c;
      return { ...c, timeline: [...timeline, entry].slice(-80) };
    });
  };

  async function reconcileTaskFromServer(taskId: string) {
    try {
      const task = await api.getTask(taskId);
      setActiveTaskId(task.task_id);
      const phase = task.phase as TaskPhase;
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: phase || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));

      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        setCanvasOpen(true);
        await loadResults(phase);
        pushReviewArtifact(phase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
        }
        return;
      }

      if (task.phase === "brief_review") {
        setCanvasOpen(true);
        if (!announcedPhasesRef.current.has("brief_review")) {
          announcedPhasesRef.current.add("brief_review");
          pushAssistant(PHASE_MSG.brief_review);
        }
        return;
      }

      if (task.status === "done") {
        await loadResults("done");
        return;
      }

      if (task.status === "error") {
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
        setBusy(false);
      }
    } catch {
      /* keep restored snapshot if server reconciliation fails */
    }
  }

  const applySnapshot = (snapshot: Partial<WorkspaceSnapshot>, targetConversationId: string) => {
    if (Array.isArray(snapshot.messages)) {
      messagesRef.current = snapshot.messages;
      setMessages(snapshot.messages);
      persistedChatMessageIdsRef.current = new Set(
        snapshot.messages
          .filter((item) => messageConversationId(item, targetConversationId) === targetConversationId)
          .map((item) => item.id)
          .filter(Boolean),
      );
    }
    setPendingMaterials(Array.isArray(snapshot.pendingMaterials) ? snapshot.pendingMaterials : []);
    setDialogOpen(false);
    setPendingFormValues({});
    setPendingCore("");
    pendingDialogContextRef.current = null;
    flowDraftRef.current = snapshot.flowDraft || null;
    pendingMessageJobRef.current = snapshot.pendingMessageJob || snapshot.pending_message_job || null;
    const rawRuntimeTurns = snapshot.pendingAgentRuntimeTurns
      || snapshot.pending_agent_runtime_turns;
    const restoredRuntimeTurns = (
      Array.isArray(rawRuntimeTurns) ? rawRuntimeTurns : []
    )
      .map((item) => parsePendingSupervisorTurn(item, targetConversationId))
      .filter((item): item is PendingSupervisorTurn => item !== null);
    pendingSupervisorTurnsRef.current = restoredRuntimeTurns;
    if (targetConversationId) {
      pendingSupervisorTurnsByConversationRef.current.set(
        targetConversationId,
        restoredRuntimeTurns,
      );
      const unavailableNoticeVersion = snapshot.agentRuntimeUnavailableNoticeVersion
        ?? snapshot.agent_runtime_unavailable_notice_version;
      if (
        typeof unavailableNoticeVersion === "number"
        && Number.isFinite(unavailableNoticeVersion)
        && unavailableNoticeVersion > 0
      ) {
        unavailableSupervisorNoticeVersionsRef.current.set(
          targetConversationId,
          unavailableNoticeVersion,
        );
      } else {
        unavailableSupervisorNoticeVersionsRef.current.delete(
          targetConversationId,
        );
      }
    }
    setPendingSupervisorTurns(restoredRuntimeTurns);
    pendingPlanMessagePersistenceIdsRef.current = new Set(
      pendingMessageJobRef.current?.continue_after_save?.type === "plan_save"
        ? [pendingMessageJobRef.current.source_message_id]
        : [],
    );
    pendingIntakeJobRef.current = snapshot.pendingIntakeJob || snapshot.pending_intake_job || null;
    pendingDirectionJobRef.current = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    pendingPlanJobRef.current = snapshot.pendingPlanJob || snapshot.pending_plan_job || null;
    planRevisionArtifactRef.current = snapshot.pendingPlanRevisionRequest || snapshot.pending_plan_revision_request || null;
    const restoredPlanRevisionChoice = snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null;
    setPendingPlanRevisionChoice(restoredPlanRevisionChoice);
    setAgentRevisionSourceMessageId(
      planRevisionArtifactRef.current?.sourceMessageId
        || restoredPlanRevisionChoice?.sourceMessageId
        || (pendingPlanJobRef.current?.kind === "plan_revision" ? pendingPlanJobRef.current.source_message_id : ""),
    );
    pendingImageEditRequestRef.current = snapshot.pendingImageEditRequest || null;
    imageEditConfirmedSelectionsRef.current = snapshot.imageEditConfirmedSelections || {};
    pendingImageJobRef.current = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    imageRevisionArtifactRef.current = snapshot.pendingImageRevision || snapshot.pending_image_revision || null;
    pptOutlineRevisionArtifactRef.current = snapshot.pendingPptOutlineRevision || snapshot.pending_ppt_outline_revision || null;
    pendingScenePackageJobRef.current = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    setPendingScenePackageResumeVersion((version) => version + 1);
    pendingVideoJobRef.current = snapshot.pendingVideoJob || snapshot.pending_video_job || null;
    videoRevisionArtifactRef.current = snapshot.pendingVideoRevision || snapshot.pending_video_revision || null;
    pendingPptJobRef.current = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    const pendingJianyingDraftJob = snapshot.pendingJianyingDraftJob || snapshot.pending_jianying_draft_job || null;
    pendingJianyingDraftJobRef.current =
      pendingJianyingDraftJob?.conversation_id === targetConversationId ? pendingJianyingDraftJob : null;
    setJianyingDraftRecordsForConversation(
      targetConversationId,
      snapshot.jianyingDraftRecords || snapshot.jianying_draft_records || {},
    );
    const restoredWorkflowProgress = snapshot.workflowProgress || snapshot.workflow_progress || null;
    workflowProgressConversationIdRef.current = targetConversationId;
    workflowProgressRef.current = restoredWorkflowProgress;
    setWorkflowProgress(restoredWorkflowProgress);
    const restoredPlanAnchors = snapshot.videoAgentPlanAnchors || snapshot.video_agent_plan_anchors;
    if (restoredPlanAnchors && typeof restoredPlanAnchors === "object") {
      const next = Object.fromEntries(
        Object.entries(restoredPlanAnchors).filter(
          (entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string",
        ),
      );
      videoAgentPlanAnchorsRef.current = next;
      setVideoAgentPlanAnchors(next);
    }
    const restoredAssetPackageAnchor = String(
      snapshot.assetPackageAnchorMessageId
      || snapshot.asset_package_anchor_message_id
      || "",
    ).trim();
    if (restoredAssetPackageAnchor) {
      assetPackageAnchorMessageIdRef.current = restoredAssetPackageAnchor;
      setAssetPackageAnchorMessageId(restoredAssetPackageAnchor);
    }
    setPptDoneForConversation(targetConversationId, snapshot.ppt_done === true);
    setReferencedMaterials([]);
    if (snapshot.canvas) setCanvas(snapshot.canvas);
    if (typeof snapshot.canvasOpen === "boolean") {
      // 历史进入会先清空 selectedStoryboardMessageId；若仍按快照开画布，
      // V2 会落到空「画布」占位（Brief、生成进度与成片…）。无分镜/方案内容时保持关闭。
      if (!snapshot.canvasOpen) {
        setCanvasOpen(false);
      } else {
        const snapshotMessages = Array.isArray(snapshot.messages)
          ? snapshot.messages
          : messagesRef.current;
        const conversationMessages = snapshotMessages.filter((message) => (
          messageConversationId(message, targetConversationId) === targetConversationId
        ));
        const latestPackages = [...conversationMessages].reverse().find((message) => (
          Boolean(message.artifact?.videoScenePackages)
        ));
        const latestPlan = [...conversationMessages].reverse().find((message) => (
          message.artifact?.type === "plan" && Boolean(message.artifact.plan)
        ));
        const canvasState = snapshot.canvas;
        const hasLegacyCanvasContent = Boolean(
          canvasState
          && (
            (canvasState.phase && canvasState.phase !== "idle")
            || canvasState.brief
            || canvasState.selectedVideo
          ),
        );
        if (latestPackages) {
          setSelectedStoryboardMessageId(latestPackages.id);
          setSelectedPlanEditorMessageId("");
          setCanvasOpen(true);
        } else if (latestPlan) {
          setSelectedPlanEditorMessageId(latestPlan.id);
          setSelectedStoryboardMessageId("");
          setCanvasOpen(true);
        } else if (hasLegacyCanvasContent) {
          setCanvasOpen(true);
        } else {
          setCanvasOpen(false);
        }
      }
    }
    if (typeof snapshot.briefConfirmed === "boolean") {
      setBriefConfirmed(snapshot.briefConfirmed);
      briefConfirmedRef.current = snapshot.briefConfirmed;
    }
    if (snapshot.taskId) setActiveTaskId(snapshot.taskId);
    if (typeof snapshot.lastEventId === "number") {
      lastEventIdRef.current = snapshot.lastEventId;
      seenEventIdsRef.current = new Set(Array.from({ length: snapshot.lastEventId }, (_, i) => i + 1));
    }
    if (Array.isArray(snapshot.announcedPhases)) announcedPhasesRef.current = new Set(snapshot.announcedPhases);
    if (typeof snapshot.briefReadyShown === "boolean") briefReadyShownRef.current = snapshot.briefReadyShown;
  };

  const makeSnapshot = (snapshotConversationId = currentConversationId): WorkspaceSnapshot => {
    const scenePackageSnapshot = latestScenePackageSnapshotForConversation(messagesRef.current, snapshotConversationId);
    const activePlanSnapshot = activePlanSnapshotForConversation(
      messagesRef.current,
      snapshotConversationId,
      pendingPlanMessagePersistenceIdsRef.current,
    );
    return {
      taskId: currentTaskId,
      pendingMaterials,
      flowDraft: flowDraftRef.current,
      pendingMessageJob:
        pendingMessageJobRef.current?.conversation_id === snapshotConversationId ? pendingMessageJobRef.current : null,
      pending_message_job:
        pendingMessageJobRef.current?.conversation_id === snapshotConversationId ? pendingMessageJobRef.current : null,
      pendingAgentRuntimeTurns: pendingSupervisorTurnsRef.current.filter(
        (item) => item.conversationId === snapshotConversationId,
      ),
      pending_agent_runtime_turns: pendingSupervisorTurnsRef.current.filter(
        (item) => item.conversationId === snapshotConversationId,
      ),
      agentRuntimeUnavailableNoticeVersion:
        unavailableSupervisorNoticeVersionsRef.current.get(snapshotConversationId),
      agent_runtime_unavailable_notice_version:
        unavailableSupervisorNoticeVersionsRef.current.get(snapshotConversationId),
      pendingIntakeJob:
        pendingIntakeJobRef.current?.conversation_id === snapshotConversationId ? pendingIntakeJobRef.current : null,
      pending_intake_job:
        pendingIntakeJobRef.current?.conversation_id === snapshotConversationId ? pendingIntakeJobRef.current : null,
      pendingDirectionJob:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pending_direction_job:
        pendingDirectionJobRef.current?.conversation_id === snapshotConversationId ? pendingDirectionJobRef.current : null,
      pendingPlanJob:
        pendingPlanJobRef.current?.conversation_id === snapshotConversationId ? pendingPlanJobRef.current : null,
      pending_plan_job:
        pendingPlanJobRef.current?.conversation_id === snapshotConversationId ? pendingPlanJobRef.current : null,
      pendingPlanRevisionRequest:
        planRevisionArtifactRef.current?.conversationId === snapshotConversationId ? planRevisionArtifactRef.current : null,
      pending_plan_revision_request:
        planRevisionArtifactRef.current?.conversationId === snapshotConversationId ? planRevisionArtifactRef.current : null,
      pendingPlanRevisionChoice:
        pendingPlanRevisionChoice?.conversationId === snapshotConversationId ? pendingPlanRevisionChoice : null,
      pending_plan_revision_choice:
        pendingPlanRevisionChoice?.conversationId === snapshotConversationId ? pendingPlanRevisionChoice : null,
      pendingImageEditRequest:
        pendingImageEditRequestRef.current?.conversationId === snapshotConversationId ? pendingImageEditRequestRef.current : null,
      imageEditConfirmedSelections: imageEditConfirmedSelectionsRef.current,
      pendingImageJob:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pending_image_job:
        pendingImageJobRef.current?.conversation_id === snapshotConversationId ? pendingImageJobRef.current : null,
      pendingImageRevision:
        imageRevisionArtifactRef.current?.conversationId === snapshotConversationId ? imageRevisionArtifactRef.current : null,
      pending_image_revision:
        imageRevisionArtifactRef.current?.conversationId === snapshotConversationId ? imageRevisionArtifactRef.current : null,
      pendingPptOutlineRevision:
        pptOutlineRevisionArtifactRef.current?.conversationId === snapshotConversationId ? pptOutlineRevisionArtifactRef.current : null,
      pending_ppt_outline_revision:
        pptOutlineRevisionArtifactRef.current?.conversationId === snapshotConversationId ? pptOutlineRevisionArtifactRef.current : null,
      pendingScenePackageJob:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pending_scene_package_job:
        pendingScenePackageJobRef.current?.conversation_id === snapshotConversationId ? pendingScenePackageJobRef.current : null,
      pendingVideoJob:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pending_video_job:
        pendingVideoJobRef.current?.conversation_id === snapshotConversationId ? pendingVideoJobRef.current : null,
      pendingVideoRevision:
        videoRevisionArtifactRef.current?.conversationId === snapshotConversationId ? videoRevisionArtifactRef.current : null,
      pending_video_revision:
        videoRevisionArtifactRef.current?.conversationId === snapshotConversationId ? videoRevisionArtifactRef.current : null,
      pendingPptJob:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      pending_ppt_job:
        pendingPptJobRef.current?.conversation_id === snapshotConversationId ? pendingPptJobRef.current : null,
      pendingJianyingDraftJob:
        pendingJianyingDraftJobRef.current?.conversation_id === snapshotConversationId ? pendingJianyingDraftJobRef.current : null,
      pending_jianying_draft_job:
        pendingJianyingDraftJobRef.current?.conversation_id === snapshotConversationId ? pendingJianyingDraftJobRef.current : null,
      jianyingDraftRecords: jianyingDraftRecordsForConversation(snapshotConversationId),
      jianying_draft_records: jianyingDraftRecordsForConversation(snapshotConversationId),
      ppt_done: isPptDoneForConversation(snapshotConversationId),
      image_accepted: latestImageResultArtifactForConversation(messagesRef.current, snapshotConversationId)?.imageAccepted === true,
      video_accepted: latestVideoResultArtifactForConversation(messagesRef.current, snapshotConversationId)?.videoAccepted === true,
      canvas,
      // 不把空占位画布写进会话快照，避免历史进入反复弹出无内容「画布」。
      canvasOpen: Boolean(
        canvasOpen
        && (
          Boolean(selectedStoryboardMessageId)
          || Boolean(selectedPlanEditorMessageId)
          || (canvas.phase && canvas.phase !== "idle")
          || Boolean(canvas.brief)
          || Boolean(canvas.selectedVideo)
        )
      ),
      briefConfirmed,
      lastEventId: lastEventIdRef.current,
      announcedPhases: Array.from(announcedPhasesRef.current),
      briefReadyShown: briefReadyShownRef.current,
      workflowProgress:
        workflowProgressConversationIdRef.current === snapshotConversationId ? workflowProgressRef.current : null,
      workflow_progress:
        workflowProgressConversationIdRef.current === snapshotConversationId ? workflowProgressRef.current : null,
      videoAgentPlanAnchors: videoAgentPlanAnchorsRef.current,
      video_agent_plan_anchors: videoAgentPlanAnchorsRef.current,
      assetPackageAnchorMessageId: assetPackageAnchorMessageIdRef.current || undefined,
      asset_package_anchor_message_id: assetPackageAnchorMessageIdRef.current || undefined,
      ...activePlanSnapshot,
      ...scenePackageSnapshot,
    };
  };

  const resetWorkspace = () => {
    unsubRef.current();
    setActiveConversationId("");
    setActiveTaskId("");
    setMessages([]);
    setCanvas(EMPTY_CANVAS);
    setCanvasOpen(false);
    setSelectedStoryboardMessageId("");
    setDialogOpen(false);
    setPendingCore("");
    setPendingIntent("video");
    setPendingFormValues({});
    setPendingMaterials([]);
    setReferencedMaterials([]);
    setComposerPrefillRequest(null);
    setBusy(false);
    setResolvedOrchestrationMode("frontend_v2");
    setResolvedAgentRuntimeMode("off");
    primaryExecutionReadyRef.current = false;
    pendingSupervisorTurnsRef.current = [];
    unavailableSupervisorNoticeVersionsRef.current = new Map();
    unavailableSupervisorRecoveryInFlightRef.current = new Set();
    setPendingSupervisorTurns([]);
    setBriefConfirmed(false);
    briefConfirmedRef.current = false;
    seenEventIdsRef.current = new Set();
    announcedPhasesRef.current = new Set();
    processedArtifactIdsRef.current = new Set();
    pendingDialogContextRef.current = null;
    flowDraftRef.current = null;
    pendingMessageJobRef.current = null;
    pendingPlanMessagePersistenceIdsRef.current = new Set();
    pendingIntakeJobRef.current = null;
    pendingDirectionJobRef.current = null;
    pendingPlanJobRef.current = null;
    pendingImageEditRequestRef.current = null;
    imageEditConfirmedSelectionsRef.current = {};
    pendingImageJobRef.current = null;
    pendingScenePackageJobRef.current = null;
    pendingVideoJobRef.current = null;
    pendingPptJobRef.current = null;
    pendingJianyingDraftJobRef.current = null;
    jianyingDraftRecordsByConversationRef.current = new Map();
    workflowProgressConversationIdRef.current = "";
    workflowProgressRef.current = null;
    setWorkflowProgress(null);
    setAssetPackageAnchorMessageId("");
    setAssetPackageProgressSteps([]);
    setSceneVideoProgressSteps([]);
    setOptimisticGeneratingSceneIds([]);
    optimisticGeneratingRevisionRef.current = {};
    pptDoneConversationIdsRef.current = new Set();
    planRevisionArtifactRef.current = null;
    pptOutlineRevisionArtifactRef.current = null;
    setPendingPlanRevisionChoice(null);
    setAgentRevisionSourceMessageId("");
    imageRevisionArtifactRef.current = null;
    videoRevisionArtifactRef.current = null;
    briefReadyShownRef.current = false;
    lastEventIdRef.current = 0;
  };

  const applyConversation = async (detail: ConversationDetailResponse) => {
    primaryExecutionReadyRef.current = resolveWorkspacePrimaryExecutionReady({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    const resolvedMode = resolveWorkspaceOrchestrationMode({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    const resolvedAgentRuntimeMode = resolveWorkspaceAgentRuntimeMode({
      conversation: detail.conversation,
      messages: detail.messages,
    });
    setResolvedAgentRuntimeMode(resolvedAgentRuntimeMode);
    setResolvedOrchestrationMode(resolvedMode);
    const snapshot = (detail.conversation.context || {}) as Partial<WorkspaceSnapshot>;
    const pendingImageEditRequest =
      snapshot.pendingImageEditRequest || ((detail.conversation.context || {}) as Record<string, unknown>).pending_image_edit_request || null;
    const imageEditConfirmedSelections = snapshot.imageEditConfirmedSelections || {};
    const flowDraft = snapshot.flowDraft || null;
    const pendingMessageJob = snapshot.pendingMessageJob || snapshot.pending_message_job || null;
    const pendingIntakeJob = snapshot.pendingIntakeJob || snapshot.pending_intake_job || null;
    const pendingDirectionJob = snapshot.pendingDirectionJob || snapshot.pending_direction_job || null;
    const storedPendingPlanJob = snapshot.pendingPlanJob || snapshot.pending_plan_job || null;
    const recoveredPendingPlanJobCandidate = storedPendingPlanJob
      ? null
      : loadPendingPlanJobRecovery<PendingPlanJob>(
          browserSessionStorage(),
          detail.conversation.conversation_id,
        );
    const recoveredPendingPlanJob = isRecoverablePendingPlanJob(recoveredPendingPlanJobCandidate)
      ? recoveredPendingPlanJobCandidate
      : null;
    if (storedPendingPlanJob) {
      clearPendingPlanJobRecovery(browserSessionStorage(), detail.conversation.conversation_id);
    } else if (recoveredPendingPlanJobCandidate && !recoveredPendingPlanJob) {
      clearPendingPlanJobRecovery(browserSessionStorage(), detail.conversation.conversation_id);
    }
    const pendingPlanJob = storedPendingPlanJob || recoveredPendingPlanJob;
    const pendingImageJob = snapshot.pendingImageJob || snapshot.pending_image_job || null;
    const pendingImageRevision = snapshot.pendingImageRevision || snapshot.pending_image_revision || null;
    const pendingPptOutlineRevision = snapshot.pendingPptOutlineRevision || snapshot.pending_ppt_outline_revision || null;
    const pendingVideoRevision = snapshot.pendingVideoRevision || snapshot.pending_video_revision || null;
    const pendingScenePackageJob = snapshot.pendingScenePackageJob || snapshot.pending_scene_package_job || null;
    const pendingPptJob = snapshot.pendingPptJob || snapshot.pending_ppt_job || null;
    const pendingJianyingDraftJob = snapshot.pendingJianyingDraftJob || snapshot.pending_jianying_draft_job || null;
    const jianyingDraftRecords = snapshot.jianyingDraftRecords || snapshot.jianying_draft_records || {};
    const restoredMessages = detail.messages
      .map((message) => messageFromResponse(message, detail.conversation.conversation_id))
      .filter((m): m is ChatMessage => Boolean(m));
    const restoredMessagesWithImageEditSelections = applyImageEditConfirmedSelectionsToMessages(restoredMessages, imageEditConfirmedSelections);
    const contextMessages = restoreLatestVideoScenePackagesFromContext(
      restoredConversationMessages(undefined, restoredMessagesWithImageEditSelections),
      snapshot as Partial<Record<string, unknown>>,
    );
    const pptAwareMessages = markLatestPptFileDoneFromContext(contextMessages, snapshot as Partial<Record<string, unknown>>);
    const imageAwareMessages = markLatestImageResultAcceptedFromContext(pptAwareMessages, snapshot as Partial<Record<string, unknown>>);
    const videoAwareMessages = markLatestVideoResultAcceptedFromContext(imageAwareMessages, snapshot as Partial<Record<string, unknown>>);
    const normalizedMessages = normalizeRestoredMessageReferences(
      dedupeRestoredScenePackageMessages(restorePendingMessageJobMessage(videoAwareMessages, pendingMessageJob)),
    );
    const resumableScenePackageJob = pendingScenePackageJob
      && pendingScenePackageJob.conversation_id === detail.conversation.conversation_id
      && !hasMaterializedScenePackageJob(normalizedMessages, pendingScenePackageJob)
      ? pendingScenePackageJob
      : null;
    const reconciledMessages = markConfirmedSceneAssetModelOptions(
      reconcileStaleSceneAssetUiFlags(normalizedMessages, {
        hasActiveAssetJob: Boolean(
          resumableScenePackageJob
          && (
            resumableScenePackageJob.kind === "scene_asset_generation"
            || resumableScenePackageJob.kind === "scene_package_generation"
          ),
        ),
      }),
    );
    // 内存 reconcile 后写回消息，避免刷新前仍从 DB 读到假「参考图生成中」。
    if (reconciledMessages !== normalizedMessages) {
      for (const item of reconciledMessages) {
        const before = normalizedMessages.find((message) => message.id === item.id);
        if (!before || before.artifact === item.artifact) continue;
        if (item.artifact?.type !== "video_scene_packages" && item.artifact?.type !== "scene_asset_model_options") continue;
        void api.updateConversationMessage(detail.conversation.conversation_id, item.id, {
          content: item.content,
          payload: {
            artifact: item.artifact,
            materials: item.materials || item.artifact?.materials || [],
            client_message_id: item.id,
          } as unknown as Record<string, unknown>,
        }).catch(() => {});
      }
    }
    const pendingPlanJobMaterialized = Boolean(
      pendingPlanJob
      && hasMaterializedPlanJob(reconciledMessages, detail.conversation.conversation_id, pendingPlanJob),
    );
    if (pendingPlanJobMaterialized && pendingPlanJob) {
      clearPendingPlanJobRecovery(
        browserSessionStorage(),
        detail.conversation.conversation_id,
        pendingPlanJob.job_id,
      );
      clearPlanJobPersistenceState(pendingPlanJob);
    }
    const contextIntent = isCreationIntent((detail.conversation.context || {}).intent)
      ? (detail.conversation.context || {}).intent as CreationIntent
      : null;
    const storedWorkflowProgress = snapshot.workflowProgress || snapshot.workflow_progress || null;
    const restoredLastPhase = (() => {
      const phase = String(detail.conversation.last_phase || "");
      if (!/scene_package_job_resume_failed|scene_asset_job_resume_failed/.test(phase)) return phase;
      const latestPackages = [...reconciledMessages].reverse().find((message) => (
        message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages
      ))?.artifact;
      if (!latestPackages?.videoScenePackages) return phase;
      if (latestPackages.sceneAssetsAwaitingModel) return "scene_package_awaiting_image_model";
      if (scenePackageHasGeneratedImages(latestPackages.videoScenePackages)) return "scene_package_ready";
      return "scene_package_awaiting_image_model";
    })();
    const fallbackBoard = storedWorkflowProgress
      ? null
      : deriveWorkflowTaskBoard({
          lastPhase: restoredLastPhase || detail.conversation.last_phase,
          fallbackIntent: contextIntent,
          messages: reconciledMessages,
        });
    const restoredWorkflowProgress: WorkflowProgressSnapshot | null = (() => {
      const base = storedWorkflowProgress || (fallbackBoard
        ? {
            version: 1 as const,
            intent: fallbackBoard.intent,
            flow_kind: fallbackBoard.flowKind,
            source_message_id: "",
            last_phase: restoredLastPhase || detail.conversation.last_phase,
            scene_package_stage: null as string | null,
            updated_at: detail.conversation.updated_at || new Date().toISOString(),
          }
        : null);
      if (!base) return null;
      if (!restoredLastPhase || base.last_phase === restoredLastPhase) return base;
      return { ...base, last_phase: restoredLastPhase };
    })();
    applySnapshot({
      ...snapshot,
      pendingScenePackageJob: resumableScenePackageJob,
      pending_scene_package_job: resumableScenePackageJob,
      flowDraft,
      pendingMessageJob,
      pending_message_job: pendingMessageJob,
      pendingIntakeJob,
      pending_intake_job: pendingIntakeJob,
      pendingDirectionJob,
      pending_direction_job: pendingDirectionJob,
      pendingPlanJob: pendingPlanJobMaterialized ? null : pendingPlanJob,
      pending_plan_job: pendingPlanJobMaterialized ? null : pendingPlanJob,
      pendingPlanRevisionChoice:
        pendingPlanJob?.kind === "plan_revision" ? null : snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null,
      pending_plan_revision_choice:
        pendingPlanJob?.kind === "plan_revision" ? null : snapshot.pendingPlanRevisionChoice || snapshot.pending_plan_revision_choice || null,
      pendingImageEditRequest: pendingImageEditRequest as PendingImageEditRequest | null,
      pendingImageJob,
      pending_image_job: pendingImageJob,
      pendingImageRevision,
      pending_image_revision: pendingImageRevision,
      pendingPptOutlineRevision,
      pending_ppt_outline_revision: pendingPptOutlineRevision,
      pendingVideoRevision,
      pending_video_revision: pendingVideoRevision,
      pendingPptJob,
      pending_ppt_job: pendingPptJob,
      pendingJianyingDraftJob:
        pendingJianyingDraftJob?.conversation_id === detail.conversation.conversation_id ? pendingJianyingDraftJob : null,
      pending_jianying_draft_job:
        pendingJianyingDraftJob?.conversation_id === detail.conversation.conversation_id ? pendingJianyingDraftJob : null,
      jianyingDraftRecords,
      jianying_draft_records: jianyingDraftRecords,
      imageEditConfirmedSelections,
      workflowProgress: restoredWorkflowProgress,
      workflow_progress: restoredWorkflowProgress,
      messages: reconciledMessages,
    }, detail.conversation.conversation_id);
    if (!pendingPlanJobMaterialized && pendingPlanJob?.context.processedKey) {
      processedArtifactIdsRef.current.add(pendingPlanJob.context.processedKey);
    }
    if (resolvedMode !== "frontend_v2") return;
    if (recoveredPendingPlanJob && !pendingPlanJobMaterialized) {
      notifyPlanJobPersistenceRecovery(recoveredPendingPlanJob);
      schedulePendingPlanJobPersistence(
        recoveredPendingPlanJob,
        pendingPlanJobRunningPhase(recoveredPendingPlanJob.kind),
        pendingPlanJobPersistenceContext(recoveredPendingPlanJob.kind),
      );
    }
    if (pendingMessageJob?.job_id && pendingMessageJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingMessageJob(pendingMessageJob);
      }, 0);
    } else if (pendingIntakeJob?.job_id && pendingIntakeJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingIntakeJob(pendingIntakeJob);
      }, 0);
    }
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingDirectionJob(pendingDirectionJob);
      }, 0);
    } else if (
      flowDraft?.stage === "directions_ready" &&
      flowDraft.creativeDirections?.length &&
      isCreationIntent(flowDraft.intent) &&
      !hasPostDirectionArtifactForDirections(reconciledMessages, detail.conversation.conversation_id, flowDraft.creativeDirections) &&
      !hasDirectionsArtifactForDraft(reconciledMessages, detail.conversation.conversation_id, flowDraft)
    ) {
      pushDirectionsArtifact(flowDraft.creativeDirections, {
        intent: flowDraft.intent,
        formValues: flowDraft.formValues || (flowDraft.form ? valuesFromForm(flowDraft.form) : {}),
        materials: flowDraft.materials || [],
        coreMessage: flowDraft.coreMessage || "",
        intakeContext: flowDraft.intakeContext,
      }, detail.conversation.conversation_id);
    } else if (
      flowDraft?.stage === "form_pending" &&
      !hasPassedRequirementCollection(
        reconciledMessages,
        detail.conversation.conversation_id,
        snapshot as Partial<WorkspaceSnapshot> & Record<string, unknown>,
        detail.conversation.last_phase,
      )
    ) {
      window.setTimeout(() => {
        restoreFormDraft(flowDraft, detail.conversation.conversation_id);
      }, 0);
    }
    if (
      !pendingMessageJob &&
      pendingPlanJob?.job_id &&
      pendingPlanJob.conversation_id === detail.conversation.conversation_id &&
      !hasMaterializedPlanJob(reconciledMessages, detail.conversation.conversation_id, pendingPlanJob)
    ) {
      window.setTimeout(() => {
        void resumePendingPlanJob(pendingPlanJob);
      }, 0);
    } else if (pendingPlanJob?.job_id && pendingPlanJob.conversation_id === detail.conversation.conversation_id) {
      await clearPendingPlanJob(detail.conversation.conversation_id, "plan_review").catch(() => {});
    }
    if (pendingImageJob?.job_id && pendingImageJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingImageJob(pendingImageJob);
      }, 0);
    }
    if (
      pendingScenePackageJob?.job_id &&
      pendingScenePackageJob.conversation_id === detail.conversation.conversation_id &&
      !hasMaterializedScenePackageJob(reconciledMessages, pendingScenePackageJob)
    ) {
      window.setTimeout(() => {
        void resumePendingScenePackageJob(pendingScenePackageJob);
      }, 0);
    } else if (pendingScenePackageJob?.job_id && pendingScenePackageJob.conversation_id === detail.conversation.conversation_id) {
      const scenePackageMessage = reconciledMessages
        .slice()
        .reverse()
        .find((message) => message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages);
      await clearPendingScenePackageJob(
        detail.conversation.conversation_id,
        "scene_package_ready",
        scenePackageMessage?.artifact?.videoScenePackages
          ? scenePackageContext(
              scenePackageMessage.artifact,
              scenePackageMessage.artifact.videoScenePackages,
              scenePackageMessage.artifact.sceneAssetFailures || [],
            )
          : {},
      ).catch(() => {});
    }
    const pendingVideoJob = snapshot.pendingVideoJob || snapshot.pending_video_job || null;
    if (pendingVideoJob?.job_id && pendingVideoJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingVideoJob(pendingVideoJob);
      }, 0);
    }
    if (pendingPptJob?.job_id && pendingPptJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingPptJob(pendingPptJob);
      }, 0);
    }
    if (pendingJianyingDraftJob?.job_id && pendingJianyingDraftJob.conversation_id === detail.conversation.conversation_id) {
      window.setTimeout(() => {
        void resumePendingJianyingDraftJob(pendingJianyingDraftJob);
      }, 0);
    }
    const taskId = snapshot.taskId || detail.conversation.current_task_id || "";
    if (taskId) {
      setActiveTaskId(taskId);
      unsubRef.current = subscribeTaskEvents(taskId, onEvent, snapshot.lastEventId || undefined);
      await reconcileTaskFromServer(taskId);
    }
  };

  const resumeVisiblePendingJobs = () => {
    const activeConversationId = conversationIdRef.current;
    if (!activeConversationId || !pageVisibleRef.current || orchestrationModeRef.current !== "frontend_v2") return;
    const pendingMessageJob = pendingMessageJobRef.current;
    if (pendingMessageJob?.job_id && pendingMessageJob.conversation_id === activeConversationId) {
      void resumePendingMessageJob(pendingMessageJob);
      return;
    }
    const pendingIntakeJob = pendingIntakeJobRef.current;
    if (pendingIntakeJob?.job_id && pendingIntakeJob.conversation_id === activeConversationId) {
      void resumePendingIntakeJob(pendingIntakeJob);
    }
    const pendingDirectionJob = pendingDirectionJobRef.current;
    if (pendingDirectionJob?.job_id && pendingDirectionJob.conversation_id === activeConversationId) {
      void resumePendingDirectionJob(pendingDirectionJob);
    }
    const pendingPlanJob = pendingPlanJobRef.current;
    if (pendingPlanJob?.job_id && pendingPlanJob.conversation_id === activeConversationId) {
      const recoveryHandle = loadPendingPlanJobRecovery<PendingPlanJob>(
        browserSessionStorage(),
        activeConversationId,
      );
      if (recoveryHandle?.job_id === pendingPlanJob.job_id) {
        schedulePendingPlanJobPersistence(
          pendingPlanJob,
          pendingPlanJobRunningPhase(pendingPlanJob.kind),
          pendingPlanJobPersistenceContext(pendingPlanJob.kind),
        );
      }
      void resumePendingPlanJob(pendingPlanJob);
    }
    const pendingImageJob = pendingImageJobRef.current;
    if (pendingImageJob?.job_id && pendingImageJob.conversation_id === activeConversationId) {
      void resumePendingImageJob(pendingImageJob);
    }
    const pendingScenePackageJob = pendingScenePackageJobRef.current;
    if (pendingScenePackageJob?.job_id && pendingScenePackageJob.conversation_id === activeConversationId) {
      void resumePendingScenePackageJob(pendingScenePackageJob);
    }
    const pendingVideoJob = pendingVideoJobRef.current;
    if (pendingVideoJob?.job_id && pendingVideoJob.conversation_id === activeConversationId) {
      void resumePendingVideoJob(pendingVideoJob);
    }
    const pendingPptJob = pendingPptJobRef.current;
    if (pendingPptJob?.job_id && pendingPptJob.conversation_id === activeConversationId) {
      void resumePendingPptJob(pendingPptJob);
    }
    const pendingJianyingDraftJob = pendingJianyingDraftJobRef.current;
    if (pendingJianyingDraftJob?.job_id && pendingJianyingDraftJob.conversation_id === activeConversationId) {
      void resumePendingJianyingDraftJob(pendingJianyingDraftJob);
    }
  };

  // 对话恢复是“先写 Ref、再提交 React 状态”。素材修订 job 额外在提交完成后接力一次，
  // 避免切换对话或刷新时，首个 setTimeout 早于可见对话状态而永久停止轮询。
  // V2.1：仅 frontend_v2 历史会话续跑本地 pending Job；video_agent_v2 以 Operation/Snapshot 为准。
  useEffect(() => {
    if (!orchestrationResolved || !currentConversationId || !pageVisibleRef.current) return;
    if (orchestrationModeRef.current !== "frontend_v2") return;
    const pendingScenePackageJob = pendingScenePackageJobRef.current;
    if (
      !pendingScenePackageJob?.job_id
      || pendingScenePackageJob.conversation_id !== currentConversationId
      || hasMaterializedScenePackageJob(messagesRef.current, pendingScenePackageJob)
    ) {
      return;
    }
    void resumePendingScenePackageJob(pendingScenePackageJob);
  }, [currentConversationId, orchestrationResolved, pendingScenePackageResumeVersion]);

  // 刷新或压缩完成后，服务端 Turn 可能已经进入 processing，但旧版页面内存里的
  // pendingSupervisorTurns 已丢失。这里按服务端队列和已保存用户消息重建接力 DTO，
  // 类似从数据库恢复一条待执行的 Service Command，绝不重新注册或重复计费。
  useEffect(() => {
    if (
      orchestrationModeRef.current === "video_agent_v2"
      && !primaryExecutionReadyRef.current
    ) return;
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (
      !currentConversationId
      || !runtimeAttached
      || supervisorRuntime.state.connection.status !== "connected"
    ) return;
    const localIds = new Set(
      pendingSupervisorTurns
        .filter((item) => item.conversationId === currentConversationId)
        .map((item) => item.clientInputId),
    );
    const recoverableInput = supervisorRuntime.state.inputQueue.find(
      (item) => ["accepted", "processing", "queued"].includes(item.status)
        && !localIds.has(item.clientInputId),
    );
    if (!recoverableInput) return;
    const sourceMessage = messagesRef.current.find(
      (message) => message.id === recoverableInput.clientInputId
        && messageConversationId(message, currentConversationId) === currentConversationId
        && message.role === "user",
    );
    if (!sourceMessage) return;

    const lastPhase = workflowProgressConversationIdRef.current === currentConversationId
      ? workflowProgressRef.current?.last_phase || ""
      : "";
    if (!imageRevisionArtifactRef.current) {
      const imageArtifact = latestImageResultArtifactForConversation(messagesRef.current, currentConversationId);
      const looksLikeRevision = /修改|重新生成|背景|轮廓光|亮明|保持不变/u.test(sourceMessage.content);
      if (imageArtifact && (lastPhase === "image_regeneration_running" || looksLikeRevision)) {
        imageRevisionArtifactRef.current = {
          conversationId: currentConversationId,
          artifact: imageArtifact,
        };
      }
    }
    if (!pptOutlineRevisionArtifactRef.current && latestPptRevisionRequestedForConversation(messagesRef.current, currentConversationId, sourceMessage.content)) {
      const outlineMessage = [...messagesRef.current]
        .reverse()
        .find((message) => messageConversationId(message, currentConversationId) === currentConversationId && message.artifact?.pptSummary);
      if (outlineMessage?.artifact) {
        pptOutlineRevisionArtifactRef.current = {
          conversationId: currentConversationId,
          artifact: outlineMessage.artifact,
        };
      }
    }

    const recoveredTurn: PendingSupervisorTurn = {
      conversationId: currentConversationId,
      clientInputId: recoverableInput.clientInputId,
      content: sourceMessage.content,
      materials: sourceMessage.materials || [],
      replyToMessageId: null,
      artifactRefs: [],
      interruptId: null,
      explicitAction: null,
      continueLegacy: false,
      registrationStatus: "registered",
      runId: recoverableInput.turnId || undefined,
    };
    void persistPendingSupervisorTurns(
      (current) => current.some((item) => item.clientInputId === recoveredTurn.clientInputId)
        ? current
        : [recoveredTurn, ...current],
      currentConversationId,
    ).catch(() => {});
  }, [
    currentConversationId,
    agentRuntimeMode,
    pendingSupervisorTurns,
    orchestrationMode,
    orchestrationResolved,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
  ]);

  // M13.2 早期候选可能已经把 Turn 保存为 accepted，却没有真实 Graph Handler。
  // 这里按会话写一次稳定提示和版本 marker，再一次性清空全部本地 pending；
  // 即使进程在两次写入之间退出，刷新也只补齐 marker，不会重复新增消息。
  useEffect(() => {
    if (
      !orchestrationResolved
      || !currentConversationId
      || !primaryExecutionUnavailable
    ) {
      return;
    }
    const targetConversationId = currentConversationId;
    const noticeId = unavailableSupervisorNoticeId(targetConversationId);
    const noticePersisted = messagesRef.current.some(
      (message) => (
        message.id === noticeId
        || message.content === UNAVAILABLE_SUPERVISOR_NOTICE
      )
        && messageConversationId(message, targetConversationId) === targetConversationId,
    );
    const pendingCount = pendingSupervisorTurns.filter(
      (item) => item.conversationId === targetConversationId,
    ).length;
    const hasActiveInput = supervisorRuntime.state.inputQueue.some(
      (item) => ["sending", "queued", "processing", "accepted"].includes(item.status),
    );
    const recoveryAction = resolveUnavailableSupervisorRecovery({
      orchestrationMode,
      primaryExecutionReady: primaryExecutionReadyRef.current,
      connectionStatus: supervisorRuntime.state.connection.status,
      pendingCount,
      hasActiveInput,
      markerVersion:
        unavailableSupervisorNoticeVersionsRef.current.get(targetConversationId) ?? 0,
      noticePersisted,
    });
    if (
      recoveryAction === "none"
      || unavailableSupervisorRecoveryInFlightRef.current.has(targetConversationId)
    ) {
      return;
    }
    unavailableSupervisorRecoveryInFlightRef.current.add(targetConversationId);
    const convergeUnavailableSupervisor = async () => {
      try {
        if (recoveryAction === "persist_notice") {
          await appendPersistedSupervisorNotice(
            UNAVAILABLE_SUPERVISOR_NOTICE,
            targetConversationId,
            noticeId,
          );
        }
        unavailableSupervisorNoticeVersionsRef.current.set(
          targetConversationId,
          UNAVAILABLE_SUPERVISOR_NOTICE_VERSION,
        );
        await persistPendingSupervisorTurns(
          () => [],
          targetConversationId,
        );
      } catch {
        unavailableSupervisorNoticeVersionsRef.current.delete(
          targetConversationId,
        );
      } finally {
        unavailableSupervisorRecoveryInFlightRef.current.delete(
          targetConversationId,
        );
      }
    };
    void convergeUnavailableSupervisor();
  }, [
    currentConversationId,
    orchestrationMode,
    orchestrationResolved,
    pendingSupervisorTurns,
    primaryExecutionUnavailable,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
  ]);

  useEffect(() => {
    const handleVisibilityResume = () => {
      pageVisibleRef.current = typeof document === "undefined" || document.visibilityState !== "hidden";
      if (pageVisibleRef.current) resumeVisiblePendingJobs();
    };
    const handleFocusResume = () => {
      pageVisibleRef.current = true;
      resumeVisiblePendingJobs();
    };
    document.addEventListener("visibilitychange", handleVisibilityResume);
    window.addEventListener("focus", handleFocusResume);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityResume);
      window.removeEventListener("focus", handleFocusResume);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const restoreToken = `${conversationId || ""}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
    restoreTokenRef.current = restoreToken;
    const isStale = () => cancelled || restoreTokenRef.current !== restoreToken;

    const restoreConversation = async () => {
      restoringRef.current = true;
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      if (!conversationId) {
        resetWorkspace();
        restoringRef.current = false;
        setBusy(false);
        return;
      }
      if (skipRouteRestoreConversationRef.current === conversationId) {
        skipRouteRestoreConversationRef.current = "";
        restoringRef.current = false;
        setBusy(false);
        return;
      }
      setDialogOpen(false);
      setPendingCore("");
      setPendingFormValues({});
      setPendingMaterials([]);
      setSelectedStoryboardMessageId("");
      setSelectedPlanEditorMessageId("");
      pendingDialogContextRef.current = null;
      unsubRef.current();
      seenEventIdsRef.current = new Set();
      announcedPhasesRef.current = new Set();
      briefReadyShownRef.current = false;
      lastEventIdRef.current = 0;
      // 挂上目标会话；仅换会话时清空气泡，避免同 id StrictMode/HMR 重挂抹掉未落库的场景包投影卡。
      const previousConversationId = conversationIdRef.current;
      setActiveConversationId(conversationId);
      if (previousConversationId !== conversationId) {
        setMessages([]);
        messagesRef.current = [];
      }
      orchestrationModeRef.current = null;
      agentRuntimeModeRef.current = null;
      primaryExecutionReadyRef.current = false;
      setOrchestrationResolved(false);
      setBusy(true);
      try {
        const detail = await api.resumeConversation(conversationId);
        if (isStale()) return;
        await applyConversation(detail);
        if (isStale()) return;
        // video_agent_v2：resume 落消息后稍候刷新 Snapshot，并强制重投场景包卡（revision 可能未变）。
        if (orchestrationModeRef.current === "video_agent_v2") {
          setWorkspaceScenePackageReprojectEpoch((value) => value + 1);
          window.setTimeout(() => {
            if (isStale()) return;
            void supervisorRuntime.refreshSnapshot()
              .then(() => {
                if (isStale()) return;
                setWorkspaceScenePackageReprojectEpoch((value) => value + 1);
              })
              .catch(() => {});
          }, 50);
        }
      } catch (err) {
        if (isStale()) return;
        const message = err instanceof Error ? err.message : String(err);
        const missing = /not found|404|Conversation not found/i.test(message);
        if (missing) {
          // 失效会话离开死链；其它错误留在当前路由并提示，避免「点了历史却空白回首页」。
          resetWorkspace();
          navigate("/", { replace: true });
        } else {
          setMessages([]);
          messagesRef.current = [];
          pushAssistant(`历史对话恢复失败:${message}`, conversationId);
        }
      } finally {
        if (restoreTokenRef.current === restoreToken) {
          restoringRef.current = false;
          setBusy(false);
          if (!cancelled && conversationId) {
            const deferredInputs = deferredOwnershipInputsRef.current.filter(
              (item) => item.routeConversationId === conversationId,
            );
            deferredOwnershipInputsRef.current = deferredOwnershipInputsRef.current.filter(
              (item) => item.routeConversationId !== conversationId,
            );
            for (const item of deferredInputs) {
              window.setTimeout(() => void handleSend(item.input), 0);
            }
          }
        }
      }
    };
    void restoreConversation();
    return () => {
      cancelled = true;
      // 禁止在 cleanup 里 setActiveConversationId("")：StrictMode 与快速切会话会清空回显。
    };
  }, [conversationId, navigate]);

  useEffect(() => {
    const startFreshConversation = () => {
      skipRouteRestoreConversationRef.current = "";
      deferredOwnershipInputsRef.current = [];
      restoringRef.current = false;
      setBusy(false);
      resetWorkspace();
      if (routeConversationIdRef.current) {
        navigate("/", { replace: true });
      }
    };
    window.addEventListener("pixelflow-new-conversation", startFreshConversation);
    return () => {
      window.removeEventListener("pixelflow-new-conversation", startFreshConversation);
    };
  }, [navigate]);

  useEffect(() => {
    if (restoringRef.current || !currentConversationId || orchestrationModeRef.current !== "frontend_v2") return;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      const snapshot = makeSnapshot(currentConversationId);
      void updateConversationWithProgress(currentConversationId, {
          current_task_id: currentTaskId || null,
          last_phase: workflowProgressRef.current?.last_phase || String(canvas.phase || "idle"),
          context: snapshot as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }, 400);
  }, [messages, pendingMaterials, pendingPlanRevisionChoice, canvas, canvasOpen, briefConfirmed, currentTaskId, currentConversationId]);

  const titleFromPrompt = (text: string) => {
    const normalized = text.trim() || "带附件对话";
    return normalized.length > 18 ? `${normalized.slice(0, 18)}...` : normalized;
  };

  const ensureConversation = async (title: string): Promise<ConversationOwnership> => {
    if (conversationIdRef.current) {
      return {
        conversationId: conversationIdRef.current,
        orchestrationMode: orchestrationModeRef.current ?? "frontend_v2",
        agentRuntimeMode: agentRuntimeModeRef.current ?? "off",
      };
    }
    const created = await api.createConversation({
      title: titleFromPrompt(title),
      last_phase: String(canvas.phase || "idle"),
      current_task_id: currentTaskId || null,
      context: makeSnapshot() as unknown as Record<string, unknown>,
    });
    const createdMode = resolveWorkspaceOrchestrationMode(created);
    const createdAgentRuntimeMode = resolveWorkspaceAgentRuntimeMode(created);
    primaryExecutionReadyRef.current = resolveWorkspacePrimaryExecutionReady(created);
    skipRouteRestoreConversationRef.current = created.conversation_id;
    setResolvedAgentRuntimeMode(createdAgentRuntimeMode);
    setResolvedOrchestrationMode(createdMode);
    setActiveConversationId(created.conversation_id);
    window.dispatchEvent(new Event("pixelflow-conversations-updated"));
    notifyContentAppConversationsUpdated(created.conversation_id);
    return {
      conversationId: created.conversation_id,
      orchestrationMode: createdMode,
      agentRuntimeMode: createdAgentRuntimeMode,
    };
  };

  const normalizeSendInput = (input: string | AgentUserMessagePayload): AgentUserMessagePayload => {
    if (typeof input === "string") return { content: input, materials: [] };
    return {
      content: input.content,
      materials: Array.isArray(input.materials) ? input.materials : [],
      reply_to_message_id: input.reply_to_message_id ?? null,
      artifact_refs: Array.isArray(input.artifact_refs) ? input.artifact_refs : [],
      interrupt_id: input.interrupt_id ?? null,
    };
  };

  const appendSupervisorNotice = (content: string, targetConversationId: string) => {
    const notice: ChatMessage = {
      id: uid(),
      conversationId: targetConversationId,
      role: "assistant",
      content,
      time: now(),
    };
    setMessages((items) => {
      const nextItems = appendVisibleConversationMessage(items, {
        activeConversationId: conversationIdRef.current,
        targetConversationId,
        message: notice,
      });
      messagesRef.current = nextItems;
      return nextItems;
    });
  };

  const appendPersistedSupervisorNotice = async (
    content: string,
    targetConversationId: string,
    messageId: string,
  ) => {
    if (messagesRef.current.some(
      (message) => message.id === messageId
        && messageConversationId(message, targetConversationId) === targetConversationId,
    )) {
      return;
    }
    const optimisticMessage = appendOptimisticMessageForConversation({
      id: messageId,
      conversationId: targetConversationId,
      role: "assistant",
      content,
      time: "",
    }, targetConversationId);
    try {
      const savedMessage = await persistChatMessage(
        targetConversationId,
        optimisticMessage,
      );
      replaceOptimisticMessage(
        optimisticMessage.id,
        savedMessage,
        targetConversationId,
      );
    } catch (error) {
      removeOptimisticMessage(optimisticMessage.id, targetConversationId);
      throw error;
    }
    await supervisorRuntime.refreshSnapshot().catch(() => {});
  };

  // 思考流 answer channel：完成后写入对话框气泡（与 Thought 折叠区分开）。
  // waiting_for_input：追问话术只留执行方案卡，不写重复气泡。
  // video_agent_v2：回答由 AgentTurnGroup 承载，禁止再落 thinking-answer 气泡。
  useEffect(() => {
    if (orchestrationModeRef.current === "video_agent_v2") return;
    const thinking = supervisorRuntime.state.agentThinking;
    const conversationId = currentConversationId;
    const plan = supervisorRuntime.state.videoAgentPlan;
    if (!thinking || thinking.status !== "completed" || !conversationId) return;
    if (plan?.status === "waiting_for_input") return;
    const answer = (thinking.answer || "").trim();
    if (!answer) return;
    const messageId = `thinking-answer:${thinking.turnId}`;
    if (thinkingAnswerNoticeInFlightRef.current.has(messageId)) return;
    if (messagesRef.current.some((message) => message.id === messageId)) return;
    thinkingAnswerNoticeInFlightRef.current.add(messageId);
    void appendPersistedSupervisorNotice(answer, conversationId, messageId)
      .catch(() => undefined)
      .finally(() => {
        thinkingAnswerNoticeInFlightRef.current.delete(messageId);
      });
  }, [
    supervisorRuntime.state.agentThinking,
    supervisorRuntime.state.videoAgentPlan?.status,
    currentConversationId,
  ]);

  const handleSupervisorTurn = async (
    pendingTurn: PendingSupervisorTurn,
    contextVersion: number,
  ): Promise<RegisteredSupervisorTurn | null> => {
    const targetConversationId = pendingTurn.conversationId;
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (!targetConversationId || !runtimeAttached) return null;
    try {
      // 每次写请求前重新读取 CAS 版本，避免上一轮 Turn 或事件消费后继续使用旧版本。
      await supervisorRuntime.refreshSnapshot();
      const expectedContextVersion = supervisorRuntime.getContextVersion() ?? contextVersion;
      const submission = buildSupervisorSubmission({
        conversationId: targetConversationId,
        clientInputId: pendingTurn.clientInputId,
        content: pendingTurn.content,
        materials: pendingTurn.materials as JsonObject[],
        replyToMessageId: pendingTurn.replyToMessageId,
        artifactRefs: pendingTurn.artifactRefs,
        interruptId: orchestrationModeRef.current === "video_agent_v2"
          ? pendingTurn.interruptId
          : null,
        explicitAction: pendingTurn.explicitAction,
      }, expectedContextVersion);
      if (submission.kind === "interrupt") {
        if (orchestrationModeRef.current !== "video_agent_v2") return null;
        await supervisorRuntime.respondToInterrupt(submission.interruptId, submission.request);
        // interrupt 成功后先原子移除恢复上下文；若写回失败，保留相同
        // client_response_id 供幂等重试，不创建额外 Turn。
        await persistPendingSupervisorTurns(
          (current) => current.filter(
            (item) => item.clientInputId !== pendingTurn.clientInputId,
          ),
          targetConversationId,
        );
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        return null;
      }
      const request: TurnStartRequest = submission.request;
      let startedRaw: JsonValue;
      try {
        startedRaw = await supervisorRuntime.startTurn(request);
      } catch (error) {
        // 新建会话：PUT 标题与 turns/start 并发升级易 409；刷新后重试一次。
        const status = error instanceof SupervisorApiError ? error.status : 0;
        if (status !== 409) throw error;
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        startedRaw = await supervisorRuntime.startTurn(request);
      }
      const started = parseRegisteredSupervisorTurn(startedRaw);
      setResolvedOrchestrationMode(started.orchestrationMode);
      primaryExecutionReadyRef.current = started.orchestrationMode === "video_agent_v2";
      // run_id 与 VideoAgent turn_id 相同；对齐乐观思考卡，避免 SSE turn 对不上。
      // text 保持空串：等 LLM 思考流 delta，不注入固定占位句。
      if (started.orchestrationMode === "video_agent_v2" && started.runId) {
        const anchorUserId = pendingTurn.clientInputId;
        thinkingTurnAnchorsRef.current[started.runId] = anchorUserId;
        thinkingTurnAnchorsRef.current[anchorUserId] = anchorUserId;
        setOptimisticAgentThinking((current) => (
          current
            ? {
              ...current,
              turnId: started.runId,
            }
            : {
              turnId: started.runId,
              title: "思考中",
              subtitle: "",
              text: "",
              answer: "",
              startedAt: new Date().toISOString(),
              status: "streaming",
            }
        ));
      }
      if (started.routeIntent === "unknown") {
        await appendPersistedSupervisorNotice(
          "我还不能确定你要创建视频、图片、PPT，还是分析参考视频。请补充说明目标。",
          targetConversationId,
          `agent-route-clarification:${targetConversationId}:${pendingTurn.clientInputId}:v1`,
        );
      }
      // Turn 已接受后刷新一次，让紧随其后的排队输入看到服务端最新版本。
      try {
        await supervisorRuntime.refreshSnapshot();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return null;
      }
      return started;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return null;
      appendSupervisorNotice("会话 Agent 请求失败，请稍后重试。", targetConversationId);
      return null;
    }
  };

  async function submitSupervisorAction(
    content: string,
    explicitAction: ExplicitActionSignal,
    options: SubmitSupervisorActionOptions,
  ): Promise<void> {
    // V2.1 批次 D：V2 禁止再写入 workflow action；frontend_v2 本就不走此入口。
    if (orchestrationModeRef.current === "video_agent_v2") return;
    const clientInputId = crypto.randomUUID();
    const targetConversationId = currentConversationId;
    const interruptId = supervisorRuntime.state.interrupt?.interruptId ?? null;
    if (
      !targetConversationId
      || !interruptId
      || !content.trim()
    ) return;
    const pendingTurn: PendingSupervisorTurn = {
      conversationId: targetConversationId,
      clientInputId,
      content,
      materials: options.materials || [],
      replyToMessageId: options.replyToMessageId ?? null,
      artifactRefs: options.artifactRefs
        ?? (explicitAction.artifact_ref ? [explicitAction.artifact_ref] : []),
      interruptId,
      explicitAction,
      continueLegacy: false,
      registrationStatus: "pending",
    };
    await persistPendingSupervisorTurns(
      (current) => current.some((item) => item.clientInputId === clientInputId)
        ? current
        : [...current, pendingTurn],
      targetConversationId,
    );
    ensurePendingSupervisorTurnVisible(pendingTurn);
  }

  useEffect(() => {
    // 恢复时优先处理尚未提交到 Runtime 的输入；已注册 Turn 则优先选择
    // Snapshot 中仍存在的 input，避免已失效的旧 registered Turn 一直 wait，
    // 阻塞后续输入。
    const candidateTurns = pendingSupervisorTurns
      .filter(
        (item) => item.conversationId === currentConversationId
          && !supervisorTurnInFlightRef.current.has(item.clientInputId)
          && !supervisorLegacyHandoffClaimedRef.current.has(item.clientInputId),
      );
    const pendingTurn = candidateTurns.find((item) => item.registrationStatus === "pending")
      || candidateTurns.find((item) => supervisorRuntime.state.inputQueue.some(
        (serverItem) => serverItem.clientInputId === item.clientInputId,
      ))
      || candidateTurns[0];
    const runtimeAttached = orchestrationModeRef.current === "video_agent_v2"
      || agentRuntimeModeRef.current === "assist"
      || agentRuntimeModeRef.current === "shadow"
      || agentRuntimeModeRef.current === "primary";
    if (!pendingTurn || !runtimeAttached) return;
    if (supervisorRuntime.state.connection.status === "fatal") {
      appendSupervisorNotice("会话 Agent 状态恢复失败，请刷新后重试。", pendingTurn.conversationId);
      return;
    }
    const serverInput = supervisorRuntime.state.inputQueue.find(
      (item) => item.clientInputId === pendingTurn.clientInputId,
    );
    // Snapshot 的 contextVersion 只作为 CAS 优化值；恢复阶段它可能尚未从
    // 外部 Store 反映到本次 render。真正注册前 handleSupervisorTurn 会再读
    // 一次最新 Snapshot，因此不能因为这里暂时为 null 而把已落库 Turn 卡死。
    if (supervisorRuntime.state.connection.status !== "connected") return;
    if (serverInput) ensurePendingSupervisorTurnVisible(pendingTurn);
    if (
      pendingTurn.explicitAction
      && pendingTurn.registrationStatus === "registered"
      && pendingTurn.runId
    ) {
      supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
      void supervisorRuntime.getRunStatus(pendingTurn.runId)
        .then(async (value) => {
          if (!value || typeof value !== "object" || Array.isArray(value)) return;
          const status = value.status;
          if (status !== "waiting_user" && status !== "completed" && status !== "failed") return;
          await persistPendingSupervisorTurns(
            (current) => current.filter(
              (item) => item.clientInputId !== pendingTurn.clientInputId,
            ),
            pendingTurn.conversationId,
          );
          await supervisorRuntime.refreshSnapshot().catch(() => {});
        })
        .catch(() => {})
        .finally(() => {
          supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
        });
      return;
    }
    const handoffAction = resolveAssistHandoffAction({
      orchestrationMode: orchestrationModeRef.current ?? "frontend_v2",
      primaryExecutionReady: primaryExecutionReadyRef.current,
      registrationStatus: pendingTurn.registrationStatus,
      serverInputStatus: serverInput?.status,
      serverRunStatus: supervisorRuntime.state.run.status,
      continueLegacy: pendingTurn.continueLegacy,
      legacyBusy,
      dialogOpen,
      pendingPlanRevision: Boolean(pendingPlanRevisionChoice),
    });
    if (handoffAction !== "register") {
      if (handoffAction === "wait") return;
      // 历史未接线会话由上面的会话级恢复 effect 统一收敛，避免每条 pending
      // 各写一条随机提示或在清空后因孤儿 inputQueue 再次重建。
      if (handoffAction === "unavailable") return;
      if (handoffAction === "failed") {
        supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
        void appendPersistedSupervisorNotice(
          "会话 Agent 未能处理已保存输入，请稍后重试。",
          pendingTurn.conversationId,
          failedSupervisorNoticeId(
            pendingTurn.conversationId,
            pendingTurn.clientInputId,
          ),
        )
          .then(() => persistPendingSupervisorTurns(
            (current) => current.filter(
              (item) => item.clientInputId !== pendingTurn.clientInputId,
            ),
            pendingTurn.conversationId,
          ))
          .catch(() => {})
          .finally(() => {
            supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
          });
        return;
      }
      // acknowledge / 历史 continue_legacy：V2 不再踢回旧采集，只清掉 pending。
      supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
      void persistPendingSupervisorTurns(
        (current) => current.filter(
          (item) => item.clientInputId !== pendingTurn.clientInputId,
        ),
        pendingTurn.conversationId,
      )
        .then(() => supervisorRuntime.refreshSnapshot().catch(() => {}))
        .finally(() => {
          supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
          supervisorLegacyHandoffClaimedRef.current.delete(pendingTurn.clientInputId);
        });
      return;
    }
    supervisorTurnInFlightRef.current.add(pendingTurn.clientInputId);
    void handleSupervisorTurn(pendingTurn, supervisorRuntime.getContextVersion() ?? 0)
      .then(async (registered) => {
        if (!registered) return;
        const registeredTurn: PendingSupervisorTurn = {
          ...pendingTurn,
          continueLegacy: false,
          registrationStatus: "registered",
          runId: registered.runId,
        };
        ensurePendingSupervisorTurnVisible(registeredTurn);
        await persistPendingSupervisorTurns(
          (current) => current.map(
            (item) => item.clientInputId === pendingTurn.clientInputId
              ? registeredTurn
              : item,
          ),
          pendingTurn.conversationId,
        );
      })
      .finally(() => {
        supervisorTurnInFlightRef.current.delete(pendingTurn.clientInputId);
      });
  }, [
    currentConversationId,
    agentRuntimeMode,
    dialogOpen,
    legacyBusy,
    orchestrationMode,
    orchestrationResolved,
    pendingPlanRevisionChoice,
    pendingSupervisorTurns,
    supervisorRuntime.contextVersion,
    supervisorRuntime.getContextVersion,
    supervisorRuntime.state.connection.status,
    supervisorRuntime.state.inputQueue,
    supervisorRuntime.state.run.status,
  ]);

  const shouldUseRecoverableIntakeEntry = (
    text: string,
    materials: Array<Record<string, unknown>>,
    activeConversation: string,
  ): boolean => {
    if (sceneGlobalAssetReferenceFromMaterials(materials)) return false;
    if (pendingImageEditRequestRef.current?.conversationId === activeConversation && !looksLikeImageEditPrompt(text)) return false;
    if (pptOutlineRevisionArtifactRef.current?.conversationId === activeConversation && pptOutlineRevisionArtifactRef.current.artifact?.pptSummary) return false;
    if (planRevisionArtifactRef.current?.conversationId === activeConversation && planRevisionArtifactRef.current.artifact.intent && planRevisionArtifactRef.current.artifact.formValues) return false;
    if (imageRevisionArtifactRef.current?.conversationId === activeConversation && imageRevisionArtifactRef.current.artifact?.imageResult) return false;
    const pendingVideoRevisionArtifact = videoRevisionArtifactRef.current?.artifact;
    if (
      videoRevisionArtifactRef.current?.conversationId === activeConversation &&
      pendingVideoRevisionArtifact?.mergedVideo &&
      pendingVideoRevisionArtifact?.generatedSceneVideos &&
      pendingVideoRevisionArtifact?.videoScenePackages
    ) {
      return false;
    }
    return true;
  };

  const handleSend = async (
    input: string | AgentUserMessagePayload,
    runtimeOptions: SendRuntimeOptions = {},
  ) => {
    if (restoringRef.current) {
      deferredOwnershipInputsRef.current.push({
        routeConversationId: routeConversationIdRef.current,
        input,
      });
      return;
    }
    const {
      content: text,
      materials = [],
      reply_to_message_id: replyToMessageId = null,
      artifact_refs: artifactRefs = [],
      interrupt_id: interruptId = null,
    } = normalizeSendInput(input);
    let activeConversation = conversationIdRef.current;
    const message: ChatMessage = {
      id: runtimeOptions.clientInputId ?? uid(),
      conversationId: activeConversation || undefined,
      role: "user",
      content: text,
      materials,
      time: "",
    };
    lastPlanAnchorUserMessageIdRef.current = message.id;
    try {
      const ownership = await ensureConversation(text);
      activeConversation = ownership.conversationId;
      // 资产包待确认：允许自然语言重做/修改后重新渲染；也可确认生成成片。
      if (
        ownership.orchestrationMode === "video_agent_v2"
        && !runtimeOptions.skipRuntimeRegistration
      ) {
        const creativeConfirmation = supervisorRuntime.state.videoAgentConfirmation;
        if (
          creativeConfirmation
          && isScriptCreativeConfirmationTitle(creativeConfirmation.title)
        ) {
          if (isAgreeScriptCreativeRequest(text)) {
            if (creativeConfirmNeedsClarification(creativeConfirmation.costSummary)) {
              await appendMessageForConversation(
                { ...message, conversationId: activeConversation },
                activeConversation,
              );
              setReferencedMaterials([]);
              if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
              pushAssistant(
                "创意方向可以，但还需要你确认视频画幅（如 9:16 / 16:9 / 1:1）和结尾引导行动（如进直播间、小黄车下单）。请直接回复这两项后再点「同意创意继续」。",
                activeConversation,
              );
              return;
            }
            await appendMessageForConversation(
              { ...message, conversationId: activeConversation },
              activeConversation,
            );
            setReferencedMaterials([]);
            if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
            try {
              await supervisorRuntime.respondToVideoAgentConfirmation(
                creativeConfirmation.confirmationId,
                {
                  step_id: creativeConfirmation.stepId,
                  decision: "confirm",
                },
              );
              pushAssistant("已确认选题创意，继续完善结构与角色设定…", activeConversation);
            } catch {
              pushAssistant("创意确认未完成，请点确认卡「同意创意继续」，或刷新后重试。", activeConversation);
            }
            return;
          }

          if (isCancelScriptCreativeRequest(text)) {
            await appendMessageForConversation(
              { ...message, conversationId: activeConversation },
              activeConversation,
            );
            setReferencedMaterials([]);
            if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
            try {
              await supervisorRuntime.respondToVideoAgentConfirmation(
                creativeConfirmation.confirmationId,
                {
                  step_id: creativeConfirmation.stepId,
                  decision: "cancel",
                },
              );
            } catch {
              // 取消失败时仍提示用户换方向，避免卡死。
            }
            pushAssistant(
              "已取消当前创意方向。请直接用自然语言说明想怎么改，我会重新从选题开始。",
              activeConversation,
            );
            return;
          }

          // 其他自然语言 = 改创意：先取消闸门，再走新 Turn 重跑 /start。
          try {
            await supervisorRuntime.respondToVideoAgentConfirmation(
              creativeConfirmation.confirmationId,
              {
                step_id: creativeConfirmation.stepId,
                decision: "cancel",
              },
            );
          } catch {
            // 继续开新 Turn，避免旧确认卡挡住改创意。
          }
          characterSupplementNoticeRef.current =
            "已按你的新想法重新从选题开始，确认创意后再继续后面的步骤…";
          creativeRevisePendingRef.current = true;
        }
        // 结构就绪且尚无参考图：用户回复「没有参考图」→ 弹出选模型卡（确认闸门），不空转 Turn。
        if (
          ownership.orchestrationMode === "video_agent_v2"
          && isNoRefImageContinueRequest(text)
        ) {
          const workspace = videoAgentView.workspace;
          const packages = Array.isArray(workspace?.scenePackages) ? workspace.scenePackages : [];
          const imageCount = countGlobalAssetImageUrls(workspace?.globalAssets);
          const hasUnconfirmedModelCard = messagesRef.current.some((item) => (
            messageConversationId(item, activeConversation) === activeConversation
            && item.artifact?.type === "scene_asset_model_options"
            && !item.artifact.sceneAssetModelConfirmed
          ));
          if (packages.length > 0 && imageCount === 0 && !hasUnconfirmedModelCard) {
            // ≤2 镜且尚无参考图：可能是旧抽取误拆，交给服务端 Turn 按脚本重拆。
            if (packages.length >= 4) {
              await appendMessageForConversation(
                { ...message, conversationId: activeConversation },
                activeConversation,
              );
              setReferencedMaterials([]);
              if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
              const globalAssets = (workspace?.globalAssets || {
                characters: [],
                scenes: [],
                props: [],
                visual_style: {},
              }) as PrepareScenePackagesResponse["global_assets"];
              const videoScenePackages: PrepareScenePackagesResponse = {
                ok: true,
                message: `已生成 ${packages.length} 个分镜资产包`,
                requires_confirmation: true,
                review_timeout_sec: null,
                target_duration_ms: workspace?.targetDurationMs || DEFAULT_TARGET_DURATION_MS,
                global_assets: globalAssets,
                scene_packages: packages as PrepareScenePackagesResponse["scene_packages"],
                creation_contract: (workspace?.creationContract || null) as VideoCreationContract | null,
              };
              await pushSceneAssetModelOptionsCard(
                {
                  conversation_id: activeConversation,
                  job_id: String(workspace?.workspaceId || "v2-scene-assets"),
                  artifact: {
                    type: "video_scene_packages",
                    title: "视频场景包",
                    formValues: {},
                    materials: materials as Array<Record<string, unknown>>,
                    videoScenePackages,
                  } as ChatArtifact,
                },
                videoScenePackages,
              );
              setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
                current.length > 0 ? current : createAssetPackageProgressSteps(),
                "awaiting_image_model",
              ));
              return;
            }
          }
        }
        // V2.1：确认卡以外的自然语言一律交给 turns/start → 思考流 → Plan/Tool。
        // 禁止前端关键词断点恢复、本地 pending Job 续跑、直接开资产包/成片 Job。
      }

      const shouldRegisterRuntime = ownership.orchestrationMode === "video_agent_v2"
        || ownership.agentRuntimeMode === "assist"
        || ownership.agentRuntimeMode === "shadow"
        || ownership.agentRuntimeMode === "primary";
      if (shouldRegisterRuntime && !runtimeOptions.skipRuntimeRegistration) {
        const restoredInterruptId = ownership.orchestrationMode === "video_agent_v2"
          && supervisorRuntime.state.conversationId === activeConversation
          ? supervisorRuntime.state.interrupt?.interruptId ?? null
          : null;
        const pendingTurn: PendingSupervisorTurn = {
          conversationId: activeConversation,
          clientInputId: message.id,
          content: text,
          materials,
          replyToMessageId,
          artifactRefs,
          interruptId: ownership.orchestrationMode === "video_agent_v2"
            ? interruptId ?? restoredInterruptId
            : null,
          explicitAction: null,
          continueLegacy: false,
          registrationStatus: "pending",
        };
        await persistPendingSupervisorTurns(
          (current) => (
            current.some((item) => item.clientInputId === message.id)
              ? current
              : [...current, pendingTurn]
          ),
          activeConversation,
        );
        ensurePendingSupervisorTurnVisible(pendingTurn);
        // 在 turns/start 返回前先挂空思考壳，避免“发出去没反应”；正文只等 LLM 流式 delta。
        if (ownership.orchestrationMode === "video_agent_v2") {
          characterSupplementNoticeRef.current = "";
          creativeRevisePendingRef.current = false;
          thinkingTurnAnchorsRef.current[message.id] = message.id;
          setOptimisticAgentThinking({
            turnId: message.id,
            title: "思考中",
            subtitle: "",
            text: "",
            answer: "",
            startedAt: new Date().toISOString(),
            status: "streaming",
          });
        }
        setReferencedMaterials([]);
        if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
        return;
      }
      const pendingPlanMessageJob = pendingMessageJobRef.current;
      if (pendingPlanMessageJob && isPendingPlanSaveForConversation(pendingPlanMessageJob, activeConversation)) {
        schedulePendingMessageJobResume(pendingPlanMessageJob, 0);
        pushAssistant("当前 plan.md 仍在保存，我已继续查询原任务；保存完成后请重新发送这条消息。", activeConversation);
        return;
      }
      if (shouldUseRecoverableIntakeEntry(text, materials, activeConversation)) {
        const pendingMessageJob = await startConversationMessageJobForConversation(message, activeConversation, {
          type: "handle_send",
          content: text,
          materials,
        });
        if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
        if (pendingMessageJob) await resumePendingMessageJob(pendingMessageJob);
        return;
      }
      await appendMessageForConversation(message, activeConversation);
      if (!conversationId) navigate(`/c/${activeConversation}`, { replace: true });
    } catch (err) {
      pushAssistant(`对话保存失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      return;
    }
    const sceneGlobalAssetReference = sceneGlobalAssetReferenceFromMaterials(materials);
    if (sceneGlobalAssetReference) {
      if (sceneGlobalAssetReference.scene_global_asset_action === "delete") {
        await handleDeleteReferencedGlobalAsset(sceneGlobalAssetReference, activeConversation);
      } else {
        await handleEditReferencedGlobalAsset(sceneGlobalAssetReference, text, activeConversation, materials);
      }
      return;
    }
    if (pendingImageEditRequestRef.current?.conversationId === activeConversation) {
      const pendingRequest = pendingImageEditRequestRef.current;
      if (looksLikeImageEditPrompt(text)) {
        pendingImageEditRequestRef.current = null;
      } else {
        const flowMaterials = mergeMaterials(pendingRequest.materials, materials);
        const nextPrompt = text.trim() || pendingRequest.prompt;
        if (!hasImageMaterial(flowMaterials)) {
          pushAssistant("我还没有找到需要编辑的原图，请上传需要编辑的图片后再提交。", activeConversation);
          return;
        }
        await showImageEditOptions({
          ...pendingRequest,
          prompt: nextPrompt,
          materials: flowMaterials,
        });
        return;
      }
    }
    const pendingPptOutlineRevision = pptOutlineRevisionArtifactRef.current;
    const pendingPptOutlineArtifact = pendingPptOutlineRevision?.artifact;
    if (pendingPptOutlineRevision?.conversationId === activeConversation && pendingPptOutlineArtifact?.pptSummary) {
      const projectId = pptProjectId(pendingPptOutlineArtifact);
      pptOutlineRevisionArtifactRef.current = null;
      if (!projectId) {
        pushAssistant("当前 PPT 项目 ID 缺失，无法更新大纲。请重新生成 PPT 大纲。", activeConversation);
        return;
      }
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到 PPT 大纲修改意见，正在调用 SmartPPT 更新大纲…", activeConversation);
      try {
        const request: PptSummaryUpdateJobRequest = {
          original_outline: String(pendingPptOutlineArtifact.pptSummary.summary || ""),
          modification_opinion: text,
          smart_ppt_project_id: projectId,
        };
        const started = await api.createPptSummaryUpdateJob(request);
        const pendingPptJob: PendingPptJob = {
          job_id: started.job_id,
          conversation_id: activeConversation,
          source_message_id: "",
          kind: "summary_update",
          started_at: new Date().toISOString(),
          request,
          artifact: pendingPptOutlineArtifact,
          context: {
            formValues: pendingPptOutlineArtifact.formValues || {},
            materials: mergeMaterials(pendingPptOutlineArtifact.materials, materials),
            coreMessage: `${pendingPptOutlineArtifact.coreMessage || pendingCore}\n大纲修改意见：${text}`,
            intakeContext: pendingPptOutlineArtifact.intakeContext,
            pptStyle: String(pendingPptOutlineArtifact.pptStyle || pendingPptOutlineArtifact.formValues?.ppt_style || "极简商务"),
          },
        };
        await persistPendingPptJob(pendingPptJob, activeConversation, "ppt_outline_update_running", {
          intent: "ppt",
          ppt_outline_feedback: text,
          formValues: pendingPptOutlineArtifact.formValues || {},
          materials: mergeMaterials(pendingPptOutlineArtifact.materials, materials),
          intakeContext: pendingPptOutlineArtifact.intakeContext,
        });
        await resumePendingPptJob(pendingPptJob);
      } catch (err) {
        pushAssistant(`PPT 大纲更新失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingPlanRevision = planRevisionArtifactRef.current;
    if (pendingPlanRevision?.conversationId === activeConversation && pendingPlanRevision.artifact.intent && pendingPlanRevision.artifact.formValues) {
      const revisionArtifact = pendingPlanRevision.artifact;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      if (!isCreationIntent(revisionArtifact.intent) || !revisionArtifact.formValues || !revisionArtifact.plan || !revisionArtifact.selectedDirection) return;
      planRevisionArtifactRef.current = null;
      const choice: PendingPlanRevisionChoice = {
        conversationId: activeConversation,
        artifact: revisionArtifact,
        feedback: text.trim(),
        materials: flowMaterials,
        sourceMessageId: pendingPlanRevision.sourceMessageId || "",
        processedKey: pendingPlanRevision.processedKey,
      };
      setPendingPlanRevisionChoice(choice);
      pushAssistant("已收到 plan.md 修改意见，请选择是在当前创意上修改，还是放弃当前创意并重新生成 3 个方向。", activeConversation);
      void updateConversationWithProgress(activeConversation, {
          last_phase: "plan_revision_mode_pending",
          context: {
            ...makeSnapshot(activeConversation),
            pendingPlanRevisionRequest: null,
            pending_plan_revision_request: null,
            pendingPlanRevisionChoice: choice,
            pending_plan_revision_choice: choice,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
      return;
    }
    const pendingImageRevision = imageRevisionArtifactRef.current;
    const pendingImageRevisionArtifact = pendingImageRevision?.artifact;
    const pendingSceneGlobalAssetReference =
      pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imageResult
        ? sceneGlobalAssetReferenceFromMaterials(pendingImageRevisionArtifact.materials || [])
        : null;
    if (pendingSceneGlobalAssetReference) {
      if (!text.trim()) {
        pushAssistant(`请在输入框填写「${pendingSceneGlobalAssetReference.name}」的图片修改意见，我会继续编辑这张全局素材。`, activeConversation);
        return;
      }
      imageRevisionArtifactRef.current = null;
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact?.materials || [], materials);
      await handleEditReferencedGlobalAsset(pendingSceneGlobalAssetReference, text, activeConversation, flowMaterials);
      return;
    }
    if (pendingImageRevision?.conversationId === activeConversation && pendingImageRevisionArtifact?.imagePrepare && pendingImageRevisionArtifact.imageResult) {
      const flowMaterials = mergeMaterials(pendingImageRevisionArtifact.materials, materials);
      setBusyForConversation(activeConversation, true);
      pushAssistant("已收到图片修改意见，正在重新准备参数并生成图片…", activeConversation);
      try {
        const imagePrepare = await api.prepareImageGeneration(
          {
            ...buildImageRevisionPreparePayload({
              formValues: pendingImageRevisionArtifact.formValues,
              selectedDirection: pendingImageRevisionArtifact.selectedDirection as unknown as Record<string, unknown>,
              planMarkdown: pendingImageRevisionArtifact.plan?.plan_markdown,
              feedback: text,
            }),
            creation_contract: pendingImageRevisionArtifact.plan?.creation_contract,
            materials: flowMaterials,
            intake_context: pendingImageRevisionArtifact.intakeContext,
          },
        );
        if (!imagePrepare.ok) {
          pushArtifact("图片重新生成准备失败，请查看提示。", {
            type: "image_prepare",
            title: "图片重新生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            imageRevisionFeedback: text,
            intent: "image",
            formValues: pendingImageRevisionArtifact.formValues,
            intakeContext: pendingImageRevisionArtifact.intakeContext,
            materials: flowMaterials,
            selectedDirection: pendingImageRevisionArtifact.selectedDirection,
            plan: pendingImageRevisionArtifact.plan,
          }, activeConversation);
          setBusyForConversation(activeConversation, false);
          return;
        }
        const request: ImageGenerationJobRequest = {
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
          conversation_id: targetConversationId,
        };
        const started = await api.startImageGenerationJob(request);
        const pendingImageJob: PendingImageJob = {
          job_id: started.job_id,
          conversation_id: activeConversation,
          source_message_id: "",
          kind: "image_regeneration",
          job_api: "generate",
          started_at: new Date().toISOString(),
          request,
          imagePrepare,
          revision_feedback: text,
          artifact: {
            ...pendingImageRevisionArtifact,
            imagePrepare,
            imageRevisionFeedback: text,
            materials: flowMaterials,
          },
        };
        await persistPendingImageJob(pendingImageJob, activeConversation, "image_regeneration_running", {
          image_revision_feedback: text,
          intake_context: pendingImageRevisionArtifact.intakeContext,
          materials: flowMaterials,
          image_prepare: imagePrepare,
          pendingImageRevision: null,
          pending_image_revision: null,
        });
        imageRevisionArtifactRef.current = null;
        await resumePendingImageJob(pendingImageJob);
      } catch (err) {
        pushAssistant(`图片重新生成失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    const pendingVideoRevision = videoRevisionArtifactRef.current;
    const pendingVideoRevisionArtifact = pendingVideoRevision?.artifact;
    const pendingMergedVideo = pendingVideoRevisionArtifact?.mergedVideo;
    const pendingGeneratedSceneVideos = pendingVideoRevisionArtifact?.generatedSceneVideos;
    const pendingVideoScenePackages = pendingVideoRevisionArtifact?.videoScenePackages;
    if (pendingVideoRevision?.conversationId === activeConversation && pendingVideoRevisionArtifact && pendingMergedVideo && pendingGeneratedSceneVideos && pendingVideoScenePackages) {
      const revisionArtifact = pendingVideoRevisionArtifact;
      const flowMaterials = mergeMaterials(revisionArtifact.materials, materials);
      const mergedVideo = pendingMergedVideo;
      const generatedSceneVideos = pendingGeneratedSceneVideos;
      const videoScenePackages = pendingVideoScenePackages;
      const originalVideoScenePackages =
        revisionArtifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, activeConversation) || videoScenePackages;
      const mergedVideoUrl = mergedVideo.merged_video_url;
      videoRevisionArtifactRef.current = null;
      if (!mergedVideoUrl) {
        pushAssistant("当前没有可分析的合并视频链接，无法进入视频修改流程。", activeConversation);
        return;
      }
      setBusyForConversation(activeConversation, true);
      if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
        videoRevisionArtifactRef.current = null;
        pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, activeConversation);
        setBusyForConversation(activeConversation, false);
        return;
      }
      pushAssistant("已收到视频修改意见，正在调用 QAAgent QC 质检 Skill…", activeConversation);
      try {
        const qualityReview = await api.reviewVideoQuality({
          merged_video_url: mergedVideoUrl,
          scene_videos: generatedSceneVideos.scene_videos.map((scene) => ({
            scene_id: scene.scene_id,
            scene_index: scene.scene_index,
            video_url: scene.video_url,
          })),
          scene_packages: videoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          original_scene_packages: originalVideoScenePackages.scene_packages as unknown as Array<Record<string, unknown>>,
          plan: revisionArtifact.plan as unknown as Record<string, unknown>,
          form_values: revisionArtifact.formValues || {},
          intake_context: revisionArtifact.intakeContext || {},
          selected_direction: revisionArtifact.selectedDirection as unknown as Record<string, unknown>,
          materials: flowMaterials,
          user_feedback: text,
        });
        const affectedSceneIds = new Set(qualityReview.affected_scene_ids || []);
        const affectedSceneLabel = formatSceneIndexesForMessage(videoScenePackages.scene_packages, affectedSceneIds);
        pushArtifact(qualityReview.ok ? "QAAgent QC 质检已完成，请选择本轮修改策略。" : "QAAgent QC 质检失败，可选择只按用户意见继续修改。", {
          type: "video_quality_review",
          title: "QAAgent QC 质检",
          description: qualityReview.ok
            ? `质检定位：${affectedSceneLabel}。`
            : qualityReview.message,
          actionLabel: "选择",
          videoQualityReview: qualityReview,
          videoRevisionFeedback: text,
          videoScenePackages,
          originalVideoScenePackages,
          generatedSceneVideos,
          mergedVideo,
          intent: "video",
          formValues: revisionArtifact.formValues,
          intakeContext: revisionArtifact.intakeContext,
          materials: flowMaterials,
          selectedDirection: revisionArtifact.selectedDirection,
          plan: revisionArtifact.plan,
        }, activeConversation);
        if (activeConversation) {
          void updateConversationWithProgress(activeConversation, {
              last_phase: qualityReview.ok ? "video_quality_review_ready" : "video_quality_review_failed",
              context: {
                ...makeSnapshot(),
                video_revision_feedback: text,
                intake_context: revisionArtifact.intakeContext,
                materials: flowMaterials,
                video_quality_review: qualityReview,
              } as unknown as Record<string, unknown>,
            })
            .catch(() => {});
        }
      } catch (err) {
        pushAssistant(`QAAgent QC 质检失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      } finally {
        setBusyForConversation(activeConversation, false);
      }
      return;
    }
    setBusyForConversation(activeConversation, true);
    pushAssistant("正在调用采集 Agent 识别意图，并抽取可自动填充的表单字段…", activeConversation);
    try {
      const pendingIntakeJob = await startIntakeAnalyzeJob(
        activeConversation,
        { prompt: text, materials },
        message.id,
      );
      void pendingIntakeJob;
    } catch (err) {
      pushAssistant(`采集 Agent 意图识别失败:${err instanceof Error ? err.message : String(err)}`, activeConversation);
      setBusyForConversation(activeConversation, false);
    }
  };

  const handleRetryVideoAnalysis = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.videoAnalysis || artifact.videoAnalysis.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const prompt = artifact.coreMessage || msg.content;
    const materials = artifact.materials || msg.materials || [];
    setBusyForConversation(targetConversationId, true);
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      releaseArtifactAction(processedKey);
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      setBusyForConversation(targetConversationId, false);
      return;
    }
    pushAssistant("正在重新调用视频分析 Skill…", targetConversationId);
    try {
      const videoAnalysis = await api.analyzeStoryboards({ prompt, materials });
      if (!videoAnalysis.ok) releaseArtifactAction(processedKey);
      pushArtifact(videoAnalysis.ok ? "视频分析已重新完成，结果如下。" : "视频分析仍未完成，请查看原因后补充视频链接。", {
        type: "video_analysis_result",
        title: videoAnalysis.mode === "batch" ? "批量视频分析" : "视频分析",
        description: videoAnalysis.ok
          ? `${videoAnalysis.video_urls.length} 个视频，调用 ${videoAnalysis.endpoint}`
          : videoAnalysis.message,
        actionLabel: "查看",
        intent: "video_analysis",
        coreMessage: prompt,
        materials,
        videoAnalysis,
      }, targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: videoAnalysis.ok ? "video_analysis_done" : "video_analysis_failed",
            context: {
              ...makeSnapshot(),
              intent: "video_analysis",
              materials,
              video_analysis: videoAnalysis,
            } as unknown as Record<string, unknown>,
          }, { intent: null })
          .catch(() => {});
      }
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`视频分析重试失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function onEvent(e: TaskEvent) {
    // SSE 事件分发器：后端事件表可能因为断线重连/afterId 被重复消费，这里先按 id 去重。
    if (e.id && seenEventIdsRef.current.has(e.id)) return;
    if (e.id) {
      seenEventIdsRef.current.add(e.id);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, e.id);
    }
    const phase = (e.data.phase as string) || "";
    appendTimelineEvent(e);
    switch (e.event) {
      case "phase_change":
        if (phase) {
          // Brief 未人工确认前，忽略 generate/edit/qc/done 阶段回放，避免旧 run 的 pending
          // 事件把画布提前推进到生成结果态。
          if (["generate", "edit", "qc", "done"].includes(phase) && !briefConfirmedRef.current) return;
          setCanvas((c) => ({ ...c, phase: phase as TaskPhase }));
          if (["segment_review", "edit_review", "qc_review", "done"].includes(phase)) {
            void loadResults(phase as TaskPhase);
            pushReviewArtifact(phase as TaskPhase);
          }
          if (PHASE_MSG[phase] && !announcedPhasesRef.current.has(phase)) {
            announcedPhasesRef.current.add(phase);
            if (!(phase in REVIEW_ARTIFACT)) pushAssistant(PHASE_MSG[phase]);
          }
        }
        break;
      case "brief_ready":
        // brief_ready 表示后端在 LangGraph interrupt 前已经准备好 Brief，前端需要展示确认卡。
        if (briefConfirmedRef.current || briefReadyShownRef.current) return;
        briefReadyShownRef.current = true;
        setCanvas((c) => ({ ...c, phase: "brief_review", brief: toBrief((e.data.brief as Record<string, unknown>) || {}) }));
        setBusy(false);
        pushArtifact("Brief 已生成。点击下方素材卡打开画布查看和确认。", {
          type: "brief",
          title: "视频 Brief",
          description: "分镜、旁白与投放参数",
          actionLabel: "查看",
        });
        break;
      case "task_done":
        // task_done 代表业务任务已完成，可以从 /assets 拉取最终视频或生成片段。
        await loadResults();
        break;
      case "brief_confirmed":
        // brief_confirmed 是业务事件，表示用户确认动作已被后端接收。
        briefConfirmedRef.current = true;
        setBriefConfirmed(true);
        break;
      case "run_finished":
        // run_finished 只表示某个 LangGraph run 结束，不一定等于业务任务 done；
        // 需要再查任务详情，同步 checkpoint 后的 phase/status/brief。
        await refreshTaskAfterRun();
        break;
      case "task_failed":
        pushAssistant(`生成失败:${String(e.data.error ?? "未知错误")}`);
        setBusy(false);
        break;
      case "auth_revoked":
        // 后端 SSE 会在长连接期间持续复查 content-app 登录态；被禁用或 token 失效时会主动断开。
        pushAssistant(`登录态已失效:${String(e.data.message ?? "请重新从 content-app 进入")}`);
        setBusy(false);
        unsubRef.current();
        break;
    }
  }

  async function loadResults(nextPhase: TaskPhase = "done") {
    // 从 /assets 拉取画布可展示的视频资产。当前只展示 final_video 和 generated_video；
    // jianying_draft 是本地草稿路径，浏览器通常不能直接播放。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const [assets, taskResult] = await Promise.all([api.listAssets(id), api.getResult(id).catch(() => null)]);
      const videos = assets.filter((a) => a.asset_type === "final_video" || a.asset_type === "generated_video");
      const results: VideoResult[] = await Promise.all(
        videos.map(async (a, i) => {
          let url = a.url;
          if (a.asset_type === "final_video") {
            try {
              url = await api.assetContentBlobUrl(id, a.asset_id);
            } catch {
              url = "";
            }
          }
          return {
            id: a.asset_id || `r${i}`,
            url,
            assetType: a.asset_type,
            status: a.status === "ready" && url ? "success" : a.status === "error" ? "failed" : "pending",
          };
        }),
      );
      const qcReport = taskResult?.result?.qc_report;
      setCanvas((c) => ({
        ...c,
        phase: nextPhase,
        results,
        qcReport: qcReport && typeof qcReport === "object" ? c.qcReport || (qcReport as CanvasState["qcReport"]) : c.qcReport,
      }));
      if (nextPhase === "done") {
        pushArtifact("生成完成,素材已就绪。点击下方素材卡打开画布查看。", {
          type: "results",
          title: "生成素材",
          description: `${results.length} 条视频结果`,
          actionLabel: "打开",
        });
      }
    } catch {
      pushAssistant("结果拉取失败,请稍后在历史中查看。");
    } finally {
      setBusy(false);
    }
  }

  async function refreshTaskAfterRun() {
    // LangGraph run 结束后重新查询业务任务。后端 getTask 会先同步 checkpoint，
    // 因此前端能拿到最新 phase、brief、error 和 result。
    const id = taskIdRef.current;
    if (!id) return;
    try {
      const task = await api.getTask(id);
      const confirmed = task.phase !== "brief_review";
      briefConfirmedRef.current = confirmed;
      setBriefConfirmed(confirmed);
      setCanvas((c) => ({
        ...c,
        phase: (task.phase as TaskPhase) || c.phase,
        brief: task.brief && Object.keys(task.brief).length > 0 ? toBrief(task.brief) : c.brief,
      }));
      if (task.status === "done") {
        await loadResults("done");
        return;
      }
      if (["segment_review", "edit_review", "qc_review"].includes(task.phase)) {
        await loadResults(task.phase as TaskPhase);
        if (PHASE_MSG[task.phase] && !announcedPhasesRef.current.has(task.phase)) {
          announcedPhasesRef.current.add(task.phase);
          pushAssistant(PHASE_MSG[task.phase]);
        }
        return;
      }
      if (task.phase === "brief_review") {
        setBusy(false);
        pushAssistant("Brief 已就绪,请打开素材卡确认后再生成视频。");
      }
      if (task.status === "error") {
        setBusy(false);
        pushAssistant(`生成失败:${task.error || "未知错误"}`);
      }
    } catch {
      pushAssistant("任务状态同步失败,请稍后重试。");
      setBusy(false);
    }
  }

  // 参数弹窗确认后的主链路：创建业务任务 -> 记录 taskId -> 重置事件缓存 -> 订阅 SSE。
  const handleConfirmParams = async (form: GenParamsForm) => {
    const dialogContext = pendingDialogContextRef.current;
    const targetConversationId = dialogContext?.conversationId || conversationIdRef.current;
    const flowMaterials = dialogContext?.materials || pendingMaterials;
    const flowCoreMessage = dialogContext?.coreMessage || pendingCore;
    const flowIntakeContext = dialogContext?.intakeContext || {};
    setDialogOpen(false);
    setPendingFormValues({});
    setBusyForConversation(targetConversationId, true);
    const values = valuesFromForm(form);
    try {
      if (form.intent === "ppt") {
        advanceWorkflowProgress(targetConversationId, "ppt_outline_running", { intent: "ppt", flow_kind: "standard" });
        pushAssistant("正在调用 SmartPPT 生成 PPT 大纲，这一步可能需要等待一会儿…", targetConversationId);
        const request: PptSummaryJobRequest = {
          ppt_topic: form.ppt_topic,
          ppt_style: form.ppt_style,
          attachments: form.attachments,
        };
        const started = await api.createPptSummaryJob(request);
        pendingDialogContextRef.current = null;
        const pendingPptJob: PendingPptJob = {
          job_id: started.job_id,
          conversation_id: targetConversationId,
          source_message_id: "",
          kind: "summary_generation",
          started_at: new Date().toISOString(),
          request,
          context: {
            formValues: values,
            materials: mergeMaterials(flowMaterials, form.attachments),
            coreMessage: flowCoreMessage,
            intakeContext: flowIntakeContext,
            pptStyle: form.ppt_style,
          },
        };
        await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_outline_running", {
          intent: "ppt",
          ppt_form: form,
          form_values: values,
          intake_context: flowIntakeContext,
          materials: mergeMaterials(flowMaterials, form.attachments),
        });
        await resumePendingPptJob(pendingPptJob);
        setBusyForConversation(targetConversationId, false);
        return;
      }
      pendingDialogContextRef.current = null;
      await startDirectionJob(
        targetConversationId,
        {
          intent: form.intent,
          values,
          materials: flowMaterials,
          intake_context: flowIntakeContext,
        },
        {
          intent: form.intent,
          formValues: values,
          materials: flowMaterials,
          coreMessage: flowCoreMessage,
          intakeContext: flowIntakeContext,
          form,
        },
        "directions_running",
      );
      setBusyForConversation(targetConversationId, false);
    } catch (err) {
      pushAssistant(`采集处理失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleCancelParamsDialog = () => {
    const dialogContext = pendingDialogContextRef.current;
    const targetConversationId = dialogContext?.conversationId || conversationIdRef.current;
    const cancelledIntent = pendingIntent;
    setDialogOpen(false);
    setPendingCore("");
    setPendingFormValues({});
    setPendingMaterials([]);
    pendingDialogContextRef.current = null;
    const flowDraft = makeFlowDraft("form_cancelled", {
      intent: cancelledIntent,
      coreMessage: dialogContext?.coreMessage || pendingCore,
      materials: dialogContext?.materials || pendingMaterials,
      intakeContext: dialogContext?.intakeContext,
      formValues: pendingFormValues,
    });
    flowDraftRef.current = flowDraft;
    setBusyForConversation(targetConversationId, false);
    pushAssistant("已取消当前需求表单，流程已终止。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "form_cancelled",
          context: {
            ...makeSnapshot(),
            flowDraft,
            intent: cancelledIntent,
            form_cancelled: true,
          } as unknown as Record<string, unknown>,
        }, { intent: cancelledIntent })
        .catch(() => {});
    }
  };

  const handleApprovePptOutline = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.pptSummary) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    if (!projectId) {
      pushAssistant("当前 PPT 项目 ID 缺失，无法继续生成页面结构。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "ppt_content_json_running", { intent: "ppt" });
    pushAssistant("PPT 大纲已确认，正在生成页面 JSON 并准备生成 PPT 图片…", targetConversationId);
    try {
      const pptStyle = String(artifact.pptStyle || artifact.formValues?.ppt_style || "极简商务");
      const request: PptContentJsonJobRequest = {
        original_outline: String(artifact.pptSummary.summary || ""),
        ppt_style: pptStyle,
        smart_ppt_project_id: projectId,
      };
      const started = await api.createPptContentJsonJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "content_json_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_content_json_running", {
        intent: "ppt",
        ppt_summary: artifact.pptSummary,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 页面图片生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRevisePptOutline = (msg: ChatMessage) => {
    if (!msg.artifact?.pptSummary) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    pptOutlineRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: msg.artifact };
    pushAssistant("请在输入框填写 PPT 大纲修改意见，我会基于当前大纲继续更新。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "ppt_outline_revision_requested",
          context: {
            ...makeSnapshot(targetConversationId),
            pendingPptOutlineRevision: pptOutlineRevisionArtifactRef.current,
            pending_ppt_outline_revision: pptOutlineRevisionArtifactRef.current,
          } as unknown as Record<string, unknown>,
        }, { intent: "ppt" })
        .catch(() => {});
    }
  };

  const handleRegeneratePptImage = async (msg: ChatMessage, pageIndex: number) => {
    const artifact = msg.artifact;
    if (!artifact?.pptImages) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    const pageJson = pptPageJson(artifact, pageIndex);
    if (!projectId || !pageJson) {
      pushAssistant("当前页缺少 PPT 项目 ID 或页面 JSON，无法重新生成。", targetConversationId);
      return;
    }
    const runningImages: PptImagesResult = {
      ...artifact.pptImages,
      ok: false,
      pages: artifact.pptImages.pages.map((page) =>
        page.page_index === pageIndex
          ? {
              ...page,
              status: "running",
              image_url: null,
              error: null,
              json_content: page.json_content || pageJson,
            }
          : page,
      ),
      message: `第 ${pageIndex} 页 PPT 图片重新生成中。`,
      quota_insufficient: false,
    };
    updatePptImagesArtifactInMessage(msg.id, targetConversationId, runningImages, artifact);
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在重新生成第 ${pageIndex} 页 PPT 图片…`, targetConversationId);
    try {
      const request: PptImageRegenerationJobRequest = {
        page_index: pageIndex,
        page_json: pageJson,
        smart_ppt_project_id: projectId,
      };
      const started = await api.createPptImageRegenerationJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_regeneration",
        started_at: new Date().toISOString(),
        request,
        artifact: { ...artifact, pptImages: runningImages },
        image_message_id: msg.id,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_image_regeneration_running", {
        intent: "ppt",
        ppt_images: runningImages,
      });
      await resumePendingPptJob(pendingPptJob);
    } catch (err) {
      const failedImages: PptImagesResult = {
        ...runningImages,
        ok: false,
        pages: runningImages.pages.map((page) =>
          page.page_index === pageIndex
            ? {
                ...page,
                status: "failed",
                image_url: null,
                error: err instanceof Error ? err.message : String(err),
              }
            : page,
        ),
        message: `第 ${pageIndex} 页 PPT 图片重新生成失败。`,
      };
      updatePptImagesArtifactInMessage(msg.id, targetConversationId, failedImages, artifact);
      pushAssistant(`PPT 页面图片重新生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleGeneratePptFile = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.pptImages) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const projectId = pptProjectId(artifact);
    const fileUrls = pptImageFileUrls(artifact);
    if (!projectId || !fileUrls.length) {
      pushAssistant("请先确保所有 PPT 页面图片都已生成成功。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "ppt_file_generation_running", { intent: "ppt" });
    pushAssistant("正在调用 SmartPPT 生成 PPT 附件…", targetConversationId);
    try {
      const request: PptFileJobRequest = { smart_ppt_project_id: projectId, file_urls: fileUrls };
      const started = await api.createPptFileJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "file_generation",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_file_generation_running", {
        intent: "ppt",
        ppt_images: artifact.pptImages,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 附件生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRegeneratePptFile = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!artifact?.pptFile || artifact.pptDone || isPptDoneForConversation(targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const projectId = pptProjectId(artifact);
    const fileUrls = pptImageFileUrls(artifact);
    if (!projectId || !fileUrls.length) {
      releaseArtifactAction(processedKey);
      pushAssistant("没有找到可用于重新生成 PPT 附件的页面图片。", targetConversationId);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    pushAssistant("正在重新生成 PPT 附件…", targetConversationId);
    try {
      const request: PptFileJobRequest = { smart_ppt_project_id: projectId, file_urls: fileUrls };
      const started = await api.createPptFileJob(request);
      const pendingPptJob: PendingPptJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "file_regeneration",
        started_at: new Date().toISOString(),
        request,
        artifact,
      };
      await persistPendingPptJob(pendingPptJob, targetConversationId, "ppt_file_regeneration_running", {
        intent: "ppt",
        ppt_images: artifact.pptImages,
      });
      await resumePendingPptJob(pendingPptJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`PPT 附件重新生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleAcceptPptFile = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!msg.artifact?.pptFile?.ok || msg.artifact.pptDone || isPptDoneForConversation(targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setPptDoneForConversation(targetConversationId, true);
    markPptFileDoneInMessage(msg.id, targetConversationId);
    pushAssistant("已确认 PPT 附件满意，制作 PPT 流程结束。", targetConversationId);
    void updateConversationWithProgress(targetConversationId, {
        last_phase: "ppt_done",
        context: {
          ...makeSnapshot(),
          intent: "ppt",
          ppt_done: true,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {})
      .finally(() => releaseArtifactAction(processedKey));
  };

  const handleRegenerateDirections = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (artifact?.type !== "directions" || !isCreationIntent(artifact.intent) || !artifact.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, msg)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const previousDirections = (artifact.directions || []).map((direction) => ({
      title: direction.title,
      description: direction.description,
      tags: direction.tags,
      data: direction.data,
    }));
    const regenerationFeedback = "用户对上一轮 3 个创意方向都不满意，请避开上一轮方向，生成新的差异化方向。";
    setBusyForConversation(targetConversationId, true);
    pushAssistant("已收到不满意反馈，正在重新生成 3 个创意方向…", targetConversationId);
    try {
      await startDirectionJob(
        targetConversationId,
        {
          intent: artifact.intent,
          values: artifact.formValues,
          materials: artifact.materials || [],
          product_creative_profile: {
            core_message: artifact.coreMessage || pendingCore,
            regenerate: true,
            revision_feedback: regenerationFeedback,
            regeneration_feedback: regenerationFeedback,
            previous_creative_directions: previousDirections,
          },
          intake_context: artifact.intakeContext,
        },
        {
          intent: artifact.intent,
          formValues: artifact.formValues,
          materials: artifact.materials || [],
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          revisionFeedback: regenerationFeedback,
        },
        "directions_running",
        msg.id,
      );
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`重新生成创意方向失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleSelectDirection = async (msg: ChatMessage, direction: CreativeDirectionResponse) => {
    if (!isCreationIntent(msg.artifact?.intent) || !msg.artifact?.formValues) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (hasLaterDirectionSuccessor(messagesRef.current, targetConversationId, msg)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "plan_generation_running", { intent: msg.artifact.intent });
    pushAssistant(`已选择创意方向「${direction.title}」，正在生成 plan.md…`, targetConversationId);
    try {
      const request: PlanGenerationJobRequest = {
        intent: msg.artifact.intent,
        form_values: msg.artifact.formValues,
        selected_direction: direction as unknown as Record<string, unknown>,
        product_creative_profile: { core_message: msg.artifact.coreMessage || pendingCore },
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      };
      const started = await api.startPlanMarkdownJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "plan_generation",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: msg.artifact.intent,
          formValues: msg.artifact.formValues,
          selectedDirection: direction,
          materials: msg.artifact.materials || [],
          coreMessage: msg.artifact.coreMessage || pendingCore,
          intakeContext: msg.artifact.intakeContext,
          processedKey,
        },
      };
      const persistenceContext = {
        selected_direction: direction,
        form_values: msg.artifact.formValues,
        intake_context: msg.artifact.intakeContext,
        materials: msg.artifact.materials || [],
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_generation_running",
          persistenceContext,
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_generation_running",
          persistenceContext,
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleApprovePlan = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.plan) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    // Video Agent 脚本方案卡：「同意方案」= 右侧确认 = 自然语言确认
    if (
      artifact.scriptPlanConfirmForAssets
      || artifact.title === "脚本方案待确认"
      || artifact.title === "已确认脚本方案"
    ) {
      if (!targetConversationId) return;
      const processedKey = beginArtifactAction(msg, targetConversationId);
      if (!processedKey) return;
      try {
        await confirmScriptPlanAndGenerateAssetPackage(
          targetConversationId,
          artifact.plan.plan_markdown || "",
          msg.id,
        );
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(
          err instanceof Error ? err.message : `确认脚本方案失败：${String(err)}`,
          targetConversationId,
        );
      }
      return;
    }
    if (!artifact.intent || !artifact.formValues || !artifact.selectedDirection) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (artifact.intent === "image") {
      setBusyForConversation(targetConversationId, true);
      advanceWorkflowProgress(targetConversationId, "image_prepare_running", { intent: "image" });
      pushAssistant("图片 plan.md 已同意，正在准备图片生成参数…", targetConversationId);
      try {
        const imagePrepare = await api.prepareImageGeneration({
          form_values: artifact.formValues,
          plan_markdown: artifact.plan.plan_markdown,
          selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
          creation_contract: artifact.plan.creation_contract,
          materials: artifact.materials || [],
          intake_context: artifact.intakeContext,
        });
        if (!imagePrepare.ok) {
          releaseArtifactAction(processedKey);
          pushArtifact("图片生成准备发现当前能力暂不可用，请按提示调整。", {
            type: "image_prepare",
            title: "图片生成准备",
            description: imagePrepare.message,
            actionLabel: "查看",
            imagePrepare,
            intent: "image",
            formValues: artifact.formValues,
            intakeContext: artifact.intakeContext,
            materials: artifact.materials || [],
            selectedDirection: artifact.selectedDirection,
            plan: artifact.plan,
          }, targetConversationId);
          if (targetConversationId) {
            void updateConversationWithProgress(targetConversationId, {
                last_phase: "image_generation_blocked",
                context: {
                  ...makeSnapshot(),
                  plan_approved: true,
                  plan_markdown: artifact.plan.plan_markdown,
                  intake_context: artifact.intakeContext,
                  materials: artifact.materials || [],
                  image_prepare: imagePrepare,
                } as unknown as Record<string, unknown>,
              })
              .catch(() => {});
          }
          return;
        }
        pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
        const request: ImageGenerationJobRequest = {
          method: imagePrepare.method,
          prompt: imagePrepare.prompt,
          negative_prompt: imagePrepare.negative_prompt,
          params: imagePrepare.params,
          conversation_id: targetConversationId,
        };
        const started = await api.startImageGenerationJob(request);
        const pendingImageJob: PendingImageJob = {
          job_id: started.job_id,
          conversation_id: targetConversationId,
          source_message_id: msg.id,
          kind: "image_generation",
          job_api: "generate",
          started_at: new Date().toISOString(),
          request,
          artifact,
          imagePrepare,
        };
        await persistPendingImageJob(pendingImageJob, targetConversationId, "image_generation_running", {
          plan_approved: true,
          plan_markdown: artifact.plan.plan_markdown,
          intake_context: artifact.intakeContext,
          materials: artifact.materials || [],
          image_prepare: imagePrepare,
        });
        await resumePendingImageJob(pendingImageJob, processedKey);
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(`图片生成参数准备失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
    setBusyForConversation(targetConversationId, true);
    advanceWorkflowProgress(targetConversationId, "scene_package_generation_running", {
      intent: "video",
      scene_package_stage: "prepare_scene_packages",
    });
    const formValues = artifact.formValues;
    const selectedDirection = artifact.selectedDirection;
    const creationContract = artifact.plan.creation_contract as unknown as VideoCreationContract;
    if (!creationContract || typeof creationContract.video_duration_sec !== "number" || !creationContract.video_model || !creationContract.image_model) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("视频 plan.md 缺少完整制作合同，请重新生成 plan.md 后再继续。", targetConversationId);
      return;
    }
    const assetManifest = artifact.plan.asset_manifest;
    if (!assetManifest || !Array.isArray(assetManifest.characters) || !Array.isArray(assetManifest.scenes) || !Array.isArray(assetManifest.props)) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("视频 plan.md 缺少最终角色、道具、场景清单，请先重新生成或修订 plan.md。", targetConversationId);
      return;
    }
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      return;
    }
  };

  /** 把终稿脚本落到聊天消息，供「同意方案」确认后生成资产包。 */
  const ensureDurableScriptPlanMessage = (
    targetConversationId: string,
    planMarkdown: string,
    _afterUserMessageId?: string,
  ) => {
    if (!targetConversationId || !planMarkdown.trim()) return;
    const fingerprint = `${targetConversationId}:${planMarkdown.length}:${planMarkdown.slice(0, 80)}`;
    if (durableScriptPlanMessageIdsRef.current.has(fingerprint)) return;
    const already = messagesRef.current.some((message) => (
      message.conversationId === targetConversationId
      && message.role === "assistant"
      && message.artifact?.type === "plan"
      && (
        message.artifact.scriptPlanConfirmForAssets
        || message.artifact.title === "脚本方案待确认"
        || message.artifact.title === "已确认脚本方案"
      )
      && message.artifact.plan?.plan_markdown === planMarkdown
    ));
    if (already) {
      durableScriptPlanMessageIdsRef.current.add(fingerprint);
      return;
    }
    const plan: PlanMarkdownResponse = {
      output_type: "video",
      plan_markdown: planMarkdown,
      template_path: "",
      consistency_issues: [],
      review_timeout_sec: null,
      plan_version: 1,
      plan_history: [],
      creation_contract: {},
      scene_blueprints: [],
    };
    void appendMessageForConversation(
      {
        id: uid(),
        conversationId: targetConversationId,
        role: "assistant",
        content: "脚本方案已就绪。点击「同意方案」、右侧确认，或回复「确认脚本」，即可生成视频资产包。",
        time: "",
        artifact: {
          type: "plan",
          title: "脚本方案待确认",
          description: "确认后将生成视频资产包（含角色/场景/道具）",
          actionLabel: "查看",
          intent: "video",
          formValues: {},
          materials: [],
          plan,
          scriptPlanConfirmForAssets: true,
          scriptPlanConfirmed: false,
        },
      },
      targetConversationId,
    );
    durableScriptPlanMessageIdsRef.current.add(fingerprint);
  };

  const markDurableScriptPlanConfirmed = (targetConversationId: string, sourceMessageId?: string) => {
    setMessages((items) => items.map((message) => {
      if (messageConversationId(message, targetConversationId) !== targetConversationId) return message;
      if (sourceMessageId && message.id === sourceMessageId && message.artifact?.type === "plan") {
        return {
          ...message,
          artifact: {
            ...message.artifact,
            title: "已确认脚本方案",
            description: "已确认，正在生成视频资产包",
            scriptPlanConfirmForAssets: true,
            scriptPlanConfirmed: true,
          },
        };
      }
      if (
        message.artifact?.type === "plan"
        && message.artifact.scriptPlanConfirmForAssets
        && !message.artifact.scriptPlanConfirmed
      ) {
        return {
          ...message,
          artifact: {
            ...message.artifact,
            title: "已确认脚本方案",
            description: "已确认，正在生成视频资产包",
            scriptPlanConfirmed: true,
          },
        };
      }
      return message;
    }));
  };

  const saveVideoAgentScriptWithRevisionRetry = async (
    conversationId: string,
    markdown: string,
    options?: { confirmForGeneration?: boolean },
  ) => {
    const readRevision = () => supervisorRuntime.state.videoAgentWorkspace.current?.revision
      ?? videoAgentView.workspace?.revision
      ?? null;
    let revision = readRevision();
    if (!revision || revision < 1) {
      throw new Error("当前会话没有可保存的脚本工作区");
    }
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await supervisorApi.saveVideoAgentScript(conversationId, {
          markdown,
          expected_revision: revision,
          ...(options?.confirmForGeneration ? { confirm_for_generation: true } : {}),
        });
        await supervisorRuntime.refreshSnapshot();
        return;
      } catch (error) {
        lastError = error;
        const status = error instanceof SupervisorApiError ? error.status : 0;
        if (status !== 409) throw error;
        const conflictRevision = error instanceof SupervisorApiError
          ? error.currentRevision
          : null;
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        const snapshotRevision = readRevision();
        // 优先用 409 正文里的权威 revision；Snapshot 投影滞后时也能推进重试。
        const nextRevision = (
          typeof conflictRevision === "number" && conflictRevision >= 1
            ? conflictRevision
            : null
        ) ?? snapshotRevision;
        if (typeof nextRevision === "number" && nextRevision >= 1 && nextRevision !== revision) {
          revision = nextRevision;
          continue;
        }
        // 同 revision 的 CAS 竞态：短暂等待后用原 revision 再试，避免死磕。
        if (attempt < 2) {
          await new Promise((resolve) => {
            window.setTimeout(resolve, 40 * (attempt + 1));
          });
          continue;
        }
        throw error;
      }
    }
    throw lastError instanceof Error ? lastError : new Error("脚本保存冲突，请刷新后重试");
  };

  /** 三种确认信号的统一入口：右下角确认 / 同意方案 / 自然语言确认。 */
  const confirmScriptPlanAndGenerateAssetPackage = async (
    targetConversationId: string,
    markdownHint = "",
    sourceMessageId?: string,
  ) => {
    const workspace = videoAgentView.workspace;
    if (!targetConversationId || !workspace?.script) {
      throw new Error("当前会话没有可确认的脚本工作区");
    }
    const hasStructuredStages = (workspace.scriptStages?.length ?? 0) > 0;
    const hint = (markdownHint || "").trim();
    const scriptContent = (workspace.script.content || "").trim();
    // 有分阶段产物且未真正改稿时，不要用 script.content（导入原文）盖住拆解后的 episode。
    const preferStages = hasStructuredStages && (!hint || hint === scriptContent);
    const markdown = resolveGeneratableScriptMarkdown({
      scriptContent: preferStages ? "" : (hint || scriptContent),
      stages: workspace.scriptStages,
    });
    if (!markdown.trim()) {
      throw new Error("当前没有可确认的脚本正文");
    }
    if (!workspaceHasExportReady({
      scriptContent: markdown,
      stages: workspace.scriptStages,
    })) {
      throw new Error("请先完成「导出脚本产物」，再确认并生成资产包");
    }
    const readinessInput = {
      scriptContent: markdown,
      stages: workspace.scriptStages,
    };
    if (scriptNeedsFullCharacterPlan(readinessInput)) {
      const readiness = analyzeScriptCharacterReadiness(readinessInput);
      const hints = readiness.missingHints.slice(0, 2).join("；") || "角色设定不完整";
      throw new Error(`请先补齐角色设定后再确认：${hints}`);
    }
    setConfirmingVideoAgentScript(true);
    try {
      const materials = (workspace.assets || [])
        .filter((asset): asset is typeof asset & { url: string } => Boolean(asset.url))
        .map((asset) => ({
          asset_id: asset.artifactRef,
          url: asset.url,
          type: asset.mediaType,
        }));
      // V2.1：按钮确认走独立命令 API，不再伪造「确认脚本」自然语言 Turn。
      if (orchestrationModeRef.current === "video_agent_v2") {
        const progressSteps = applyAssetPackageJobStage(
          createAssetPackageProgressSteps(),
          "prepare_scene_packages",
        );
        setAssetPackageProgressSteps(progressSteps);
        const progressAnchor = resolveAssetPackageProgressAnchorId({
          preferredAnchorId: sourceMessageId || assetPackageAnchorMessageIdRef.current,
          messages: messagesRef.current,
        });
        if (progressAnchor) {
          assetPackageAnchorMessageIdRef.current = progressAnchor;
          setAssetPackageAnchorMessageId(progressAnchor);
        }
        await confirmVideoAgentScriptPlanWithRevisionRetry(
          targetConversationId,
          markdown,
        );
        // 仅在命令成功后再标记确认，避免 409 重试前的乐观态误导。
        scriptPlanConfirmedRef.current = true;
        markDurableScriptPlanConfirmed(targetConversationId, sourceMessageId);
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        // 命令成功后先解卡；若仍在异步 prepare（无包且 job 活跃），hydrate 会再打回 running。
        const confirmedWorkspace = supervisorRuntime.state.videoAgentWorkspace.current
          ?? videoAgentView.workspace;
        const confirmedPackages = Array.isArray(confirmedWorkspace?.scenePackages)
          ? confirmedWorkspace.scenePackages
          : [];
        const confirmedImages = countGlobalAssetImageUrls(confirmedWorkspace?.globalAssets);
        const confirmedJobStatus = String(confirmedWorkspace?.scenePackageJob?.status || "").toLowerCase();
        const confirmedJobActive = Boolean(
          confirmedWorkspace?.scenePackageJob
          && ["polling", "running", "start_paused_quota", "queued"].includes(confirmedJobStatus),
        );
        if (!(confirmedPackages.length === 0 && confirmedJobActive)) {
          setAssetPackageProgressSteps((current) => applyAssetPackageJobStage(
            current.length > 0 ? current : createAssetPackageProgressSteps(),
            confirmedImages > 0 && confirmedPackages.length > 0
              ? "completed"
              : "awaiting_image_model",
          ));
        }
        return;
      }
      await saveVideoAgentScriptWithRevisionRetry(targetConversationId, markdown, {
        confirmForGeneration: true,
      });
      scriptPlanConfirmedRef.current = true;
      markDurableScriptPlanConfirmed(targetConversationId, sourceMessageId);
      ensureDurableScriptPlanMessage(targetConversationId, markdown);
      markDurableScriptPlanConfirmed(targetConversationId, sourceMessageId);
      await startVideoAgentAssetPackageFromScript(
        targetConversationId,
        markdown,
        materials,
        "已确认脚本方案，正在生成视频资产包…",
      );
    } finally {
      setConfirmingVideoAgentScript(false);
    }
  };

  const confirmVideoAgentScriptPlanWithRevisionRetry = async (
    conversationId: string,
    markdown: string,
  ) => {
    const readRevision = () => supervisorRuntime.state.videoAgentWorkspace.current?.revision
      ?? videoAgentView.workspace?.revision
      ?? null;
    // 导入/补字段后本地 revision 常滞后；先拉权威 Snapshot，减少无意义首跳 409。
    await supervisorRuntime.refreshSnapshot().catch(() => {});
    let revision = readRevision();
    if (!revision || revision < 1) {
      throw new Error("当前会话没有可确认的脚本工作区");
    }
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await supervisorApi.confirmVideoAgentScriptPlan(conversationId, {
          expected_revision: revision,
          markdown,
        });
        return;
      } catch (error) {
        lastError = error;
        if (!(error instanceof SupervisorApiError)) throw error;
        if (error.status === 422) {
          const missing = error.missingFields.length > 0
            ? error.missingFields.join("、")
            : "";
          throw new Error(
            missing
              ? `脚本方案仍缺少：${missing}。请先在对话框补齐后再确认。`
              : (error.message || "脚本方案未就绪，请先补齐生产字段"),
          );
        }
        if (error.status !== 409) throw error;
        const conflictRevision = error.currentRevision;
        await supervisorRuntime.refreshSnapshot().catch(() => {});
        const snapshotRevision = readRevision();
        const nextRevision = (
          typeof conflictRevision === "number" && conflictRevision >= 1
            ? conflictRevision
            : null
        ) ?? snapshotRevision;
        if (typeof nextRevision === "number" && nextRevision >= 1 && nextRevision !== revision) {
          revision = nextRevision;
          continue;
        }
        if (attempt < 2) {
          await new Promise((resolve) => {
            window.setTimeout(resolve, 40 * (attempt + 1));
          });
          continue;
        }
        throw error;
      }
    }
    throw lastError instanceof Error ? lastError : new Error("脚本确认冲突，请刷新后重试");
  };

  /** 脚本就绪且用户确认方案后进入视频资产包生成。 */
  const startVideoAgentAssetPackageFromScript = async (
    targetConversationId: string,
    planMarkdown: string,
    materials: Array<Record<string, unknown>> = [],
    notice = "正在生成视频资产包…",
    options: { revisionFeedback?: string } = {},
  ) => {
    if (!targetConversationId || !planMarkdown.trim()) return;
    // V2.1 批次 B：自然语言/按钮重做也走 Turn，禁止直接 startPrepareScenePackagesJob。
    if (orchestrationModeRef.current === "video_agent_v2") {
      const revisionFeedback = options.revisionFeedback?.trim() || "";
      const content = revisionFeedback
        ? `请按修改意见重新生成视频资产包：${revisionFeedback}`
        : "确认脚本";
      pushAssistant(notice, targetConversationId);
      await handleSupervisorTurn(
        {
          conversationId: targetConversationId,
          clientInputId: uid(),
          content,
          materials,
          replyToMessageId: null,
          artifactRefs: [],
          interruptId: null,
          explicitAction: null,
          continueLegacy: false,
          registrationStatus: "pending",
        },
        supervisorRuntime.getContextVersion() ?? 0,
      );
      return;
    }
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      return;
    }
  };

  const handleEditPlan = (msg: ChatMessage) => {
    if (!msg.artifact?.plan || !isCreationIntent(msg.artifact.intent)) return;
    setSelectedStoryboardMessageId("");
    setSelectedPlanEditorMessageId(msg.id);
    setCanvasOpen(true);
  };

  const handlePublishPlanEdit = async (msg: ChatMessage, editedMarkdown: string) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const existingPlanJob = pendingPlanJobRef.current;
    if (existingPlanJob?.job_id && existingPlanJob.conversation_id === targetConversationId) {
      setSavingPlanEdit(existingPlanJob.kind === "plan_manual_edit");
      setBusyForConversation(targetConversationId, true);
      schedulePendingPlanJobPersistence(
        existingPlanJob,
        pendingPlanJobRunningPhase(existingPlanJob.kind),
        pendingPlanJobPersistenceContext(existingPlanJob.kind),
      );
      await resumePendingPlanJob(existingPlanJob);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setSavingPlanEdit(true);
    setBusyForConversation(targetConversationId, true);
    try {
      const request: PlanManualEditJobRequest = {
        intent: artifact.intent,
        form_values: artifact.formValues,
        selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
        current_plan_markdown: artifact.plan.plan_markdown,
        edited_plan_markdown: editedMarkdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_durations_sec: artifact.plan.scene_durations_sec || [],
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
        product_creative_profile: { core_message: artifact.coreMessage || pendingCore },
        intake_context: artifact.intakeContext,
        materials: artifact.materials || [],
      };
      const started = await api.startPlanManualEditJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "plan_manual_edit",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: artifact.intent,
          formValues: artifact.formValues,
          selectedDirection: artifact.selectedDirection,
          materials: artifact.materials || [],
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          processedKey,
        },
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_manual_edit_running",
          { plan_approved: false },
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_manual_edit_running",
          { plan_approved: false },
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 编辑发布失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setSavingPlanEdit(false);
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetrySceneAssets = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    const hasSceneAssetFailures = Boolean(artifact?.sceneAssetFailures?.length);
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!artifact || !videoScenePackages?.scene_packages.length || !hasSceneAssetFailures) {
      if (targetConversationId) {
        pushAssistant("当前卡片没有可重试的失败参考图，请从最新场景包卡片操作。", targetConversationId);
      }
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) {
      pushAssistant("参考图重试仍在处理中，请稍候…", targetConversationId);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    const targetAssets = sceneAssetRetryTargets(artifact.sceneAssetFailures);
    if (!targetAssets.length) {
      releaseArtifactAction(processedKey);
      setBusyForConversation(targetConversationId, false);
      pushAssistant("失败记录缺少可重试的素材标识，请从最新 plan 或场景包重新生成。", targetConversationId);
      return;
    }
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED && orchestrationModeRef.current !== "video_agent_v2") {
      notifyLegacyVideoJobBlocked(targetConversationId, processedKey);
      setBusyForConversation(targetConversationId, false);
      return;
    }
    // V2.1 批次 B：失败参考图重试走 Turn；勿在 Turn 前 pushAssistant，避免刷新后助手气泡抢到用户消息前面。
    if (orchestrationModeRef.current === "video_agent_v2") {
      try {
        await handleSupervisorTurn(
          {
            conversationId: targetConversationId,
            clientInputId: uid(),
            content: `继续生成失败的参考图（共 ${targetAssets.length} 个）`,
            materials: artifact.materials || [],
            replyToMessageId: null,
            artifactRefs: [],
            interruptId: null,
            explicitAction: null,
            continueLegacy: false,
            registrationStatus: "pending",
          },
          supervisorRuntime.getContextVersion() ?? 0,
        );
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(`场景参考图继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
    pushAssistant(`正在重新生成 ${targetAssets.length} 个失败的场景参考图…`, targetConversationId);
  };

  const handleRevisePlan = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setAgentRevisionSourceMessageId(msg.id);
    planRevisionArtifactRef.current = msg.artifact
      ? { conversationId: targetConversationId, artifact: msg.artifact, sourceMessageId: msg.id, processedKey }
      : null;
    pushAssistant("已暂停当前 plan.md。请在输入框填写修改意见，提交后我会让你选择修改当前 Plan 或重新生成创意。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "plan_revision_requested",
          context: {
            ...makeSnapshot(targetConversationId),
            plan_approved: false,
            plan_revision_requested: true,
            pendingPlanRevisionRequest: planRevisionArtifactRef.current,
            pending_plan_revision_request: planRevisionArtifactRef.current,
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  };

  const handleConfirmPlanRevisionMode = async (mode: PlanRevisionMode) => {
    const pending = pendingPlanRevisionChoice;
    if (!pending) return;
    const { artifact, conversationId: targetConversationId, feedback, materials } = pending;
    if (!isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection || !artifact.plan) return;
    setPendingPlanRevisionChoice(null);
    setBusyForConversation(targetConversationId, true);
    try {
      const existingPlanJob = pendingPlanJobRef.current;
      if (
        existingPlanJob?.job_id
        && existingPlanJob.conversation_id === targetConversationId
      ) {
        schedulePendingPlanJobPersistence(
          existingPlanJob,
          pendingPlanJobRunningPhase(existingPlanJob.kind),
          pendingPlanJobPersistenceContext(existingPlanJob.kind),
        );
        await resumePendingPlanJob(existingPlanJob);
        return;
      }
      if (mode === "regenerate_directions") {
        pushAssistant("已选择放弃当前创意，正在重新生成 3 个创意方向…", targetConversationId);
        await startDirectionJob(
          targetConversationId,
          {
            intent: artifact.intent,
            values: artifact.formValues,
            materials,
            product_creative_profile: { revision_feedback: feedback },
            intake_context: artifact.intakeContext,
          },
          {
            intent: artifact.intent,
            formValues: artifact.formValues,
            materials,
            coreMessage: `${artifact.coreMessage || pendingCore}\n修改意见：${feedback}`,
            intakeContext: artifact.intakeContext,
            revisionFeedback: feedback,
          },
          "directions_running",
          pending.sourceMessageId,
        );
        return;
      }

      pushAssistant(`正在当前创意基础上修订 plan.md v${artifact.plan.plan_version || 1}…`, targetConversationId);
      const request: PlanRevisionJobRequest = {
        intent: artifact.intent,
        form_values: artifact.formValues,
        selected_direction: artifact.selectedDirection as unknown as Record<string, unknown>,
        current_plan_markdown: artifact.plan.plan_markdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        revision_feedback: feedback,
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
        product_creative_profile: { revision_feedback: feedback },
        intake_context: artifact.intakeContext,
        materials,
      };
      const started = await api.startPlanRevisionJob(request);
      const pendingPlanJob: PendingPlanJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: pending.sourceMessageId,
        kind: "plan_revision",
        started_at: new Date().toISOString(),
        request,
        context: {
          intent: artifact.intent,
          formValues: artifact.formValues,
          selectedDirection: artifact.selectedDirection,
          materials,
          coreMessage: artifact.coreMessage || pendingCore,
          intakeContext: artifact.intakeContext,
          processedKey: pending.processedKey,
        },
      };
      const persistenceContext = {
        pendingPlanRevisionChoice: null,
        pending_plan_revision_choice: null,
      };
      await continueStartedPlanJob({
        pendingPlanJob,
        saveRecovery: (job) => {
          pendingPlanJobRef.current = job;
          savePendingPlanJobRecovery(browserSessionStorage(), job);
        },
        persistPending: (job) => persistPendingPlanJob(
          job,
          targetConversationId,
          "plan_revision_running",
          persistenceContext,
        ),
        notifyRecovery: notifyPlanJobPersistenceRecovery,
        schedulePersistenceRetry: (job) => schedulePendingPlanJobPersistence(
          job,
          "plan_revision_running",
          persistenceContext,
        ),
        resumePending: resumePendingPlanJob,
      });
    } catch (err) {
      if (pending.processedKey) releaseArtifactAction(pending.processedKey);
      pushAssistant(`plan.md 修改失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleCancelPlanRevisionMode = () => {
    const pending = pendingPlanRevisionChoice;
    if (!pending) return;
    setPendingPlanRevisionChoice(null);
    setAgentRevisionSourceMessageId("");
    if (pending.processedKey) releaseArtifactAction(pending.processedKey);
    pushAssistant("已取消本次 plan.md 修改方式选择，当前 Plan 保持不变。", pending.conversationId);
    void updateConversationWithProgress(pending.conversationId, {
        last_phase: "plan_review",
        context: {
          ...makeSnapshot(pending.conversationId),
          pendingPlanRevisionChoice: null,
          pending_plan_revision_choice: null,
        } as unknown as Record<string, unknown>,
      })
      .catch(() => {});
  };

  const handleRollbackPlan = async (msg: ChatMessage, version: number) => {
    const artifact = msg.artifact;
    if (!artifact?.plan || !isCreationIntent(artifact.intent) || !artifact.formValues || !artifact.selectedDirection) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const rollbackSnapshot = makeSnapshot(targetConversationId);
    setBusyForConversation(targetConversationId, true);
    pushAssistant(
      `正在把 plan.md v${artifact.plan.plan_version || 1} 直接回退到 v${version}，不会创建新版本…`,
      targetConversationId,
    );
    try {
      const plan = await api.restorePlanMarkdown({
        intent: artifact.intent,
        current_plan_markdown: artifact.plan.plan_markdown,
        current_plan_version: artifact.plan.plan_version || 1,
        plan_history: artifact.plan.plan_history || [],
        restore_version: version,
        creation_contract: artifact.plan.creation_contract || artifact.creationContract || {},
        scene_durations_sec: artifact.plan.scene_durations_sec || [],
        scene_blueprints: artifact.plan.scene_blueprints || [],
        asset_manifest: artifact.plan.asset_manifest,
      });
      await persistPlanArtifactForConversation(
        createPlanArtifactMessage(
          plan,
          artifact.selectedDirection,
          {
            intent: artifact.intent,
            formValues: artifact.formValues,
            materials: artifact.materials || [],
            coreMessage: artifact.coreMessage || pendingCore,
            intakeContext: artifact.intakeContext,
          },
          targetConversationId,
        ),
        targetConversationId,
        {
          type: "plan_save",
          last_phase: "plan_review",
          processed_key: processedKey,
          success_message: `已回退到 plan.md v${plan.plan_version}，未创建新版本。`,
          context: rollbackSnapshot as unknown as Record<string, unknown>,
        },
      );
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`plan.md 回退失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleGenerateImage = async (msg: ChatMessage) => {
    const imagePrepare = msg.artifact?.imagePrepare;
    if (!imagePrepare || !imagePrepare.ok) return;
    const artifact = msg.artifact;
    if (!artifact) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`正在调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const request: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
        conversation_id: targetConversationId,
      };
      const started = await api.startImageGenerationJob(request);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_generation",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request,
        artifact,
        imagePrepare,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_generation_running", {
        image_prepare: imagePrepare,
      });
      await resumePendingImageJob(pendingImageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  const handleRetryImageResult = async (msg: ChatMessage) => {
    const artifact = msg.artifact;
    if (!artifact?.imageResult || canAcceptImageResult(artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storedImageEditRequest = (artifact.imageEditRequest || {}) as Partial<PendingImageEditRequest>;
    const sceneGlobalAssetReference = storedImageEditRequest.sceneGlobalAssetReference
      || sceneGlobalAssetReferenceFromMaterials(artifact.materials || []);
    if (sceneGlobalAssetReference) {
      const processedKey = beginArtifactAction(msg, targetConversationId);
      if (!processedKey) return;
      releaseArtifactAction(processedKey);
      await pushSceneGlobalAssetEditOptions(
        sceneGlobalAssetReference,
        String(storedImageEditRequest.prompt || artifact.imageRevisionFeedback || "图片编辑"),
        targetConversationId,
        (storedImageEditRequest.materials || artifact.materials || []) as Array<Record<string, unknown>>,
      );
      return;
    }
    const imagePrepare = artifact.imagePrepare;
    if (!imagePrepare) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    if (imagePrepare.method === "image_edit") {
      const imageEditRequest = imageEditRequestFromArtifact(artifact, targetConversationId);
      pendingImageEditRequestRef.current = imageEditRequest;
      releaseArtifactAction(processedKey);
      await showImageEditOptions(imageEditRequest);
      return;
    }
    setBusyForConversation(targetConversationId, true);
    pushAssistant(`已继续调用 ${imagePrepare.endpoint} 生成图片…`, targetConversationId);
    try {
      const request: ImageGenerationJobRequest = {
        method: imagePrepare.method,
        prompt: imagePrepare.prompt,
        negative_prompt: imagePrepare.negative_prompt,
        params: imagePrepare.params,
        conversation_id: targetConversationId,
      };
      const started = await api.startImageGenerationJob(request);
      const pendingImageJob: PendingImageJob = {
        job_id: started.job_id,
        conversation_id: targetConversationId,
        source_message_id: msg.id,
        kind: "image_regeneration",
        job_api: "generate",
        started_at: new Date().toISOString(),
        request,
        artifact,
        imagePrepare,
      };
      await persistPendingImageJob(pendingImageJob, targetConversationId, "image_regeneration_running", {
        image_prepare: imagePrepare,
      });
      await resumePendingImageJob(pendingImageJob, processedKey);
    } catch (err) {
      releaseArtifactAction(processedKey);
      pushAssistant(`图片继续生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
    } finally {
      setBusyForConversation(targetConversationId, false);
    }
  };

  async function handleAcceptImageResult(msg: ChatMessage, auto = false) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (auto && !shouldAutoAcceptImageResult(msg, targetConversationId)) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    imageRevisionArtifactRef.current = null;
    const sceneAssetReview = msg.artifact.sceneGlobalAssetEditReview;
    if (sceneAssetReview) {
      await startSceneGlobalAssetRevision(
        {
          source: "scene_global_asset",
          asset_id: sceneAssetReview.asset_id,
          asset_group: sceneAssetReview.asset_group,
          name: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          source_image_url: sceneAssetReview.source_image_url,
          url: sceneAssetReview.source_image_url,
          type: "image",
          filename: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          storyboard_message_id: sceneAssetReview.storyboard_message_id || sceneAssetReview.source_message_id,
        },
        "replace",
        {
          source: "image_asset",
          displayImageUrl: sceneAssetReview.edited_image_url,
          generationReferenceUrl: sceneAssetReview.edited_image_url,
          assetName: sceneAssetReview.asset_name,
          raw: sceneAssetReview.editResult as unknown as Record<string, unknown>,
        },
        {
          processedKey,
          reviewMessageId: msg.id,
          targetConversationId,
        },
      );
      return;
    }
    markImageResultAccepted(msg.id, targetConversationId);
    pushAssistant(auto ? timeoutReviewMessage(AUTO_CONFIRM_TIMEOUT_SECONDS) : "已确认图片满意，流程结束。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "image_accepted",
          context: { ...makeSnapshot(), image_accepted: true } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  function handleReviseImageResult(msg: ChatMessage) {
    if (!msg.artifact?.imageResult || !canAcceptImageResult(msg.artifact.imageResult)) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const sceneAssetReview = msg.artifact.sceneGlobalAssetEditReview;
    if (!sceneAssetReview && isReviewExpired(msg.artifact.reviewExpiresAt)) {
      void handleAcceptImageResult(msg, true);
      return;
    }
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const sceneGlobalAssetReference = sceneAssetReview
      ? {
          source: "scene_global_asset" as const,
          asset_id: sceneAssetReview.asset_id,
          asset_group: sceneAssetReview.asset_group,
          scene_global_asset_action: "edit" as const,
          name: sceneAssetReview.asset_name || sceneAssetReview.asset_id,
          source_image_url: sceneAssetReview.edited_image_url,
          original_image_url: sceneAssetReview.original_image_url,
          url: sceneAssetReview.edited_image_url,
          type: "image" as const,
          filename: `${sceneAssetReview.asset_id}.png`,
          storyboard_message_id: sceneAssetReview.storyboard_message_id || sceneAssetReview.source_message_id,
        }
      : sceneGlobalAssetReferenceFromMaterials(msg.artifact.materials || []);
    const revisionArtifact = sceneGlobalAssetReference
      ? {
          ...msg.artifact,
          materials: [
            sceneGlobalAssetReference,
            ...(msg.artifact.materials || []).filter((material) => material.source !== "scene_global_asset"),
          ],
        }
      : msg.artifact;
    imageRevisionArtifactRef.current = { conversationId: targetConversationId, artifact: revisionArtifact };
    pushAssistant(
      sceneGlobalAssetReference
        ? `请在输入框填写「${sceneGlobalAssetReference.name}」的图片修改意见，我会继续编辑这张全局素材并替换回场景包。`
        : "请在输入框填写图片修改意见，我会基于当前 plan.md 和图片参数重新生成。",
      targetConversationId,
    );
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: sceneGlobalAssetReference ? "scene_global_asset_revision_requested" : "image_revision_requested",
          context: {
            ...makeSnapshot(),
            image_revision_requested: true,
            scene_global_asset_revision_requested: Boolean(sceneGlobalAssetReference),
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  const handleGenerateJianyingDraft = async (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const sourceMessageId = msg.artifact?.type === "jianying_draft" ? msg.artifact.pendingJianyingDraftJob?.source_message_id : msg.id;
    const latestMessage = messagesRef.current.find(
      (message) => message.id === sourceMessageId && messageConversationId(message, targetConversationId) === targetConversationId,
    ) || msg;
    const artifact = latestMessage.artifact;
    if (!artifact?.mergedVideo?.ok || !artifact.generatedSceneVideos?.ok || !artifact.videoScenePackages?.ok) return;
    if (!targetConversationId) return;

    const generatedSceneVideos = artifact.generatedSceneVideos;
    const expectedScenePackages = artifact.videoScenePackages.scene_packages;
    const successfulScenes: JianyingDraftScene[] = generatedSceneVideos.scene_videos.map((scene) => ({
      scene_id: scene.scene_id,
      scene_index: scene.scene_index,
      task_id: scene.task_id || null,
      video_url: scene.video_url,
    }));
    const expectedSceneIds = new Set(expectedScenePackages.map((scene) => scene.scene_id));
    const successfulSceneIds = new Set(successfulScenes.map((scene) => scene.scene_id));
    if (
      generatedSceneVideos.failed_scenes.length > 0 ||
      successfulScenes.length !== expectedScenePackages.length ||
      successfulSceneIds.size !== expectedSceneIds.size ||
      [...expectedSceneIds].some((sceneId) => !successfulSceneIds.has(sceneId))
    ) {
      pushAssistant("当前版本存在未成功生成的分镜，无法生成剪映草稿。", targetConversationId);
      return;
    }

    let storyboard_version_id: string;
    try {
      storyboard_version_id = storyboardVersionId(successfulScenes);
    } catch {
      pushAssistant("当前分镜视频不符合剪映草稿输入要求，请检查分镜视频后重试。", targetConversationId);
      return;
    }
    const existingPending = pendingJianyingDraftJobRef.current;
    if (existingPending?.conversation_id === targetConversationId && existingPending.storyboard_version_id === storyboard_version_id) {
      void resumePendingJianyingDraftJob(existingPending);
      return;
    }
    const existingRecord = jianyingDraftRecordsForConversation(targetConversationId)[storyboard_version_id];
    if (isJianyingDraftSucceededResultValid(existingRecord)) return;
    // 失败/超时重试语义保留给后续原生 Tool；旧 Job HTTP 已删除。
    const retry_failed = existingRecord?.status === "failed" || existingRecord?.status === "timeout";
    if (!jianyingDraftStartGuardRef.current.tryAcquire(targetConversationId, storyboard_version_id)) return;
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      pushAssistant(
        retry_failed
          ? `${LEGACY_VIDEO_JOB_CONTINUE_TIP}（失败草稿请在对话中让 Agent 按 retry_failed 语义继续）`
          : LEGACY_VIDEO_JOB_CONTINUE_TIP,
        targetConversationId,
      );
      jianyingDraftStartGuardRef.current.release(targetConversationId, storyboard_version_id);
      return;
    }
  };

  const handleSaveVideoScenePackage = async (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const latestMessage = messagesRef.current.find(
      (message) =>
        message.id === msg.id &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        Boolean(message.artifact?.videoScenePackages),
    );
    const artifact = latestMessage?.artifact;
    const videoScenePackages = artifact?.videoScenePackages;
    if (!latestMessage || !artifact || !videoScenePackages) {
      pushAssistant("当前没有找到可保存的分镜场景包，请重新打开后再试。", targetConversationId);
      return;
    }
    try {
      if (targetConversationId) {
        await api.updateConversationMessage(targetConversationId, latestMessage.id, {
          content: latestMessage.content,
          payload: {
            artifact,
            materials: artifact.materials || [],
            client_message_id: latestMessage.id,
          } as unknown as Record<string, unknown>,
        });
      }
      persistScenePackageSnapshot(targetConversationId, videoScenePackages, "scene_package_saved", {
        video_scene_package_edited_scene_ids: artifact.videoScenePackageEditedSceneIds || [],
      });
      setCanvasOpen(false);
      setSelectedStoryboardMessageId("");
      setSelectedPlanEditorMessageId("");
    } catch {
      pushAssistant("分镜保存失败，请检查网络后重试。", targetConversationId);
    }
  };

  const handleGenerateVideoFromScenePackages = async (
    msg: ChatMessage,
    options?: { sceneId?: string },
  ) => {
    const latestMessage =
      messagesRef.current.find(
        (message) => message.id === msg.id && message.artifact?.videoScenePackages,
      ) || msg;
    const artifact = latestMessage.artifact;
    if (!artifact) return;
    if (artifact.sceneAssetsGenerating) {
      pushAssistant(
        "参考图仍在生成中，请稍候。可先打开卡片查看场景包结构。",
        messageConversationId(latestMessage, conversationIdRef.current),
      );
      return;
    }
    if (artifact.sceneAssetsAwaitingModel) {
      pushAssistant(
        "场景包结构已就绪。请在对话中选择生图模型并确认，确认后开始生成参考图。",
        messageConversationId(latestMessage, conversationIdRef.current),
      );
      return;
    }
    const videoScenePackages = artifact.videoScenePackages;
    if (!videoScenePackages?.ok || videoScenePackages.scene_packages.length === 0) return;
    const targetConversationId = messageConversationId(latestMessage, conversationIdRef.current);
    const targetSceneId = String(options?.sceneId || "").trim();
    const targetScene = targetSceneId
      ? videoScenePackages.scene_packages.find((scene) => scene.scene_id === targetSceneId)
      : undefined;
    if (targetSceneId && !targetScene) {
      pushAssistant(`未找到分镜 ${targetSceneId}，无法单独生成。`, targetConversationId);
      return;
    }
    const dirtySceneIds = new Set(artifact.videoScenePackageEditedSceneIds || []);
    const retrySceneIds = failedSceneIdsFromGeneratedSceneVideos(artifact.generatedSceneVideos, videoScenePackages.scene_packages);
    const isSingleSceneGeneration = Boolean(targetSceneId);
    const isDirtySceneRegeneration = !isSingleSceneGeneration
      && canReuseUneditedSceneVideos(videoScenePackages, artifact.generatedSceneVideos, dirtySceneIds);
    const hasGeneratedSceneVideos = Boolean(artifact.generatedSceneVideos?.scene_videos.length);
    const isFailedSceneRetry = !isSingleSceneGeneration
      && Boolean(artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && retrySceneIds.size > 0);
    if (
      !isSingleSceneGeneration
      && hasGeneratedSceneVideos
      && dirtySceneIds.size === 0
      && !isFailedSceneRetry
    ) {
      pushAssistant("当前分镜没有检测到修改内容，无需重新生成视频。", targetConversationId);
      return;
    }
    if (
      !isSingleSceneGeneration
      && artifact.generatedSceneVideos
      && !artifact.generatedSceneVideos.ok
      && retrySceneIds.size === 0
    ) {
      pushAssistant("当前失败结果没有定位到具体分镜，无法只重试异常片段。请重新生成场景包后再试。", targetConversationId);
      return;
    }
    const processedKey = beginArtifactAction(
      latestMessage,
      targetConversationId,
      isSingleSceneGeneration ? `generate_scene:${targetSceneId}` : undefined,
    );
    if (!processedKey) return;
    // V2 单镜：不占会话 busy，允许分镜 5 生成中继续点分镜 6/7。
    const lockConversationBusy = !(
      orchestrationModeRef.current === "video_agent_v2" && isSingleSceneGeneration
    );
    if (lockConversationBusy) {
      setBusyForConversation(targetConversationId, true);
    }
    // V2.1 批次 C：分镜视频生成/脏镜重生成走 Turn → generate_scenes，禁止直接开 Job。
    // 禁止在 handleSupervisorTurn 前 pushAssistant「正在生成…」：该提示会先落库，
    // 刷新后按 created_at 排在用户 Turn 前面，看起来像「模型先说、用户后点」。
    if (orchestrationModeRef.current === "video_agent_v2") {
      try {
        const content = isSingleSceneGeneration
          ? `确认并生成分镜视频（${targetSceneId}）`
          : isDirtySceneRegeneration
            ? `重新生成已修改的分镜视频（${Array.from(dirtySceneIds).join("、")}）`
            : isFailedSceneRetry
              ? `继续生成失败的分镜视频（${Array.from(retrySceneIds).join("、")}）`
              : "确认并生成分镜视频";
        // 点按后立刻盖蒙版；Snapshot / generation_jobs 晚到时也不能闪回旧成片。
        const baselineRevision = videoAgentView.workspace?.revision || 0;
        if (isSingleSceneGeneration && targetSceneId) {
          optimisticGeneratingRevisionRef.current[targetSceneId] = baselineRevision;
          setOptimisticGeneratingSceneIds((prev) => (
            prev.includes(targetSceneId) ? prev : [...prev, targetSceneId]
          ));
        } else {
          const pendingIds = isDirtySceneRegeneration
            ? Array.from(dirtySceneIds)
            : isFailedSceneRetry
              ? Array.from(retrySceneIds)
              : videoScenePackages.scene_packages.map((scene) => String(scene.scene_id || "").trim()).filter(Boolean);
          if (pendingIds.length > 0) {
            for (const id of pendingIds) {
              optimisticGeneratingRevisionRef.current[id] = baselineRevision;
            }
            setOptimisticGeneratingSceneIds((prev) => {
              const next = new Set(prev);
              for (const id of pendingIds) next.add(id);
              return [...next];
            });
          }
        }
        await handleSupervisorTurn(
          {
            conversationId: targetConversationId,
            clientInputId: uid(),
            content,
            materials: [],
            replyToMessageId: null,
            artifactRefs: [],
            interruptId: null,
            explicitAction: null,
            continueLegacy: false,
            registrationStatus: "pending",
          },
          supervisorRuntime.getContextVersion() ?? 0,
        );
      } catch (err) {
        releaseArtifactAction(processedKey);
        if (isSingleSceneGeneration && targetSceneId) {
          delete optimisticGeneratingRevisionRef.current[targetSceneId];
          setOptimisticGeneratingSceneIds((prev) => prev.filter((id) => id !== targetSceneId));
        } else {
          optimisticGeneratingRevisionRef.current = {};
          setOptimisticGeneratingSceneIds([]);
        }
        pushAssistant(`分镜视频生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        if (lockConversationBusy) {
          setBusyForConversation(targetConversationId, false);
        }
      }
      return;
    }
    pushAssistant(
      isSingleSceneGeneration
        ? `正在生成分镜 ${targetScene?.scene_index || targetSceneId} 的视频…`
        : isDirtySceneRegeneration
          ? `已保存分镜修改，正在重生成 ${dirtySceneIds.size} 个已修改分镜视频…`
          : isFailedSceneRetry
            ? `正在重新生成 ${retrySceneIds.size} 个失败或额度暂停的分镜视频…`
            : "场景包已确认，正在生成场景视频…",
      targetConversationId,
    );
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      releaseArtifactAction(processedKey);
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      if (lockConversationBusy) {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
  };

  const handleRetryVideoMerge = async (msg: ChatMessage) => {
    const generatedSceneVideos = msg.artifact?.generatedSceneVideos;
    const videoScenePackages = msg.artifact?.videoScenePackages;
    if (!generatedSceneVideos?.scene_videos.length || !videoScenePackages || msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      releaseArtifactAction(processedKey);
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      setBusyForConversation(targetConversationId, false);
      return;
    }
  };

  async function handleAcceptVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (!targetConversationId) return;
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    const currentMessage = messagesRef.current.find(
      (message) =>
        message.id === msg.id &&
        messageConversationId(message, targetConversationId) === targetConversationId &&
        message.artifact?.type === "video_result",
    ) || msg;
    if (!currentMessage.artifact) {
      releaseArtifactAction(processedKey);
      return;
    }
    const acceptedArtifact: ChatArtifact = {
      ...currentMessage.artifact,
      videoAccepted: true,
    };
    try {
      await api.updateConversationMessage(targetConversationId, currentMessage.id, {
        content: currentMessage.content,
        payload: {
          artifact: acceptedArtifact,
          materials: currentMessage.materials || acceptedArtifact.materials || [],
          client_message_id: currentMessage.id,
        } as unknown as Record<string, unknown>,
      });
      videoRevisionArtifactRef.current = null;
      markVideoResultAccepted(currentMessage.id, targetConversationId);
      await updateConversationWithProgress(targetConversationId, {
          last_phase: "video_accepted",
          context: { ...makeSnapshot(), video_accepted: true, pendingVideoRevision: null, pending_video_revision: null } as unknown as Record<string, unknown>,
        });
      pushAssistant("已确认视频无修改意见，流程结束。", targetConversationId);
    } catch {
      releaseArtifactAction(processedKey);
      pushAssistant("视频结束状态保存失败，请检查网络后重试。", targetConversationId);
    }
  }

  function handleReviseVideoResult(msg: ChatMessage) {
    if (!msg.artifact?.mergedVideo?.ok || msg.artifact.videoAccepted) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    videoRevisionArtifactRef.current = {
      conversationId: targetConversationId,
      artifact: {
        ...msg.artifact,
        originalVideoScenePackages: msg.artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId),
      },
    };
    pushAssistant("请在输入框填写视频修改意见。我会先做 QAAgent QC 质检，再让你选择是否结合质检结果重生成受影响场景。", targetConversationId);
    if (targetConversationId) {
      void updateConversationWithProgress(targetConversationId, {
          last_phase: "video_revision_requested",
          context: {
            ...makeSnapshot(),
            video_revision_requested: true,
            video_revision_requested_at: new Date().toISOString(),
          } as unknown as Record<string, unknown>,
        })
        .catch(() => {});
    }
  }

  async function handleRegenerateVideoWithRevision(msg: ChatMessage, useQualityReview: boolean) {
    const artifact = msg.artifact;
    if (!artifact?.videoScenePackages || !artifact.generatedSceneVideos || !artifact.mergedVideo || !artifact.videoRevisionFeedback) return;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const processedKey = beginArtifactAction(msg, targetConversationId);
    if (!processedKey) return;
    setBusyForConversation(targetConversationId, true);
    const originalVideoScenePackages = artifact.originalVideoScenePackages || latestOriginalVideoScenePackagesForConversation(messagesRef.current, targetConversationId) || artifact.videoScenePackages;
    const affectedSceneIds = sceneIdsForRevision(
      artifact.videoScenePackages.scene_packages,
      artifact.videoRevisionFeedback,
      artifact.videoQualityReview,
      useQualityReview,
    );
    if (affectedSceneIds.size === 0) {
      releaseArtifactAction(processedKey);
      videoRevisionArtifactRef.current = {
        conversationId: targetConversationId,
        artifact: {
          ...artifact,
          originalVideoScenePackages,
        },
      };
      pushAssistant("QAAgent QC 质检没有定位到具体分镜。为了避免误把整条视频重做，请在修改意见里明确写出要修改的分镜，例如“只修改第2个分镜”。", targetConversationId);
      if (targetConversationId) {
        void updateConversationWithProgress(targetConversationId, {
            last_phase: "video_revision_scene_required",
            context: {
              ...makeSnapshot(),
              pendingVideoRevision: videoRevisionArtifactRef.current,
              pending_video_revision: videoRevisionArtifactRef.current,
              video_revision_feedback: artifact.videoRevisionFeedback,
              video_quality_review: artifact.videoQualityReview,
            } as unknown as Record<string, unknown>,
          })
          .catch(() => {});
      }
      setBusyForConversation(targetConversationId, false);
      return;
    }
    const affectedSceneLabel = formatSceneIndexesForMessage(artifact.videoScenePackages.scene_packages, affectedSceneIds);
    // V2.1 批次 C：QC/修改意见重生成走 Turn；勿在 Turn 前 pushAssistant，避免刷新后顺序颠倒。
    if (orchestrationModeRef.current === "video_agent_v2") {
      try {
        await handleSupervisorTurn(
          {
            conversationId: targetConversationId,
            clientInputId: uid(),
            content: (
              `按修改意见重生成分镜 ${Array.from(affectedSceneIds).join("、")}：`
              + `${artifact.videoRevisionFeedback || ""}`
            ).trim(),
            materials: [],
            replyToMessageId: null,
            artifactRefs: [],
            interruptId: null,
            explicitAction: null,
            continueLegacy: false,
            registrationStatus: "pending",
          },
          supervisorRuntime.getContextVersion() ?? 0,
        );
      } catch (err) {
        releaseArtifactAction(processedKey);
        pushAssistant(`分镜重生成失败:${err instanceof Error ? err.message : String(err)}`, targetConversationId);
      } finally {
        setBusyForConversation(targetConversationId, false);
      }
      return;
    }
    pushAssistant(`正在重生成 ${affectedSceneLabel}，并复用未受影响分镜…`, targetConversationId);
    if (LEGACY_VIDEO_JOB_HTTP_REMOVED) {
      releaseArtifactAction(processedKey);
      pushAssistant(LEGACY_VIDEO_JOB_CONTINUE_TIP, targetConversationId);
      setBusyForConversation(targetConversationId, false);
      return;
    }
  }

  const handleApprove = async () => {
    // 对应后端 /brief/confirm：恢复 LangGraph 的 Brief interrupt，批准后进入 GENERATE。
    pushAssistant("Brief 已确认,开始生成…");
    setBusy(true);
    briefConfirmedRef.current = true;
    setBriefConfirmed(true);
    try {
      await api.confirmBrief(taskIdRef.current, true);
      setCanvas((c) => ({ ...c, phase: "generate" }));
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleRevise = async () => {
    // 对应后端 /brief/revise：当前只写业务 brief/反馈/偏好，不会直接恢复 LangGraph run。
    const fb = "请优化分镜节奏与卖点表达";
    pushAssistant("已请求修改 Brief。");
    try {
      await api.reviseBrief(taskIdRef.current, {}, fb);
      briefConfirmedRef.current = false;
      setBriefConfirmed(false);
      setCanvas((c) => ({ ...c, phase: "brief_review" }));
    } catch (err) {
      pushAssistant(`修改失败:${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleConfirmStage = async (stage: "segments" | "edit" | "qc", approved: boolean) => {
    setBusy(true);
    try {
      const task = await api.confirmStage(taskIdRef.current, stage, approved);
      setCanvas((c) => ({ ...c, phase: (task.phase as TaskPhase) || c.phase }));
      pushAssistant(approved ? "已确认,继续下一步。" : "已退回,重新处理。");
    } catch (err) {
      pushAssistant(`确认失败:${err instanceof Error ? err.message : String(err)}`);
      setBusy(false);
    }
  };

  const handleOpenVideoResult = (msg: ChatMessage, video: VideoResult, results: VideoResult[]) => {
    setSelectedStoryboardMessageId("");
    setCanvasOpen(true);
    setCanvas((current) => ({
      ...current,
      phase: "done",
      results: results.length > 0 ? results : current.results,
      selectedVideo: video,
      selectedVideoSourceMessageId: video.assetType === "final_video" ? msg.id : undefined,
    }));
  };

  const handleDownloadPreviewVideo = (video: VideoResult, sourceMessageId?: string) => {
    if (video.assetType !== "final_video") return;
    const sourceMessage = sourceMessageId
      ? messagesRef.current.find((message) => message.id === sourceMessageId)
      : [...messagesRef.current].reverse().find((message) => message.artifact?.mergedVideo?.merged_video_url === video.url);
    if (
      sourceMessage
      && activeSupervisorVideoTarget?.ui.kind === "video_result_review"
      && activeSupervisorVideoMessage?.id === sourceMessage.id
      && video.url.startsWith("https://")
    ) {
      void submitSupervisorAction(
        "下载最终视频",
        buildSupervisorWorkflowAction({
          action: "continue_workflow",
          intent: "video",
          workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
          stage: activeSupervisorVideoTarget.stage,
          artifactRef: activeSupervisorVideoTarget.artifactRef,
          patch: { delivery_download_url: video.url },
        }),
        {
          artifactRefs: activeSupervisorVideoTarget.artifactRef
            ? [activeSupervisorVideoTarget.artifactRef]
            : [],
        },
      );
      return;
    }
    if (sourceMessage && legacyArtifactActionsEnabled) {
      void recordArtifactDownload(sourceMessage, video.url);
    }
  };

  useEffect(() => {
    if (
      restoringRef.current
      || !currentConversationId
      || !pageVisibleRef.current
      || orchestrationModeRef.current !== "frontend_v2"
    ) return;
    const visibleMessages = [...messages].reverse();
    const expiredImageResult = visibleMessages.find((message) => {
      if (messageConversationId(message, currentConversationId) !== currentConversationId) return false;
      if (message.artifact?.type !== "image_result" || !message.artifact.imageResult || message.artifact.imageAccepted) return false;
      if (hasPendingImageRevisionForResult(message, currentConversationId)) return false;
      return canAcceptImageResult(message.artifact.imageResult) && isReviewExpired(message.artifact.reviewExpiresAt);
    });
    if (expiredImageResult) {
      void handleAcceptImageResult(expiredImageResult, true);
    }
  }, [messages, currentConversationId]);

  const selectedStoryboardMessageRaw = selectedStoryboardMessageId
    ? messages.find((message) => message.id === selectedStoryboardMessageId && message.artifact?.videoScenePackages)
    : undefined;
  // V2：场景包打开预览时，把 Workspace 已回填的分镜视频合并进面板，避免仍只显示参考图。
  const selectedStoryboardMessage = useMemo(() => {
    if (!selectedStoryboardMessageRaw?.artifact?.videoScenePackages) {
      return selectedStoryboardMessageRaw;
    }
    if (orchestrationMode !== "video_agent_v2" || !videoAgentView.workspace) {
      return selectedStoryboardMessageRaw;
    }
    if (videoAgentView.workspace.conversationId !== currentConversationId) {
      return selectedStoryboardMessageRaw;
    }
    const existing = selectedStoryboardMessageRaw.artifact.generatedSceneVideos;
    const workspaceVideos = videoAgentView.workspace.scenes
      .map((scene) => {
        const videoUrl = scene.mediaUrl
          || scene.variants.find((variant) => variant.videoUrl)?.videoUrl
          || null;
        if (!videoUrl) return null;
        const packageScene = selectedStoryboardMessageRaw.artifact?.videoScenePackages?.scene_packages
          .find((item) => item.scene_id === scene.sceneId);
        return {
          scene_id: scene.sceneId,
          scene_index: scene.sceneIndex,
          duration_ms: Number(packageScene?.duration_ms) || 5_000,
          video_url: videoUrl,
        };
      })
      .filter((item): item is NonNullable<typeof item> => item !== null)
      .sort((left, right) => left.scene_index - right.scene_index);
    // Workspace 与消息已有成片按 scene_id 合并；避免单镜重生时面板短暂退回参考图。
    const mergedBySceneId = new Map<string, (typeof workspaceVideos)[number]>();
    for (const scene of existing?.scene_videos || []) {
      const sceneId = String(scene.scene_id || "").trim();
      const videoUrl = String(scene.video_url || "").trim();
      if (!sceneId || !videoUrl) continue;
      mergedBySceneId.set(sceneId, {
        scene_id: sceneId,
        scene_index: Number(scene.scene_index) || 0,
        duration_ms: Number(scene.duration_ms) || 5_000,
        video_url: videoUrl,
      });
    }
    for (const scene of workspaceVideos) {
      mergedBySceneId.set(scene.scene_id, scene);
    }
    const mergedVideos = [...mergedBySceneId.values()]
      .sort((left, right) => left.scene_index - right.scene_index);
    if (mergedVideos.length === 0) return selectedStoryboardMessageRaw;
    const existingUrls = new Set((existing?.scene_videos || []).map((item) => item.video_url).filter(Boolean));
    const needsMerge = mergedVideos.length !== (existing?.scene_videos || []).length
      || mergedVideos.some((item) => !existingUrls.has(item.video_url));
    if (!needsMerge && existing?.scene_videos?.length) return selectedStoryboardMessageRaw;
    return {
      ...selectedStoryboardMessageRaw,
      artifact: {
        ...selectedStoryboardMessageRaw.artifact,
        generatedSceneVideos: {
          ok: Boolean(existing?.ok) && (existing?.failed_scenes?.length || 0) === 0,
          endpoint: existing?.endpoint || "generate_scenes",
          scene_videos: mergedVideos,
          failed_scenes: existing?.failed_scenes || [],
          message: existing?.message || "分镜视频已回填到场景包预览。",
        },
      },
    };
  }, [
    selectedStoryboardMessageRaw,
    orchestrationMode,
    videoAgentView.workspace,
    currentConversationId,
  ]);
  // 分镜面板：按镜标「视频生成中」蒙版（Workspace generation_jobs / edit_status）。
  const storyboardGeneratingSceneIds = useMemo(() => {
    const ids = new Set<string>();
    const workspace = videoAgentView.workspace;
    const artifact = selectedStoryboardMessage?.artifact;
    const progress = workspace?.sceneVideoProgress;
    const progressIncomplete = Boolean(
      progress
      && progress.total > 0
      && progress.completed < progress.total,
    );
    if (
      orchestrationMode === "video_agent_v2"
      && workspace
      && workspace.conversationId === currentConversationId
    ) {
      for (const scene of workspace.scenes || []) {
        const busy = (scene.generationJobStatuses || []).some((status) => (
          ["polling", "created", "running", "start_paused_quota"].includes(
            String(status || "").toLowerCase(),
          )
        ));
        // polling 中：即使仍有旧成片也要蒙版（单镜重生场景）。
        if (busy) {
          ids.add(scene.sceneId);
          continue;
        }
        // 启动后、job 状态尚未投影前：edit_status=重新生成中 且本批仍在进行。
        if (
          scene.editStatus === "重新生成中"
          && (progressIncomplete || Boolean(artifact?.sceneVideosGenerating))
        ) {
          ids.add(scene.sceneId);
        }
      }
      if (progressIncomplete && progress?.sceneId) {
        // 单镜重生启动时后端会写 scene_id；有旧成片也必须蒙该镜。
        ids.add(progress.sceneId);
      }
    }
    // 点击后乐观蒙版，覆盖 Snapshot 到达前的空窗。
    for (const sceneId of optimisticGeneratingSceneIds) {
      if (sceneId) ids.add(sceneId);
    }
    // Workspace 尚未写入 jobs 时，用场景包卡 generating + progress.sceneId 兜底。
    if (ids.size === 0 && artifact?.sceneVideosGenerating && progress?.sceneId) {
      ids.add(progress.sceneId);
    }
    return [...ids];
  }, [
    orchestrationMode,
    currentConversationId,
    videoAgentView.workspace,
    selectedStoryboardMessage?.artifact?.sceneVideosGenerating,
    optimisticGeneratingSceneIds,
  ]);

  // 乐观蒙版：Workspace revision 推进且对应镜 job 终态后清掉。
  useEffect(() => {
    if (optimisticGeneratingSceneIds.length === 0) return;
    const workspace = videoAgentView.workspace;
    if (!workspace || workspace.conversationId !== currentConversationId) {
      optimisticGeneratingRevisionRef.current = {};
      setOptimisticGeneratingSceneIds([]);
      return;
    }
    const currentRevision = workspace.revision;
    setOptimisticGeneratingSceneIds((prev) => {
      const next = prev.filter((sceneId) => {
        const baseline = optimisticGeneratingRevisionRef.current[sceneId];
        // revision 尚未因本轮 generate_scenes 推进：保留乐观蒙版。
        if (typeof baseline === "number" && currentRevision <= baseline) {
          return true;
        }
        const scene = (workspace.scenes || []).find((item) => item.sceneId === sceneId);
        if (!scene) return true;
        const busy = (scene.generationJobStatuses || []).some((status) => (
          ["polling", "created", "running", "start_paused_quota"].includes(
            String(status || "").toLowerCase(),
          )
        ));
        if (busy || scene.editStatus === "重新生成中") return true;
        delete optimisticGeneratingRevisionRef.current[sceneId];
        return false;
      });
      if (
        next.length === prev.length
        && next.every((sceneId, index) => sceneId === prev[index])
      ) {
        return prev;
      }
      return next;
    });
  }, [
    currentConversationId,
    optimisticGeneratingSceneIds.length,
    videoAgentView.workspace,
    videoAgentView.workspace?.revision,
    videoAgentView.workspace?.scenes,
  ]);
  const selectedPlanEditorMessage = selectedPlanEditorMessageId
    ? messages.find((message) => message.id === selectedPlanEditorMessageId && message.artifact?.type === "plan" && message.artifact.plan)
    : undefined;
  const derivedWorkflowTaskBoard = deriveWorkflowTaskBoard({
    progress: workflowProgress,
    messages,
  });
  const agentPlanTaskBoard = deriveWorkflowTaskBoardFromAgentPlan(
    supervisorRuntime.state.videoAgentPlan,
  );
  // Video Agent 有自主 Plan 时只展示 Plan；V2 不再回退 Workflow 影子阶段板。
  const workflowTaskBoard = orchestrationMode === "video_agent_v2"
    ? (agentPlanTaskBoard
      ? {
          ...agentPlanTaskBoard,
          workflowId: `${currentConversationId}:${agentPlanTaskBoard.workflowId}`,
        }
      : null)
    : (
      (runtimePolicy.legacyRunnerEnabled || runtimePolicy.supervisorEnabled)
      && (agentPlanTaskBoard || derivedWorkflowTaskBoard)
        ? {
            ...(agentPlanTaskBoard || derivedWorkflowTaskBoard)!,
            workflowId: `${currentConversationId}:${(agentPlanTaskBoard || derivedWorkflowTaskBoard)!.workflowId}`,
          }
        : null
    );
  const legacyArtifactActionsEnabled = runtimePolicy.legacyArtifactActionsEnabled;

  const jianyingDraftVersionForMessage = (msg: ChatMessage): string => {
    if (msg.artifact?.type === "jianying_draft") return msg.artifact.pendingJianyingDraftJob?.storyboard_version_id || "";
    const scenes = (msg.artifact?.generatedSceneVideos?.scene_videos || []).map((scene) => ({
      scene_id: scene.scene_id,
      scene_index: scene.scene_index,
      task_id: scene.task_id || null,
      video_url: scene.video_url,
    }));
    try {
      return scenes.length > 0 ? storyboardVersionId(scenes) : "";
    } catch {
      return "";
    }
  };

  const getJianyingDraftResult = (msg: ChatMessage): JianyingDraftJobResponse | null => {
    if (msg.artifact?.jianyingDraft) return msg.artifact.jianyingDraft;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storyboardVersionId = jianyingDraftVersionForMessage(msg);
    return storyboardVersionId ? jianyingDraftRecordsForConversation(targetConversationId)[storyboardVersionId] || null : null;
  };

  const isJianyingDraftRunning = (msg: ChatMessage): boolean => {
    if (msg.artifact?.pendingJianyingDraftJob) return true;
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    const storyboardVersionId = jianyingDraftVersionForMessage(msg);
    const pending = pendingJianyingDraftJobRef.current;
    return Boolean(
      storyboardVersionId && pending?.conversation_id === targetConversationId && pending.storyboard_version_id === storyboardVersionId,
    );
  };

  const handleDownloadJianyingDraft = (msg: ChatMessage) => {
    const targetConversationId = messageConversationId(msg, conversationIdRef.current);
    if (targetConversationId) pushAssistant("剪映草稿已开始下载。", targetConversationId);
  };

  function renderSupervisorVideoArtifact(target: SupervisorVideoTarget) {
    const currentMessage = activeSupervisorVideoMessage;
    if (!currentMessage) return null;
    const acceptsMessage = (msg: ChatMessage): boolean => Boolean(
      msg.id === currentMessage.id
      && messageConversationId(msg, currentConversationId) === currentConversationId,
    );
    const submit = (
      content: string,
      request: {
        action: AgentAction;
        patch?: Readonly<Record<string, unknown>>;
      },
    ): Promise<void> => {
      return submitSupervisorAction(
        content,
        buildSupervisorWorkflowAction({
          action: request.action,
          intent: "video",
          workflowId: target.workflow.workflow_id,
          stage: target.stage,
          artifactRef: target.artifactRef,
          patch: request.patch || {},
        }),
        {
          artifactRefs: target.artifactRef ? [target.artifactRef] : [],
        },
      );
    };
    const replacementPatch = (replacement: SceneGlobalAssetReplacement): Record<string, unknown> => ({
      source: replacement.source,
      display_image_url: replacement.displayImageUrl,
      generation_reference_url: replacement.generationReferenceUrl,
      ...(replacement.thirdAssetId ? { third_asset_id: replacement.thirdAssetId } : {}),
      ...(replacement.assetType ? { asset_type: replacement.assetType } : {}),
      ...(replacement.contentAssetId ? { content_asset_id: replacement.contentAssetId } : {}),
      ...(replacement.assetName ? { asset_name: replacement.assetName } : {}),
    });
    const failedSceneIds = (msg: ChatMessage): string[] => Array.from(new Set(
      (msg.artifact?.generatedSceneVideos?.failed_scenes || [])
        .map((item) => typeof item.scene_id === "string" ? item.scene_id.trim() : "")
        .filter(Boolean),
    ));
    const submitSceneReviewDecision = (msg: ChatMessage): void => {
      if (!acceptsMessage(msg)) return;
      const failedIds = failedSceneIds(msg);
      if (failedIds.length > 0) {
        submit("重试失败的分镜视频", {
          action: "retry_failed",
          patch: { scene_ids: failedIds },
        });
        return;
      }
      if ((msg.artifact?.videoScenePackageEditedSceneIds || []).length > 0) {
        submit("重新生成已修改的分镜视频", { action: "regenerate_stage" });
        return;
      }
      submit("确认当前视频阶段", { action: "continue_workflow" });
    };
    const submitRevision = (msg: ChatMessage, useQualityReview: boolean): void => {
      if (!acceptsMessage(msg)) return;
      const packages = msg.artifact?.videoScenePackages;
      const scenes = packages?.scene_packages as ScenePackageRecord[] | undefined;
      if (!packages || !scenes?.length) return;
      const requested = window.prompt(
        useQualityReview ? "可补充修改意见；留空则只按质检结果修改。" : "请输入本次视频修改意见。",
        msg.artifact?.videoRevisionFeedback || "",
      );
      if (requested === null) return;
      const feedback = requested.trim();
      const qualityReview = useQualityReview ? msg.artifact?.videoQualityReview : undefined;
      const revisionFeedback = feedback || qualityReview?.revision_prompt?.trim() || "";
      if (!revisionFeedback) return;
      const affectedIds = sceneIdsForRevision(
        scenes,
        revisionFeedback,
        qualityReview,
        useQualityReview,
      );
      const revisedScenes = scenePackagesWithRevisionContract(
        scenes,
        affectedIds,
        revisionFeedback,
        qualityReview,
        packages.global_assets,
        msg.artifact?.originalVideoScenePackages?.scene_packages as ScenePackageRecord[] | undefined,
      );
      const scene_patches = Object.fromEntries(
        revisedScenes
          .filter((scene) => affectedIds.has(scene.scene_id))
          .map((scene) => [scene.scene_id, {
            storyline: scene.storyline || "",
            shot_description: scene.shot_description || {},
            narration: scene.narration || "",
            reference_asset_ids: scene.reference_asset_ids || [],
          }]),
      );
      if (Object.keys(scene_patches).length === 0) return;
      submit("按修改范围重新生成分镜", {
        action: "modify_workflow",
        patch: { scene_patches },
      });
    };

    return {
      message: currentMessage,
      onSelectDirection: (msg: ChatMessage, direction: CreativeDirectionResponse) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_direction_review") return;
        submit(`选择创意方向：${direction.title}`, {
          action: "continue_workflow",
          patch: { direction_id: direction.direction_id },
        });
      },
      onRegenerateDirections: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_direction_review") return;
        submit("重新生成视频创意方向", { action: "regenerate_stage" });
      },
      onApprovePlan: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit("同意当前视频创作方案", { action: "continue_workflow" });
      },
      onRevisePlan: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        const feedback = window.prompt("请输入方案修改意见。")?.trim();
        if (!feedback) return;
        submit("修改当前视频创作方案", {
          action: "modify_workflow",
          patch: { revision_feedback: feedback },
        });
      },
      onRollbackPlan: (msg: ChatMessage, version: number) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit(`恢复视频方案 v${version}`, {
          action: "modify_workflow",
          patch: { plan_version: version },
        });
      },
      onRegeneratePlanDirections: (msg: ChatMessage) => {
        if (!acceptsMessage(msg) || target.ui.kind !== "video_plan_review") return;
        submit("返回创意方向并生成新创意", { action: "regenerate_stage" });
      },
      onGenerateVideoFromScenePackages: submitSceneReviewDecision,
      onRetrySceneAssets: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("继续生成视频场景素材", { action: "continue_workflow" });
      },
      onRetryVideoMerge: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("重试视频合并", { action: "retry_failed" });
      },
      onAcceptVideoResult: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        submit("确认最终视频", { action: "continue_workflow" });
      },
      onReviseVideoResult: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const feedback = window.prompt("请输入视频修改意见。")?.trim();
        if (!feedback) return;
        submit("提交视频修改意见并启动质检", {
          action: "modify_workflow",
          patch: { user_feedback: feedback },
        });
      },
      onRegenerateVideoWithRevision: submitRevision,
      onGenerateJianyingDraft: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const result = getJianyingDraftResult(msg);
        if (result?.status === "failed" || result?.status === "timeout") {
          submit("重新生成剪映草稿", {
            action: "retry_failed",
            patch: { jianying_action: "start" },
          });
          return;
        }
        submit("生成剪映草稿", {
          action: "modify_workflow",
          patch: { jianying_action: "start" },
        });
      },
      onDownloadJianyingDraft: (msg: ChatMessage) => {
        if (!acceptsMessage(msg)) return;
        const result = getJianyingDraftResult(msg);
        if (!result?.storyboard_version_id || !result.download_url?.startsWith("https://")) return;
        submit("下载剪映草稿", {
          action: "continue_workflow",
          patch: {
            jianying_action: "download",
            storyboard_version_id: result.storyboard_version_id,
            download_url: result.download_url,
          },
        });
      },
      onDownloadArtifact: (msg: ChatMessage, url: string) => {
        if (!acceptsMessage(msg) || !url.startsWith("https://")) return;
        submit("下载最终视频", {
          action: "continue_workflow",
          patch: { delivery_download_url: url },
        });
      },
      onUpdateVideoScenePackage: (sceneId: string, patch: ScenePackagePatch) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        const normalizedPatch: Record<string, unknown> = {};
        if (typeof patch.storyline === "string" && patch.storyline.trim()) {
          normalizedPatch.storyline = patch.storyline;
        }
        if (typeof patch.narration === "string") normalizedPatch.narration = patch.narration;
        if (patch.shot_description && typeof patch.shot_description === "object") {
          const text = typeof patch.shot_description.text === "string"
            ? patch.shot_description.text.trim()
            : "";
          if (text) normalizedPatch.shot_description = { text };
          const mentions = Array.isArray(patch.shot_description.mentions)
            ? patch.shot_description.mentions
            : [];
          normalizedPatch.reference_asset_ids = mentions
            .map((item) => item && typeof item === "object" && typeof item.asset_id === "string" ? item.asset_id : "")
            .filter(Boolean)
            .slice(0, 9);
        } else if (Array.isArray(patch.reference_asset_ids)) {
          normalizedPatch.reference_asset_ids = patch.reference_asset_ids.slice(0, 9);
        }
        if (Object.keys(normalizedPatch).length === 0) return;
        return submit("修改视频分镜", {
          action: "modify_workflow",
          patch: {
            scene_id: sceneId,
            scene_patch: normalizedPatch,
          },
        });
      },
      onDeleteGlobalAsset: (asset: SceneGlobalAssetReference) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        submit("删除视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "delete",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
          },
        });
      },
      onReplaceGlobalAsset: (
        asset: SceneGlobalAssetReference,
        replacement: SceneGlobalAssetReplacement,
      ) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        submit("替换视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "replace",
            asset_group: asset.asset_group,
            asset_id: asset.asset_id,
            asset_patch: replacementPatch(replacement),
          },
        });
      },
      onAddGlobalAsset: (
        assetGroup: GlobalSceneAssetGroup,
        replacement: SceneGlobalAssetReplacement,
      ) => {
        if (target.ui.kind !== "video_scene_package_review") return;
        const videoScenePackages = currentMessage.artifact?.videoScenePackages;
        if (!videoScenePackages) return;
        const added = addGlobalSceneAssetReference(videoScenePackages.global_assets, {
          assetGroup,
          manualId: replacement.contentAssetId || replacement.thirdAssetId || uid(),
          replacement,
        });
        submit("新增视频全局素材", {
          action: "modify_workflow",
          patch: {
            asset_action: "add",
            asset_group: assetGroup,
            asset_id: added.added_asset.asset_id,
            asset_patch: {
              ...replacementPatch(replacement),
              asset_name: added.added_asset.name,
            },
          },
        });
      },
      onSaveStoryboard: () => {
        setCanvasOpen(false);
        setSelectedStoryboardMessageId("");
      },
    };
  }

  const supervisorVideoArtifact = activeSupervisorVideoTarget
    ? renderSupervisorVideoArtifact(activeSupervisorVideoTarget)
    : null;

  /** 对话入口打开右侧脚本预览（默认收起；可再收起后再次点开）。 */
  const openScriptPreviewFromChat = () => {
    setCanvasOpen(false);
    setSelectedPlanEditorMessageId("");
    setSelectedStoryboardMessageId("");
    setScriptPreviewOpen(true);
    const plan = supervisorRuntime.state.videoAgentPlan;
    const exportStep = plan
      ? Object.values(plan.steps).find((step) => stageIdFromStep(step) === "export")
      : null;
    if (exportStep) setSelectedVideoAgentStepId(exportStep.stepId);
  };
  const scriptPreviewAvailable = Boolean(
    videoAgentView.workspace?.script
    || (videoAgentView.workspace?.scriptStages?.length ?? 0) > 0,
  );
  const nativeScriptPreviewOpener = scriptPreviewAvailable
    ? openScriptPreviewFromChat
    : undefined;
  /** Turn 结论「查看分镜」：打开时间线上最新视频场景包卡对应的分镜画布。 */
  const openScenePackageStoryboardFromChat = () => {
    const conversationMessages = messagesRef.current.filter((message) => (
      messageConversationId(message, currentConversationId) === currentConversationId
    ));
    const latestPackages = [...conversationMessages].reverse().find((message) => (
      message.artifact?.type === "video_scene_packages"
      && Boolean(message.artifact.videoScenePackages)
    ));
    if (!latestPackages) {
      pushAssistant("暂未找到可打开的视频场景包，请稍候场景包卡片出现后再试。", currentConversationId);
      return;
    }
    setScriptPreviewOpen(false);
    setCanvasOpen(true);
    setSelectedPlanEditorMessageId("");
    setSelectedStoryboardMessageId(latestPackages.id);
  };
  const scenePackageStoryboardAvailable = messages.some((message) => (
    messageConversationId(message, currentConversationId) === currentConversationId
    && message.artifact?.type === "video_scene_packages"
    && Boolean(message.artifact.videoScenePackages)
  ));
  const nativeScenePackageStoryboardOpener = scenePackageStoryboardAvailable
    ? openScenePackageStoryboardFromChat
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
      <ChatPanel
        messages={messages}
        onSubmit={handleSend}
        onNewConversation={() => {
          window.dispatchEvent(new Event("pixelflow-new-conversation"));
          navigate("/", { replace: true });
        }}
        agentActivityBlocks={[
          ...(() => {
            // video_agent_v2：思考/工具/回答由 AgentTurnGroup 独占，并锚在触发该 Turn 的用户消息后。
            if (orchestrationMode === "video_agent_v2") {
              const turns = selectNativeAgentTurns(supervisorRuntime.nativeUiState);
              const blocks: Array<{ afterMessageId: string; content: ReactElement }> = [];
              for (const turn of turns) {
                const historyAnchor = (supervisorRuntime.state.agentThinkingHistory || [])
                  .find((item) => item.turnId === turn.turnId);
                const afterMessageId = resolveThinkingAfterMessageId(turn.turnId, messages, {
                  pendingTurns: pendingSupervisorTurnsRef.current,
                  knownAnchor: thinkingTurnAnchorsRef.current[turn.turnId],
                  afterMessageId: historyAnchor?.afterMessageId || historyAnchor?.clientInputId || null,
                });
                if (!afterMessageId) continue;
                thinkingTurnAnchorsRef.current[turn.turnId] = afterMessageId;
                blocks.push({
                  afterMessageId,
                  content: (
                    <AgentTurnGroup
                      key={turn.turnId}
                      turn={turn}
                      onOpenScriptPreview={nativeScriptPreviewOpener}
                      onOpenScenePackageStoryboard={nativeScenePackageStoryboardOpener}
                    />
                  ),
                });
              }
              return blocks;
            }
            const liveThinking = supervisorRuntime.state.agentThinking ?? optimisticAgentThinking;
            const blocks: Array<{ afterMessageId: string; content: ReactElement }> = [];
            // 历史思考保留：完成后默认折叠，结论条仍可见。
            for (const archived of agentThinkingHistory) {
              // 当前 Turn 由 liveThinking 负责打字机；归档只保留历史 Turn。
              if (liveThinking?.turnId === archived.turnId) {
                continue;
              }
              if (!archived.afterMessageId) continue;
              blocks.push({
                afterMessageId: archived.afterMessageId,
                content: (
                  <AgentThinkingStream
                    key={archived.turnId}
                    thinking={archived}
                    defaultExpanded={false}
                  />
                ),
              });
            }
            if (liveThinking) {
              const afterMessageId = resolveThinkingAfterMessageId(liveThinking.turnId, messages, {
                pendingTurns: pendingSupervisorTurnsRef.current,
                knownAnchor: thinkingTurnAnchorsRef.current[liveThinking.turnId]
                  || lastPlanAnchorUserMessageIdRef.current,
              });
              if (afterMessageId) {
                thinkingTurnAnchorsRef.current[liveThinking.turnId] = afterMessageId;
                // 当前 Turn 优先展示 live（含完成后的打字机追平），避免立刻切归档导致整段砸出。
                blocks.push({
                  afterMessageId,
                  content: (
                    <AgentThinkingStream
                      key={liveThinking.turnId}
                      thinking={liveThinking}
                      defaultExpanded={liveThinking.status === "streaming"}
                      onRevealStateChange={({ catchingUp, status }) => {
                        setHoldActivePlanForThinking(status === "streaming" || catchingUp);
                      }}
                    />
                  ),
                });
              }
            }
            return blocks;
          })(),
          ...(() => {
            const planOrder = videoAgentPlanHistory.order.length > 0
              ? videoAgentPlanHistory.order
              : supervisorRuntime.state.videoAgentPlanOrder;
            const planMap = Object.keys(videoAgentPlanHistory.plans).length > 0
              ? videoAgentPlanHistory.plans
              : supervisorRuntime.state.videoAgentPlans;
            const orderedPlanIds = [...planOrder].sort((leftId, rightId) => {
              const left = planMap[leftId] || supervisorRuntime.state.videoAgentPlans[leftId];
              const right = planMap[rightId] || supervisorRuntime.state.videoAgentPlans[rightId];
              const rank = (status: string | undefined) => {
                const value = String(status || "").toLowerCase();
                if (value === "running" || value === "planning" || value === "awaiting_confirmation") {
                  return 2;
                }
                if (value === "waiting_for_input") return 1;
                return 0;
              };
              const delta = rank(left?.status) - rank(right?.status);
              if (delta !== 0) return delta;
              return planOrder.indexOf(leftId) - planOrder.indexOf(rightId);
            });
            // 只保留最新一条可展示执行方案，挂在最近用户消息后（对话底部），避免多轮合并卡叠三层。
            const visiblePlanId = (() => {
              const activeId = supervisorRuntime.state.videoAgentPlan?.planId;
              const candidates = orderedPlanIds.filter((planId) => {
                const plan = planMap[planId] || supervisorRuntime.state.videoAgentPlans[planId];
                if (!plan) return false;
                const planStatus = String(plan.status || "").toLowerCase();
                const isActivePlan = activeId === planId;
                const stepCount = Object.keys(plan.steps || {}).length;
                const hasGateSlot = Boolean(
                  (isActivePlan && supervisorRuntime.state.videoAgentConfirmation)
                  || (isActivePlan && supervisorRuntime.state.videoAgentQuota),
                );
                if (
                  orchestrationMode === "video_agent_v2"
                  && stepCount === 0
                  && !hasGateSlot
                  && planStatus !== "awaiting_confirmation"
                ) {
                  return false;
                }
                return true;
              });
              if (activeId && candidates.includes(activeId)) return activeId;
              return candidates.length > 0 ? candidates[candidates.length - 1] : null;
            })();
            const planIdsToShow = visiblePlanId ? [visiblePlanId] : [];
            return planIdsToShow.map((planId) => {
              const plan = planMap[planId] || supervisorRuntime.state.videoAgentPlans[planId];
              if (!plan) return null;
              const isActivePlan = supervisorRuntime.state.videoAgentPlan?.planId === planId;
              const latestUserMessageId = (
                lastPlanAnchorUserMessageIdRef.current
                || [...messages].reverse().find((message) => message.role === "user")?.id
                || messages.find((message) => message.role === "user")?.id
                || ""
              );
              const afterMessageId = resolveVideoAgentPlanAnchorId({
                preferredUserMessageId: latestUserMessageId,
                messages,
              });
              if (!afterMessageId) return null;
              // 思考流打字机未结束前不展示本轮 Plan，保证「先想完 → 再出执行方案」。
              if (isActivePlan && holdActivePlanForThinking && orchestrationMode !== "video_agent_v2") {
                return null;
              }
              return {
                afterMessageId,
                content: (
                  <AgentPlanTimeline
                    plan={plan}
                    selectedStepId={selectedVideoAgentStepId}
                    scriptStages={videoAgentView.workspace?.scriptStages}
                    onSelectStep={(stepId) => {
                      setSelectedVideoAgentStepId(stepId);
                      setCanvasOpen(false);
                      setSelectedStoryboardMessageId("");
                      setSelectedPlanEditorMessageId("");
                      setScriptPreviewOpen(true);
                    }}
                    confirmationSlot={(() => {
                      const conf = supervisorRuntime.state.videoAgentConfirmation;
                      // 对话区只展示最新一条方案：有确认单就挂上，保证「等待确认」可点。
                      if (conf) {
                        if (
                          conf.planId
                          && conf.planId !== plan.planId
                          && !isActivePlan
                        ) {
                          return null;
                        }
                        return (
                          <AgentConfirmationCard
                            confirmationId={conf.confirmationId}
                            stepId={conf.stepId}
                            title={conf.title}
                            costSummary={conf.costSummary}
                            affectedSceneIds={conf.affectedSceneIds}
                            confirmLabel={
                              isScriptCreativeConfirmationTitle(conf.title)
                                ? "同意创意继续"
                                : "确认执行"
                            }
                            cancelLabel={
                              isScriptCreativeConfirmationTitle(conf.title)
                                ? "换个方向"
                                : "取消"
                            }
                            submitting={videoAgentConfirmationSubmitting}
                            actionAvailable={conf.submittable}
                            unavailableReason={conf.unavailableReason}
                            submissionError={videoAgentConfirmationError}
                            onSubmit={handleVideoAgentConfirmation}
                          />
                        );
                      }
                      const hasAwaiting = Object.values(plan.steps || {}).some(
                        (step) => step.status === "awaiting_confirmation",
                      );
                      if (!hasAwaiting) return null;
                      // Snapshot 无 confirmation 但本地仍 awaiting：给出可点的恢复入口。
                      return (
                        <section
                          aria-label="执行确认已过期"
                          className="rounded-xl border border-amber-200 bg-amber-50 p-4"
                        >
                          <h2 className="text-base font-semibold text-amber-950">合并分镜视频为成片</h2>
                          <p className="mt-1 text-sm text-amber-900">
                            当前确认单已失效或未同步。请重新发起合并，系统会再次弹出确认。
                          </p>
                          <div className="mt-4 flex justify-end">
                            <button
                              type="button"
                              className="rounded-lg bg-amber-700 px-3 py-2 text-sm text-white hover:opacity-90"
                              onClick={() => {
                                void handleSend("合并视频吧");
                              }}
                            >
                              重新发起合并
                            </button>
                          </div>
                        </section>
                      );
                    })()}
                    quotaSlot={isActivePlan && supervisorRuntime.state.videoAgentQuota ? (
                      <AgentQuotaCard
                        quotaInterruptId={supervisorRuntime.state.videoAgentQuota.quotaInterruptId}
                        submitting={videoAgentQuotaSubmitting}
                        actionAvailable={supervisorRuntime.state.videoAgentQuota.submittable}
                        unavailableReason={supervisorRuntime.state.videoAgentQuota.unavailableReason}
                        submissionError={videoAgentQuotaError}
                        onSubmit={handleVideoAgentQuota}
                      />
                    ) : null}
                  />
                ),
              };
            });
          })()
            .filter((block): block is { afterMessageId: string; content: ReactElement } => block !== null),
        ]}
        composerTopSlot={
          sceneVideoProgressSteps.some((step) => step.status === "running")
            ? (
              <AgentPipelineProgress
                title="执行规划 · 分镜视频"
                subtitle="生成中"
                steps={sceneVideoProgressSteps}
                defaultCollapsed
              />
            )
            : assetPackageProgressSteps.some((step) => step.status === "running")
            ? (
              <AgentPipelineProgress
                title="执行规划 · 视频资产包"
                subtitle="分步生成"
                steps={assetPackageProgressSteps}
                defaultCollapsed
              />
            )
            : null
        }
        nativeTurnGroups={(() => {
          // 有锚点的 Turn 已进 agentActivityBlocks；这里只兜底尚未解析到用户消息的 Turn。
          if (orchestrationMode !== "video_agent_v2") return null;
          const turns = selectNativeAgentTurns(supervisorRuntime.nativeUiState);
          const orphans = turns.filter((turn) => {
            const historyAnchor = (supervisorRuntime.state.agentThinkingHistory || [])
              .find((item) => item.turnId === turn.turnId);
            const afterMessageId = resolveThinkingAfterMessageId(turn.turnId, messages, {
              pendingTurns: pendingSupervisorTurnsRef.current,
              knownAnchor: thinkingTurnAnchorsRef.current[turn.turnId],
              afterMessageId: historyAnchor?.afterMessageId || historyAnchor?.clientInputId || null,
            });
            return !afterMessageId;
          });
          if (orphans.length === 0) return null;
          return orphans.map((turn) => (
            <AgentTurnGroup
              key={turn.turnId}
              turn={turn}
              onOpenScriptPreview={nativeScriptPreviewOpener}
              onOpenScenePackageStoryboard={nativeScenePackageStoryboardOpener}
            />
          ));
        })()}
        referencedMaterials={referencedMaterials}
        onRemoveReferencedMaterial={handleRemoveReferencedMaterial}
        composerPrefillRequest={composerPrefillRequest}
        composerDisabled={interactionPolicy.composer.disabled}
        artifactActionsDisabled={interactionPolicy.artifact.actionsDisabled}
        runtimeBusy={interactionPolicy.runtime.busy}
        runtimeNotice={runtimeNotice}
        workflowTaskBoard={workflowTaskBoard}
        onSelectDirection={(legacyArtifactActionsEnabled ? handleSelectDirection : undefined)
          ?? supervisorVideoArtifact?.onSelectDirection}
        onRegenerateDirections={(legacyArtifactActionsEnabled ? handleRegenerateDirections : undefined)
          ?? supervisorVideoArtifact?.onRegenerateDirections}
        onApprovePlan={(msg) => {
          const isScriptAssetConfirm = Boolean(
            msg.artifact?.scriptPlanConfirmForAssets
            || msg.artifact?.title === "脚本方案待确认"
            || msg.artifact?.title === "已确认脚本方案",
          );
          if (isScriptAssetConfirm || legacyArtifactActionsEnabled) {
            void handleApprovePlan(msg);
            return;
          }
          supervisorVideoArtifact?.onApprovePlan?.(msg);
        }}
        onRegeneratePlanDirections={supervisorVideoArtifact?.onRegeneratePlanDirections}
        onEditPlan={legacyArtifactActionsEnabled ? handleEditPlan : undefined}
        onRevisePlan={(legacyArtifactActionsEnabled ? handleRevisePlan : undefined)
          ?? supervisorVideoArtifact?.onRevisePlan}
        agentRevisionSourceMessageId={agentRevisionSourceMessageId}
        onRollbackPlan={(legacyArtifactActionsEnabled ? handleRollbackPlan : undefined)
          ?? supervisorVideoArtifact?.onRollbackPlan}
        onGenerateImage={legacyArtifactActionsEnabled ? handleGenerateImage : undefined}
        onConfirmImageEditOptions={legacyArtifactActionsEnabled ? handleConfirmImageEditOptions : undefined}
        onConfirmSceneAssetModel={handleConfirmSceneAssetModel}
        onAcceptImageResult={legacyArtifactActionsEnabled ? handleAcceptImageResult : undefined}
        onReviseImageResult={legacyArtifactActionsEnabled ? handleReviseImageResult : undefined}
        onGenerateVideoFromScenePackages={handleGenerateVideoFromScenePackages}
        onAcceptVideoResult={(legacyArtifactActionsEnabled ? handleAcceptVideoResult : undefined)
          ?? supervisorVideoArtifact?.onAcceptVideoResult}
        onReviseVideoResult={(legacyArtifactActionsEnabled ? handleReviseVideoResult : undefined)
          ?? supervisorVideoArtifact?.onReviseVideoResult}
        onOpenVideoResult={handleOpenVideoResult}
        onRegenerateVideoWithRevision={handleRegenerateVideoWithRevision}
        onRetryImageResult={legacyArtifactActionsEnabled ? handleRetryImageResult : undefined}
        onRetrySceneAssets={(msg: ChatMessage) => {
          // prepare-scene-packages / Video Agent 资产包走 Python job；v2 下不能落到空的 continue_workflow。
          if (msg.artifact?.videoScenePackages && msg.artifact.sceneAssetFailures?.length) {
            void handleRetrySceneAssets(msg);
            return;
          }
          if (legacyArtifactActionsEnabled) {
            void handleRetrySceneAssets(msg);
            return;
          }
          supervisorVideoArtifact?.onRetrySceneAssets?.(msg);
        }}
        onRetryVideoMerge={(legacyArtifactActionsEnabled ? handleRetryVideoMerge : undefined)
          ?? supervisorVideoArtifact?.onRetryVideoMerge}
        onRetryVideoAnalysis={legacyArtifactActionsEnabled ? handleRetryVideoAnalysis : undefined}
        onApprovePptOutline={legacyArtifactActionsEnabled ? handleApprovePptOutline : undefined}
        onRevisePptOutline={legacyArtifactActionsEnabled ? handleRevisePptOutline : undefined}
        onRegeneratePptImage={legacyArtifactActionsEnabled ? handleRegeneratePptImage : undefined}
        onGeneratePptFile={legacyArtifactActionsEnabled ? handleGeneratePptFile : undefined}
        onAcceptPptFile={legacyArtifactActionsEnabled ? handleAcceptPptFile : undefined}
        onRegeneratePptFile={legacyArtifactActionsEnabled ? handleRegeneratePptFile : undefined}
        onGenerateJianyingDraft={(legacyArtifactActionsEnabled ? handleGenerateJianyingDraft : undefined)
          ?? supervisorVideoArtifact?.onGenerateJianyingDraft}
        onDownloadJianyingDraft={(legacyArtifactActionsEnabled ? handleDownloadJianyingDraft : undefined)
          ?? supervisorVideoArtifact?.onDownloadJianyingDraft}
        jianyingDraftCapability={jianyingDraftCapability}
        getJianyingDraftResult={getJianyingDraftResult}
        isJianyingDraftRunning={isJianyingDraftRunning}
        onDownloadArtifact={(msg, url) => {
          if (supervisorVideoArtifact?.message?.id === msg.id) {
            supervisorVideoArtifact.onDownloadArtifact(msg, url);
            return;
          }
          if (legacyArtifactActionsEnabled) void recordArtifactDownload(msg, url);
        }}
        onOpenArtifact={(msg) => {
          if (!msg.artifact) return;
          if (
            supervisorVideoArtifact
            && supervisorVideoArtifact.message?.id !== msg.id
            && !legacyArtifactActionsEnabled
          ) return;
          setScriptPreviewOpen(false);
          setCanvasOpen(true);
          setSelectedPlanEditorMessageId("");
          if (msg.artifact.type === "video_scene_packages") {
            setSelectedStoryboardMessageId(msg.id);
            return;
          }
          // V2：分镜视频卡也带 videoScenePackages，打开时应进入分镜面以便回填预览。
          if (
            msg.artifact.type === "video_result"
            && msg.artifact.videoScenePackages
          ) {
            setSelectedStoryboardMessageId(msg.id);
            setCanvas((c) => ({ ...c, phase: "done", selectedVideo: null }));
            return;
          }
          setSelectedStoryboardMessageId("");
          if (msg.artifact.type === "brief") setCanvas((c) => ({ ...c, phase: "brief_review" }));
          if (msg.artifact.type === "results") setCanvas((c) => ({ ...c, phase: "done" }));
          if (msg.artifact.type === "segments") setCanvas((c) => ({ ...c, phase: "segment_review" }));
          if (msg.artifact.type === "edit") setCanvas((c) => ({ ...c, phase: "edit_review" }));
          if (msg.artifact.type === "qc") setCanvas((c) => ({ ...c, phase: "qc_review" }));
          if (msg.artifact.type === "video_result") setCanvas((c) => ({ ...c, phase: "done", selectedVideo: null }));
          if (["segments", "edit", "qc"].includes(msg.artifact.type)) {
            const phaseByType = { segments: "segment_review", edit: "edit_review", qc: "qc_review" } as const;
            void loadResults(phaseByType[msg.artifact.type as "segments" | "edit" | "qc"]);
          }
        }}
        onOpenScriptPreview={openScriptPreviewFromChat}
      />
      {orchestrationMode === "video_agent_v2" && canvasOpen ? (
        <ArtifactCanvasRouter
          kind={
            selectedPlanEditorMessage?.artifact?.plan
              ? "plan_markdown"
              : selectedStoryboardMessage?.artifact?.videoScenePackages
                ? "scene_package"
                : resolveCanvasKindFromArtifact(
                    (selectedStoryboardMessage?.artifact
                      || selectedPlanEditorMessage?.artifact
                      || null) as Record<string, unknown> | null,
                  ) || "legacy_canvas"
          }
          header={{
            title: selectedPlanEditorMessage?.artifact?.plan
              ? "创作方案"
              : selectedStoryboardMessage?.artifact?.videoScenePackages
                ? "场景包"
                : "工作台",
            versionLabel: videoAgentView.workspace
              ? `rev ${videoAgentView.workspace.revision}`
              : null,
            dirtySceneCount:
              selectedStoryboardMessage?.artifact?.videoScenePackageEditedSceneIds?.length || 0,
            saveStatus: savingPlanEdit || savingVideoAgentScript ? "saving" : "idle",
          }}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
            setSelectedPlanEditorMessageId("");
          }}
          planMarkdown={
            selectedPlanEditorMessage?.artifact?.plan ? (
              <Suspense fallback={<div className="p-4 text-[13px] text-ink-soft">正在加载 Markdown 编辑器…</div>}>
                <PlanMarkdownEditor
                  planVersion={selectedPlanEditorMessage.artifact.plan.plan_version || 1}
                  initialMarkdown={selectedPlanEditorMessage.artifact.plan.plan_markdown}
                  saving={savingPlanEdit}
                  onConfirm={(markdown) => handlePublishPlanEdit(selectedPlanEditorMessage, markdown)}
                  onClose={() => {
                    setCanvasOpen(false);
                    setSelectedPlanEditorMessageId("");
                  }}
                />
              </Suspense>
            ) : null
          }
          scenePackage={
            selectedStoryboardMessage?.artifact?.videoScenePackages ? (
              <ScenePackageCanvas
                msg={selectedStoryboardMessage}
                generatingSceneIds={storyboardGeneratingSceneIds}
                mergedVideoUrl={
                  videoAgentView.workspace?.mergedVideoUrl
                  || selectedStoryboardMessage.artifact?.mergedVideo?.merged_video_url
                  || null
                }
                // V2：编辑仅进本地草稿，点「保存」再提交 patch_scene Turn，避免打字/@ 就打断对话。
                deferSceneUpdates
                onUpdateVideoScenePackage={(sceneId, patch) => {
                  handleUpdateVideoScenePackage(selectedStoryboardMessage, sceneId, patch);
                }}
                onReferenceGlobalAsset={handleReferenceGlobalAsset}
                onDeleteGlobalAsset={handleDeleteGlobalAsset}
                onReplaceGlobalAsset={legacyArtifactActionsEnabled ? handleReplaceGlobalAsset : undefined}
                onSupervisorReplaceGlobalAsset={supervisorVideoArtifact?.onReplaceGlobalAsset}
                onAddGlobalAsset={legacyArtifactActionsEnabled
                  ? (assetGroup, replacement) => handleAddGlobalAsset(selectedStoryboardMessage, assetGroup, replacement)
                  : supervisorVideoArtifact?.onAddGlobalAsset}
                onGenerateVideo={(sceneId) => handleGenerateVideoFromScenePackages(
                  selectedStoryboardMessage,
                  sceneId ? { sceneId } : undefined,
                )}
                onRetrySceneAssets={() => handleRetrySceneAssets(selectedStoryboardMessage)}
                onSave={() => handleSaveVideoScenePackage(selectedStoryboardMessage)}
                onClose={() => {
                  setCanvasOpen(false);
                  setSelectedStoryboardMessageId("");
                  setSelectedPlanEditorMessageId("");
                }}
              />
            ) : null
          }
          legacyCanvas={(
            <CanvasPanel
              state={canvas}
              onApprove={legacyArtifactActionsEnabled ? handleApprove : () => undefined}
              onRevise={legacyArtifactActionsEnabled ? handleRevise : () => undefined}
              onConfirmStage={legacyArtifactActionsEnabled ? handleConfirmStage : undefined}
              onSelectVideo={(video) => setCanvas((current) => ({ ...current, selectedVideo: video }))}
              onDownloadVideo={handleDownloadPreviewVideo}
              onClose={() => {
                setCanvasOpen(false);
                setSelectedStoryboardMessageId("");
                setSelectedPlanEditorMessageId("");
              }}
              briefConfirmed={briefConfirmed}
            />
          )}
        />
      ) : legacyArtifactActionsEnabled && canvasOpen && selectedPlanEditorMessage?.artifact?.plan ? (
        <Suspense fallback={<aside className="fixed inset-0 z-50 flex h-full w-full min-w-0 items-center justify-center border-l border-line bg-[#f8fafc] text-[13px] text-ink-soft xl:static xl:z-auto xl:w-[52vw] xl:min-w-[680px]">正在加载 Markdown 编辑器…</aside>}>
          <PlanMarkdownEditor
            planVersion={selectedPlanEditorMessage.artifact.plan.plan_version || 1}
            initialMarkdown={selectedPlanEditorMessage.artifact.plan.plan_markdown}
            saving={savingPlanEdit}
            onConfirm={(markdown) => handlePublishPlanEdit(selectedPlanEditorMessage, markdown)}
            onClose={() => {
              setCanvasOpen(false);
              setSelectedPlanEditorMessageId("");
            }}
          />
        </Suspense>
      ) : canvasOpen && selectedStoryboardMessage?.artifact?.videoScenePackages ? (
        <VideoAgentStoryboardSurface
          msg={selectedStoryboardMessage}
          generatingSceneIds={storyboardGeneratingSceneIds}
          mergedVideoUrl={
            videoAgentView.workspace?.mergedVideoUrl
            || selectedStoryboardMessage.artifact?.mergedVideo?.merged_video_url
            || null
          }
          // V2 / Supervisor：编辑先进草稿，保存时才写回并提交「修改分镜」Turn。
          deferSceneUpdates={
            orchestrationMode === "video_agent_v2" || Boolean(supervisorVideoArtifact)
          }
          onUpdateVideoScenePackage={(sceneId, patch) => {
            if (orchestrationMode === "video_agent_v2" || legacyArtifactActionsEnabled) {
              handleUpdateVideoScenePackage(selectedStoryboardMessage, sceneId, patch);
              return;
            }
            void supervisorVideoArtifact?.onUpdateVideoScenePackage?.(sceneId, patch);
          }}
          onReferenceGlobalAsset={runtimePolicy.supervisorEnabled || legacyArtifactActionsEnabled ? handleReferenceGlobalAsset
            : undefined}
          onDeleteGlobalAsset={runtimePolicy.supervisorEnabled || legacyArtifactActionsEnabled ? handleDeleteGlobalAsset
            : undefined}
          onReplaceGlobalAsset={legacyArtifactActionsEnabled ? handleReplaceGlobalAsset : undefined}
          onSupervisorReplaceGlobalAsset={supervisorVideoArtifact?.onReplaceGlobalAsset}
          onAddGlobalAsset={legacyArtifactActionsEnabled
            ? (assetGroup, replacement) => handleAddGlobalAsset(selectedStoryboardMessage, assetGroup, replacement)
            : supervisorVideoArtifact?.onAddGlobalAsset}
          onGenerateVideo={(sceneId) => handleGenerateVideoFromScenePackages(
            selectedStoryboardMessage,
            sceneId ? { sceneId } : undefined,
          )}
          onRetrySceneAssets={() => handleRetrySceneAssets(selectedStoryboardMessage)}
          onSave={orchestrationMode === "video_agent_v2" || legacyArtifactActionsEnabled
            ? () => handleSaveVideoScenePackage(selectedStoryboardMessage)
            : supervisorVideoArtifact?.onSaveStoryboard}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
            setSelectedPlanEditorMessageId("");
          }}
        />
      ) : canvasOpen && (
        <CanvasPanel
          state={canvas}
          onApprove={legacyArtifactActionsEnabled ? handleApprove : () => undefined}
          onRevise={legacyArtifactActionsEnabled ? handleRevise : () => undefined}
          onConfirmStage={legacyArtifactActionsEnabled ? handleConfirmStage : undefined}
          onSelectVideo={(video) => setCanvas((current) => ({ ...current, selectedVideo: video }))}
          onDownloadVideo={handleDownloadPreviewVideo}
          onClose={() => {
            setCanvasOpen(false);
            setSelectedStoryboardMessageId("");
            setSelectedPlanEditorMessageId("");
          }}
          briefConfirmed={briefConfirmed}
        />
      )}
      {!canvasOpen && scriptPreviewOpen && (
        videoAgentView.workspace?.script
        || (videoAgentView.workspace?.scriptStages?.length ?? 0) > 0
      ) ? (
        <AgentScriptPreviewPanel
          revision={videoAgentView.workspace!.revision}
          script={videoAgentView.workspace!.script}
          stages={videoAgentView.workspace!.scriptStages}
          focusStageId={(() => {
            const plan = supervisorRuntime.state.videoAgentPlan;
            const stepId = selectedVideoAgentStepId;
            if (!plan || !stepId) return null;
            const step = plan.steps[stepId];
            return step ? stageIdFromStep(step) : null;
          })()}
          saving={savingVideoAgentScript}
          confirming={confirmingVideoAgentScript}
          exportReady={workspaceHasExportReady({
            scriptContent: videoAgentView.workspace!.script?.content,
            stages: videoAgentView.workspace!.scriptStages,
          })}
          onClose={() => setScriptPreviewOpen(false)}
          onSave={videoAgentView.workspace!.script ? async (markdown) => {
            const conversationId = currentConversationId;
            const workspace = videoAgentView.workspace;
            if (!conversationId || !workspace?.script) {
              throw new Error("当前会话没有可保存的脚本工作区");
            }
            setSavingVideoAgentScript(true);
            try {
              await saveVideoAgentScriptWithRevisionRetry(conversationId, markdown);
              pushAssistant(
                workspaceHasExportReady({
                  scriptContent: markdown,
                  stages: workspace.scriptStages,
                })
                  ? "脚本已保存。请确认脚本方案后再生成视频资产包。"
                  : "脚本已保存。请先完成「导出脚本产物」，再确认并生成资产包。",
                conversationId,
              );
            } finally {
              setSavingVideoAgentScript(false);
            }
          } : undefined}
          onConfirmScript={videoAgentView.workspace!.script ? async (markdown) => {
            const conversationId = currentConversationId;
            if (!conversationId) {
              throw new Error("当前会话没有可确认的脚本工作区");
            }
            await confirmScriptPlanAndGenerateAssetPackage(conversationId, markdown);
          } : undefined}
        />
      ) : null}
      {legacyArtifactActionsEnabled && dialogOpen && (
        <GenParamsDialog
          key={`${pendingIntent}:${pendingCore}`}
          open
          intent={pendingIntent}
          initialCoreMessage={pendingCore}
          initialValues={pendingFormValues}
          initialMaterials={pendingMaterials}
          onConfirm={handleConfirmParams}
          onCancel={handleCancelParamsDialog}
        />
      )}
      {restoredSupervisorUi?.kind === "video_intake_form" && activeSupervisorVideoTarget ? (
        <GenParamsDialog
          key={`supervisor-video:${activeSupervisorVideoTarget.workflow.workflow_id}:${activeSupervisorVideoTarget.workflow.stage_version}`}
          open
          intent="video"
          initialCoreMessage={activeSupervisorVideoTarget.ui.coreMessage}
          initialValues={activeSupervisorVideoTarget.ui.formValues}
          initialMaterials={activeSupervisorVideoTarget.ui.materials}
          onConfirm={(form) => {
            void submitSupervisorAction(
              "确认视频创作需求",
              buildSupervisorWorkflowAction({
                action: "continue_workflow",
                intent: "video",
                workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
                stage: activeSupervisorVideoTarget.stage,
                artifactRef: activeSupervisorVideoTarget.artifactRef,
                patch: {
                  form_values: form,
                  intake_rounds: activeSupervisorVideoTarget.ui.intakeRounds,
                },
              }),
              {
                materials: activeSupervisorVideoTarget.ui.materials,
                artifactRefs: activeSupervisorVideoTarget.artifactRef
                  ? [activeSupervisorVideoTarget.artifactRef]
                  : [],
              },
            );
          }}
          onCancel={() => {
            void submitSupervisorAction(
              "取消视频创作流程",
              buildSupervisorWorkflowAction({
                action: "cancel_workflow",
                intent: "video",
                workflowId: activeSupervisorVideoTarget.workflow.workflow_id,
                stage: activeSupervisorVideoTarget.stage,
                artifactRef: activeSupervisorVideoTarget.artifactRef,
                patch: { form_cancelled: true },
              }),
              {
                artifactRefs: activeSupervisorVideoTarget.artifactRef
                  ? [activeSupervisorVideoTarget.artifactRef]
                  : [],
              },
            );
          }}
        />
      ) : null}
      {restoredSupervisorUi?.kind === "authorization_required"
        && activeSupervisorVideoTarget
        && restoredSupervisorUi.authorizationAction ? (
          <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 px-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
              <h2 className="text-base font-semibold text-slate-900">需要重新授权</h2>
              <p className="mt-2 text-sm text-slate-600">
                当前操作尚未调用供应商。请确认登录状态后继续原操作。
              </p>
              <button
                type="button"
                className="mt-4 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                onClick={() => {
                  void submitSupervisorAction(
                    "授权后继续原视频操作",
                    restoredSupervisorUi.authorizationAction as ExplicitActionSignal,
                    {
                      artifactRefs: activeSupervisorVideoTarget.artifactRef
                        ? [activeSupervisorVideoTarget.artifactRef]
                        : [],
                    },
                  );
                }}
              >
                重新授权并继续
              </button>
            </div>
          </div>
        ) : null}
      <PlanRevisionDialog
        open={Boolean(legacyArtifactActionsEnabled && pendingPlanRevisionChoice && pendingPlanRevisionChoice.conversationId === currentConversationId)}
        feedback={pendingPlanRevisionChoice?.feedback || ""}
        onConfirm={(mode) => void handleConfirmPlanRevisionMode(mode)}
        onCancel={handleCancelPlanRevisionMode}
      />
      </div>
    </div>
  );
}

export { LEGACY_VIDEO_JOB_HTTP_REMOVED, LEGACY_VIDEO_JOB_CONTINUE_TIP } from "./legacyWorkspaceLegacyVideoJobs";
/** 旧视频 Job 阻断提示（契约测试 grep）：请在对话中说明需求，由 VideoAgent 继续生成 */
export { WorkspacePage as LegacyWorkspace };
