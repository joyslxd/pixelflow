import { useEffect, useRef } from "react";
import { Composer } from "@/components/composer/Composer";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "@/lib/chat";
import type { VideoResult } from "@/lib/types";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import type { CreativeDirectionResponse, ImageEditModelSelection } from "@/lib/api";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSubmit: (payload: AgentUserMessagePayload) => void;
  referencedMaterials?: Array<Record<string, unknown>>;
  onRemoveReferencedMaterial?: (key: string) => void;
  composerPrefillRequest?: { id: string; content: string } | null;
  onOpenArtifact?: (msg: ChatMessage) => void;
  onSelectDirection?: (msg: ChatMessage, direction: CreativeDirectionResponse) => void;
  onRegenerateDirections?: (msg: ChatMessage) => void;
  onApprovePlan?: (msg: ChatMessage) => void;
  onRevisePlan?: (msg: ChatMessage) => void;
  onRollbackPlan?: (msg: ChatMessage, version: number) => void;
  onGenerateImage?: (msg: ChatMessage) => void;
  onConfirmImageEditOptions?: (msg: ChatMessage, selection: ImageEditModelSelection) => void;
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
  busy?: boolean;
}

function isProgressMessage(message: ChatMessage): boolean {
  if (message.role !== "assistant" || message.artifact) return false;
  if (/采集 Agent 判断这是(?:图片|视频)生成需求/.test(message.content)) return true;
  return /正在|生成中|处理中|继续查询|准备|调用|轮询|合并|重生成/.test(message.content);
}

function hasRecoverableArtifactAction(message: ChatMessage): boolean {
  const artifact = message.artifact;
  if (!artifact) return false;
  if (artifact.imageResult && !artifact.imageResult.ok) return true;
  if (artifact.videoAnalysis && !artifact.videoAnalysis.ok) return true;
  if (artifact.sceneAssetFailures?.length) return true;
  if (artifact.generatedSceneVideos && !artifact.generatedSceneVideos.ok && Boolean(artifact.videoScenePackages)) return true;
  if (artifact.mergedVideo && !artifact.mergedVideo.ok && Boolean(artifact.generatedSceneVideos?.scene_videos.length)) return true;
  if (artifact.pptSummary && !artifact.pptSummary.ok) return true;
  if (artifact.pptFile && !artifact.pptFile.ok) return true;
  return false;
}

export function ChatPanel({
  messages,
  onSubmit,
  referencedMaterials,
  onRemoveReferencedMaterial,
  composerPrefillRequest,
  onOpenArtifact,
  onSelectDirection,
  onRegenerateDirections,
  onApprovePlan,
  onRevisePlan,
  onRollbackPlan,
  onGenerateImage,
  onConfirmImageEditOptions,
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
  busy,
}: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const latestVideoScenePackageMessageId = [...messages]
    .reverse()
    .find((message) => message.artifact?.type === "video_scene_packages" && message.artifact.videoScenePackages)?.id;
  const latestActionableMessageId = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.artifact)?.id;
  const latestAssistantMessage = [...messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const latestProgressMessageId = latestAssistantMessage && isProgressMessage(latestAssistantMessage)
    ? latestAssistantMessage.id
    : undefined;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="flex min-w-0 flex-1 flex-col border-r border-line">
      <div className="flex h-12 shrink-0 items-center px-5 text-[14px] font-semibold text-ink">
        对话
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
            return (
              <MessageBubble
                key={m.id}
                msg={m}
                isLatestVideoScenePackage={isLatestVideoScenePackage}
                actionsDisabled={Boolean(busy) || (!isLatestActionableQualityReview && isSupersededArtifact && !keepScenePackageActions && !keepRecoverableActions)}
                showProgressLoading={m.id === latestProgressMessageId}
                onOpenArtifact={onOpenArtifact}
                onSelectDirection={onSelectDirection}
                onRegenerateDirections={onRegenerateDirections}
                onApprovePlan={onApprovePlan}
                onRevisePlan={onRevisePlan}
                onRollbackPlan={onRollbackPlan}
                onGenerateImage={onGenerateImage}
                onConfirmImageEditOptions={onConfirmImageEditOptions}
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
              />
            );
          })
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 p-4">
        <Composer
          onSubmit={onSubmit}
          referencedMaterials={referencedMaterials}
          onRemoveReferencedMaterial={onRemoveReferencedMaterial}
          prefillRequest={composerPrefillRequest}
          busy={busy}
        />
      </div>
    </div>
  );
}
