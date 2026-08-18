import type { ReactNode } from "react";
import type { NativeAgentTurn } from "../state/contracts";
import {
  nativeTurnSectionPresence,
  turnOffersScenePackageStoryboard,
  turnOffersScriptPreview,
} from "../state/selectors";
import { splitScriptVersionPreviewParts } from "@/features/video-agent/scriptSkillStages";
import { AgentActivityTimeline } from "./AgentActivityTimeline";
import { AgentReasoningDisclosure } from "./AgentReasoningDisclosure";

interface AgentTurnGroupProps {
  turn: NativeAgentTurn;
  /** 用户气泡由外层渲染；此处只渲染 Agent 侧块。 */
  userSlot?: ReactNode;
  /** 结果卡（确认/产物）由外层注入，保持顺序：活动后、回答前。 */
  resultCardsSlot?: ReactNode;
  now?: number;
  /** 打开右侧脚本预览；有脚本草稿时由外层传入。 */
  onOpenScriptPreview?: () => void;
  /** 当前 Turn 是最新回复，且 Workspace 中当前脚本版本等待确认。 */
  showScriptConfirmationCta?: boolean;
  /** 打开分镜资产包画布；有场景包时由外层传入。 */
  onOpenScenePackageStoryboard?: () => void;
}

/**
 * 同一 Turn 的稳定展示顺序：
 * 用户 → 思考 → 计划/活动 → 结果卡 → 最终回答。
 * 若活动已出现但思考尚未到达，先占位「思考中」，避免活动抢到最前。
 */
export function AgentTurnGroup({
  turn,
  userSlot,
  resultCardsSlot,
  now,
  onOpenScriptPreview,
  showScriptConfirmationCta = false,
  onOpenScenePackageStoryboard,
}: AgentTurnGroupProps) {
  const sections = nativeTurnSectionPresence(turn);
  const toolsRunning = turn.tools.some((tool) => tool.status === "running");
  const showReasoningPlaceholder = !sections.hasReasoning
    && (sections.hasActivity || toolsRunning)
    && !sections.hasResponse;
  const showScenePackageCta = Boolean(onOpenScenePackageStoryboard)
    && turnOffersScenePackageStoryboard(turn);
  const showScriptPreviewCta = Boolean(onOpenScriptPreview)
    && !showScenePackageCta
    && (showScriptConfirmationCta || turnOffersScriptPreview(turn));

  const responseBody = turn.responseText.trim()
    || (turn.responseStatus === "streaming" ? "…" : "");

  const renderResponseText = (content: string): ReactNode => {
    if (!onOpenScriptPreview || !content) return content;
    const parts = splitScriptVersionPreviewParts(content);
    if (parts.every((part) => part.kind === "text")) return content;
    return parts.map((part, index) => {
      if (part.kind === "text") {
        return <span key={`t-${index}`}>{part.text}</span>;
      }
      return (
        <button
          key={`s-${index}`}
          type="button"
          className="inline p-0 align-baseline font-medium text-accent underline underline-offset-2 hover:opacity-80"
          onClick={(event) => {
            event.stopPropagation();
            onOpenScriptPreview();
          }}
        >
          {part.text}
        </button>
      );
    });
  };

  return (
    <div className="space-y-3" data-native-turn-id={turn.turnId}>
      {userSlot}
      {sections.hasReasoning ? (
        <AgentReasoningDisclosure
          text={turn.reasoningText}
          status={turn.reasoningStatus}
          startedAt={turn.reasoningStartedAt}
          durationMs={turn.reasoningDurationMs}
          now={now}
        />
      ) : showReasoningPlaceholder ? (
        <AgentReasoningDisclosure
          text="正在分析你的输入…"
          status="streaming"
          startedAt={turn.tools[0]?.startedAt ?? null}
          durationMs={null}
          now={now}
        />
      ) : null}
      {sections.hasPlan || sections.hasActivity ? (
        <AgentActivityTimeline
          planSteps={turn.planSteps}
          tools={turn.tools}
          now={now}
        />
      ) : null}
      {resultCardsSlot}
      {sections.hasResponse ? (
        <div className="rounded-2xl bg-white px-3.5 py-2.5 text-sm leading-relaxed text-slate-800 shadow-sm ring-1 ring-slate-200/80">
          <div className="whitespace-pre-wrap break-words">
            {renderResponseText(responseBody)}
          </div>
          {showScenePackageCta ? (
            <button
              type="button"
              data-scene-package-cta="true"
              data-allow-when-disabled="true"
              className="mt-3 inline-flex items-center rounded-lg bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent ring-1 ring-accent/20 transition hover:bg-accent/15"
              onClick={() => onOpenScenePackageStoryboard?.()}
            >
              查看分镜
            </button>
          ) : showScriptPreviewCta ? (
            <button
              type="button"
              data-script-preview-cta="true"
              className="mt-3 inline-flex items-center rounded-lg bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent ring-1 ring-accent/20 transition hover:bg-accent/15"
              onClick={() => onOpenScriptPreview?.()}
            >
              {showScriptConfirmationCta ? "查看并确认脚本" : "在右侧查看脚本"}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
