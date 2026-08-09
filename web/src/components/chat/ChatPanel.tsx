import { useEffect, useRef, type ReactNode } from "react";
import { Composer } from "@/components/composer/Composer";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "@/lib/chat";
import type { VideoResult } from "@/lib/types";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import type { CreativeDirectionResponse, ImageEditModelSelection } from "@/lib/api";
import { isJianyingDraftResultRetryable, type JianyingDraftCapability, type JianyingDraftJobResponse } from "@/lib/jianyingDraft";
import type { WorkflowTaskBoardModel } from "@/lib/workflowTaskBoard";
import type { SupervisorRuntimeNoticeModel } from "@/lib/supervisor/runtimeNotice";
import { ConversationRuntimeNotice } from "./ConversationRuntimeNotice";
import { WorkflowTaskBoard } from "./WorkflowTaskBoard";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSubmit: (payload: AgentUserMessagePayload) => void;
  onNewConversation?: () => void;
  referencedMaterials?: Array<Record<string, unknown>>;
  onRemoveReferencedMaterial?: (key: string) => void;
  composerPrefillRequest?: { id: string; content: string } | null;
  onOpenArtifact?: (msg: ChatMessage) => void;
  onSelectDirection?: (msg: ChatMessage, direction: CreativeDirectionResponse) => void;
  onRegenerateDirections?: (msg: ChatMessage) => void;
  onApprovePlan?: (msg: ChatMessage) => void;
  onRegeneratePlanDirections?: (msg: ChatMessage) => void;
  onEditPlan?: (msg: ChatMessage) => void;
  onRevisePlan?: (msg: ChatMessage) => void;
  agentRevisionSourceMessageId?: string;
  onRollbackPlan?: (msg: ChatMessage, version: number) => void;
  onGenerateImage?: (msg: ChatMessage) => void;
  onConfirmImageEditOptions?: (msg: ChatMessage, selection: ImageEditModelSelection) => void;
  onConfirmSceneAssetModel?: (msg: ChatMessage, selection: ImageEditModelSelection) => void;
  onAcceptImageResult?: (msg: ChatMessage) => void;
  onReviseImageResult?: (msg: ChatMessage) => void;
  onGenerateVideoFromScenePackages?: (msg: ChatMessage) => void;
  onAcceptVideoResult?: (msg: ChatMessage) => void;
  onReviseVideoResult?: (msg: ChatMessage) => void;
  onOpenVideoResult?: (msg: ChatMessage, video: VideoResult, results: VideoResult[]) => void;
  onRegenerateVideoWithRevision?: (msg: ChatMessage, useQualityReview: boolean) => void;
  onRetryImageResult?: (msg: ChatMessage) => void;
  onRetrySceneAssets?: (msg: ChatMessage) => void;
  onRetryVideoMerge?: (msg: ChatMessage) => void;
  onRetryVideoAnalysis?: (msg: ChatMessage) => void;
  onApprovePptOutline?: (msg: ChatMessage) => void;
  onRevisePptOutline?: (msg: ChatMessage) => void;
  onRegeneratePptImage?: (msg: ChatMessage, pageIndex: number) => void;
  onGeneratePptFile?: (msg: ChatMessage) => void;
  onAcceptPptFile?: (msg: ChatMessage) => void;
  onRegeneratePptFile?: (msg: ChatMessage) => void;
  onGenerateJianyingDraft?: (msg: ChatMessage) => void;
  onDownloadJianyingDraft?: (msg: ChatMessage) => void;
  jianyingDraftCapability?: JianyingDraftCapability;
  getJianyingDraftResult?: (msg: ChatMessage) => JianyingDraftJobResponse | null;
  isJianyingDraftRunning?: (msg: ChatMessage) => boolean;
  onDownloadArtifact?: (msg: ChatMessage, url: string) => void;
  composerDisabled?: boolean;
  artifactActionsDisabled?: boolean;
  runtimeBusy?: boolean;
  runtimeNotice?: SupervisorRuntimeNoticeModel | null;
  workflowTaskBoard?: WorkflowTaskBoardModel | null;
  agentActivity?: ReactNode;
  /** 把活动卡片锚在指定用户/助手消息之后，保证多轮对话时间线顺序正确。 */
  agentActivityBlocks?: Array<{ afterMessageId: string; content: ReactNode }>;
}

function isProgressMessage(message: ChatMessage): boolean {
  if (message.role !== "assistant" || message.artifact) return false;
  if (/采集 Agent 判断这是(?:图片|视频)生成需求/.test(message.content)) return true;
  return /正在|生成中|处理中|继续查询|准备|调用|轮询|合并|重生成/.test(message.content);
}

function hasRecoverableArtifactAction(message: ChatMessage): boolean {
  const artifact = message.artifact;
  if (!artifact) return false;
  // 刷新后解除假 confirmed 的模型卡，即使后面已有场景包消息，仍需可再次确认生图。
  if (artifact.type === "scene_asset_model_options" && !artifact.sceneAssetModelConfirmed) return true;
  if (artifact.type === "video_scene_packages" && (artifact.sceneAssetsAwaitingModel || artifact.sceneAssetsGenerating)) return true;
  if (artifact.imageResult && !artifact.imageResult.ok) return true;
  if (artifact.videoAnalysis && !artifact.videoAnalysis.ok) return true;
  if (artifact.sceneAssetFailures?.length) return true;
  if (artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && Boolean(artifact.videoScenePackages)) return true;
  if (artifact.mergedVideo && !artifact.mergedVideo.ok && Boolean(artifact.generatedSceneVideos?.scene_videos.length)) return true;
  if (artifact.pptSummary && !artifact.pptSummary.ok) return true;
  if (artifact.pptFile && !artifact.pptFile.ok) return true;
  if (artifact.type === "jianying_draft" && isJianyingDraftResultRetryable(artifact.jianyingDraft)) return true;
  return false;
}

export function ChatPanel({
  messages,
  onSubmit,
  onNewConversation,
  referencedMaterials,
  onRemoveReferencedMaterial,
  composerPrefillRequest,
  onOpenArtifact,
  onSelectDirection,
  onRegenerateDirections,
  onApprovePlan,
  onRegeneratePlanDirections,
  onEditPlan,
  onRevisePlan,
  agentRevisionSourceMessageId,
  onRollbackPlan,
  onGenerateImage,
  onConfirmImageEditOptions,
  onConfirmSceneAssetModel,
  onAcceptImageResult,
  onReviseImageResult,
  onGenerateVideoFromScenePackages,
  onAcceptVideoResult,
  onReviseVideoResult,
  onOpenVideoResult,
  onRegenerateVideoWithRevision,
  onRetryImageResult,
  onRetrySceneAssets,
  onRetryVideoMerge,
  onRetryVideoAnalysis,
  onApprovePptOutline,
  onRevisePptOutline,
  onRegeneratePptImage,
  onGeneratePptFile,
  onAcceptPptFile,
  onRegeneratePptFile,
  onGenerateJianyingDraft,
  onDownloadJianyingDraft,
  jianyingDraftCapability,
  getJianyingDraftResult,
  isJianyingDraftRunning,
  onDownloadArtifact,
  composerDisabled = false,
  artifactActionsDisabled = false,
  runtimeBusy = false,
  runtimeNotice = null,
  workflowTaskBoard,
  agentActivity = null,
  agentActivityBlocks = [],
}: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const latestVideoScenePackageMessageId = [...messages]
    .reverse()
    .find((message) => message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages)?.id;
  const latestActionableMessageId = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.artifact?.type !== "jianying_draft" && message.artifact)?.id;
  const latestAssistantMessage = [...messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const latestProgressMessageId = latestAssistantMessage && isProgressMessage(latestAssistantMessage)
    ? latestAssistantMessage.id
    : undefined;
  const firstUserMessageId = messages.find((message) => message.role === "user")?.id;
  const activityBlocksByMessageId = new Map<string, ReactNode[]>();
  const orphanActivityBlocks: ReactNode[] = [];
  const messageIds = new Set(messages.map((message) => message.id));
  for (const block of agentActivityBlocks) {
    if (!block.afterMessageId) continue;
    if (!messageIds.has(block.afterMessageId)) {
      orphanActivityBlocks.push(block.content);
      continue;
    }
    const current = activityBlocksByMessageId.get(block.afterMessageId) || [];
    current.push(block.content);
    activityBlocksByMessageId.set(block.afterMessageId, current);
  }
  // 锚点消息已不存在时，挂到首条用户消息后，避免沉到对话底部看起来像“消失”。
  if (orphanActivityBlocks.length > 0 && firstUserMessageId) {
    const current = activityBlocksByMessageId.get(firstUserMessageId) || [];
    activityBlocksByMessageId.set(firstUserMessageId, [...current, ...orphanActivityBlocks]);
    orphanActivityBlocks.length = 0;
  }
  // 兼容旧用法：未显式锚定时，执行方案跟在首条用户消息后，避免被后续轮次顶到最底部。
  const legacyActivityPlaced = Boolean(
    agentActivity
    && firstUserMessageId
    && (activityBlocksByMessageId.get(firstUserMessageId)?.length || 0) === 0,
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, agentActivityBlocks.length]);

  return (
    <div className="flex min-w-0 flex-1 flex-col border-r border-line" aria-busy={runtimeBusy || undefined}>
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 px-5 text-[14px] font-semibold text-ink">
        <span>对话</span>
        {onNewConversation ? (
          <button
            type="button"
            onClick={onNewConversation}
            className="rounded-lg border border-line px-2.5 py-1 text-[12px] font-medium text-ink-soft transition-colors hover:border-accent/30 hover:text-accent"
          >
            新建对话
          </button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-2">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-ink-soft">
            <p className="text-[15px] font-medium text-ink">描述你想要的视频</p>
            <p className="mt-1 text-[13px]">
              输入商品信息与创意诉求,Agent 会生成 Brief 并产出成片。
            </p>
          </div>
        ) : (
          messages.map((m) => {
            const isLatestVideoScenePackage = m.id === latestVideoScenePackageMessageId;
            const isSupersededArtifact = Boolean(m.artifact && latestActionableMessageId && m.id !== latestActionableMessageId);
            const keepScenePackageActions = isLatestVideoScenePackage && m.artifact?.type === "video_scene_packages";
            const isLatestActionableQualityReview = m.id === latestActionableMessageId && m.artifact?.type === "video_quality_review";
            const keepRecoverableActions = hasRecoverableArtifactAction(m);
            const anchoredBlocks = activityBlocksByMessageId.get(m.id) || [];
            return (
              <div key={m.id} className="space-y-5">
                <MessageBubble
                  msg={m}
                  isLatestVideoScenePackage={isLatestVideoScenePackage}
                  actionsDisabled={Boolean(artifactActionsDisabled) || (!isLatestActionableQualityReview && isSupersededArtifact && !keepScenePackageActions && !keepRecoverableActions)}
                  showProgressLoading={m.id === latestProgressMessageId}
                  onOpenArtifact={onOpenArtifact}
                  onSelectDirection={onSelectDirection}
                  onRegenerateDirections={onRegenerateDirections}
                  onApprovePlan={onApprovePlan}
                  onRegeneratePlanDirections={onRegeneratePlanDirections}
                  onEditPlan={onEditPlan}
                  onRevisePlan={onRevisePlan}
                  hidePlanEdit={isSupersededArtifact || m.id === agentRevisionSourceMessageId}
                  onRollbackPlan={onRollbackPlan}
                  onGenerateImage={onGenerateImage}
                  onConfirmImageEditOptions={onConfirmImageEditOptions}
                  onConfirmSceneAssetModel={onConfirmSceneAssetModel}
                  onAcceptImageResult={onAcceptImageResult}
                  onReviseImageResult={onReviseImageResult}
                  onGenerateVideoFromScenePackages={onGenerateVideoFromScenePackages}
                  onAcceptVideoResult={onAcceptVideoResult}
                  onReviseVideoResult={onReviseVideoResult}
                  onOpenVideoResult={onOpenVideoResult}
                  onRegenerateVideoWithRevision={onRegenerateVideoWithRevision}
                  onRetryImageResult={onRetryImageResult}
                  onRetrySceneAssets={onRetrySceneAssets}
                  onRetryVideoMerge={onRetryVideoMerge}
                  onRetryVideoAnalysis={onRetryVideoAnalysis}
                  onApprovePptOutline={onApprovePptOutline}
                  onRevisePptOutline={onRevisePptOutline}
                  onRegeneratePptImage={onRegeneratePptImage}
                  onGeneratePptFile={onGeneratePptFile}
                  onAcceptPptFile={onAcceptPptFile}
                  onRegeneratePptFile={onRegeneratePptFile}
                  onGenerateJianyingDraft={onGenerateJianyingDraft}
                  onDownloadJianyingDraft={onDownloadJianyingDraft}
                  jianyingDraftCapability={jianyingDraftCapability}
                  jianyingDraftResult={getJianyingDraftResult?.(m) || null}
                  jianyingDraftRunning={Boolean(isJianyingDraftRunning?.(m))}
                  onDownloadArtifact={onDownloadArtifact}
                />
                {anchoredBlocks.map((block, index) => (
                  <div key={`${m.id}-activity-${index}`} className="w-full">{block}</div>
                ))}
                {legacyActivityPlaced && m.id === firstUserMessageId ? (
                  <div className="w-full">{agentActivity}</div>
                ) : null}
              </div>
            );
          })
        )}
        {agentActivity && !legacyActivityPlaced && activityBlocksByMessageId.size === 0 ? (
          <div className="w-full">{agentActivity}</div>
        ) : null}
        {orphanActivityBlocks.map((block, index) => (
          <div key={`orphan-activity-${index}`} className="w-full">{block}</div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="relative shrink-0 px-4 pb-4 pt-2">
        <ConversationRuntimeNotice notice={runtimeNotice} />
        {workflowTaskBoard ? (
          <div className="relative z-0 mr-auto -mb-4 w-full max-w-[1080px] pl-6 pr-7">
            <WorkflowTaskBoard key={workflowTaskBoard.workflowId} model={workflowTaskBoard} />
          </div>
        ) : null}
        <div className="relative z-10">
          <Composer
            onSubmit={onSubmit}
            referencedMaterials={referencedMaterials}
            onRemoveReferencedMaterial={onRemoveReferencedMaterial}
            prefillRequest={composerPrefillRequest}
            disabled={composerDisabled}
          />
        </div>
      </div>
    </div>
  );
}
