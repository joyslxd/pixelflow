import { useEffect, useRef } from "react";
import { Composer } from "@/components/composer/Composer";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "@/lib/chat";
import type { AgentUserMessagePayload } from "@/lib/authStorage";
import type { CreativeDirectionResponse } from "@/lib/api";
import type { SceneAssetCollection, ScenePackagePatch } from "@/lib/scenePackages";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSubmit: (payload: AgentUserMessagePayload) => void;
  onOpenArtifact?: (msg: ChatMessage) => void;
  onSelectDirection?: (msg: ChatMessage, direction: CreativeDirectionResponse) => void;
  onApprovePlan?: (msg: ChatMessage) => void;
  onRevisePlan?: (msg: ChatMessage) => void;
  onGenerateImage?: (msg: ChatMessage) => void;
  onAcceptImageResult?: (msg: ChatMessage) => void;
  onReviseImageResult?: (msg: ChatMessage) => void;
  onGenerateVideoFromScenePackages?: (msg: ChatMessage) => void;
  onAcceptVideoResult?: (msg: ChatMessage) => void;
  onReviseVideoResult?: (msg: ChatMessage) => void;
  onRegenerateVideoWithRevision?: (msg: ChatMessage, useFlawAnalysis: boolean) => void;
  onUpdateVideoScenePackage?: (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => void;
  onUpdateVideoSceneAssetField?: (
    msg: ChatMessage,
    sceneId: string,
    collection: SceneAssetCollection,
    index: number,
    field: string,
    value: string,
  ) => void;
  busy?: boolean;
}

export function ChatPanel({
  messages,
  onSubmit,
  onOpenArtifact,
  onSelectDirection,
  onApprovePlan,
  onRevisePlan,
  onGenerateImage,
  onAcceptImageResult,
  onReviseImageResult,
  onGenerateVideoFromScenePackages,
  onAcceptVideoResult,
  onReviseVideoResult,
  onRegenerateVideoWithRevision,
  onUpdateVideoScenePackage,
  onUpdateVideoSceneAssetField,
  busy,
}: ChatPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
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
          messages.map((m) => (
            <MessageBubble
              key={m.id}
              msg={m}
              onOpenArtifact={onOpenArtifact}
              onSelectDirection={onSelectDirection}
              onApprovePlan={onApprovePlan}
              onRevisePlan={onRevisePlan}
              onGenerateImage={onGenerateImage}
              onAcceptImageResult={onAcceptImageResult}
              onReviseImageResult={onReviseImageResult}
              onGenerateVideoFromScenePackages={onGenerateVideoFromScenePackages}
              onAcceptVideoResult={onAcceptVideoResult}
              onReviseVideoResult={onReviseVideoResult}
              onRegenerateVideoWithRevision={onRegenerateVideoWithRevision}
              onUpdateVideoScenePackage={onUpdateVideoScenePackage}
              onUpdateVideoSceneAssetField={onUpdateVideoSceneAssetField}
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <div className="shrink-0 p-4">
        <Composer onSubmit={onSubmit} busy={busy} />
      </div>
    </div>
  );
}
