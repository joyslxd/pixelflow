/** 新 Runtime 工作台组合根：消息、公开进度与只读 Workspace 均从 Snapshot 投影。 */

import { useMemo } from "react";

import { VideoWorkspaceSnapshotPanel } from "@/features/agent-runtime/VideoWorkspaceSnapshotPanel";
import { useAgentConversation } from "@/features/agent-runtime/useAgentConversation";
import { projectVisible } from "@/features/agent-runtime/state";
import type { InterruptResponseV1 } from "@/api/contracts";
import { ConversationList } from "@/features/conversations/ConversationList";
import { ConversationMessages } from "@/features/conversations/ConversationMessages";

import { AgentTaskBoard } from "./AgentTaskBoard";
import { Composer } from "./Composer";
import { ConnectionNotice } from "./ConnectionNotice";
import { WorkspaceShell } from "./WorkspaceShell";

type AgentWorkspaceProps = {
  conversationId?: string;
};

function statusLabel(status: string | undefined): string {
  return ({
    accepted: "已受理",
    running: "正在处理",
    completed: "已完成",
    failed: "处理失败",
    cancelled: "已取消",
  } as Record<string, string>)[status ?? ""] ?? "未启动";
}

/** 只消费 Gateway 公开 Snapshot/SSE 的新工作台，不保留旧任务轮询或业务状态副本。 */
export function AgentWorkspace({ conversationId }: AgentWorkspaceProps) {
  const {
    conversations,
    detail,
    runtime,
    error,
    loading,
    canSend,
    newConversation,
    openConversation,
    submitTurn,
    cancelQuotaInterrupt,
    refreshActiveRun,
    cancelActiveRun,
  } = useAgentConversation(conversationId);
  const snapshot = runtime.snapshot;
  const visible = useMemo(() => projectVisible(runtime), [runtime]);
  const progressText = visible.progressLines.join("\n") || "等待公开进度";

  const cancelQuotaInterruptedPlan = async () => {
    /** 仅为已投影的 M06 额度中断创建 cancel_workflow 响应。 */

    const workspace = runtime.videoWorkspace ?? snapshot?.workspace;
    const interruptId = workspace?.summary.quota_interrupt_id;
    if (!workspace || typeof interruptId !== "string" || !interruptId) return;
    const response: InterruptResponseV1 = {
      client_response_id: crypto.randomUUID(),
      value: {
        content: "取消当前额度中断任务。",
        explicit_action: {
          action: "cancel_workflow",
          patch: {},
        },
      },
    };
    await cancelQuotaInterrupt(workspace.workspace_id, interruptId, response);
  };

  return (
    <WorkspaceShell
      sidebar={(
        <ConversationList
          conversations={conversations}
          activeConversationId={detail?.conversation.conversation_id}
          onCreate={() => void newConversation()}
          onOpen={(id) => void openConversation(id)}
        />
      )}
      header={(
        <>
          <span className="truncate font-medium">{detail?.conversation.title || "选择或新建对话"}</span>
          <ConnectionNotice connection={runtime.connection} />
        </>
      )}
      messages={(
        <ConversationMessages
          messages={runtime.messages}
          responsePreview={visible.responsePreview}
          loading={loading}
        />
      )}
      composer={(
        <>
          <AgentTaskBoard status={snapshot?.status} />
          <Composer
            canSend={canSend && detail !== null}
            sending={runtime.inputStatus === "sending"}
            inputStatus={runtime.inputStatus}
            onSubmit={submitTurn}
          />
          {error ? <p className="mt-2 text-sm text-red-600" role="alert">{error}</p> : null}
        </>
      )}
      workspace={(
        <>
          <div className="flex items-center justify-between gap-2">
            <p>运行：{snapshot?.run_id ?? "未启动"}</p>
            <button
              className="rounded border border-line px-2 py-1 text-xs"
              onClick={() => void refreshActiveRun()}
              disabled={!snapshot}
            >
              刷新
            </button>
          </div>
          <p className="mt-1">状态：{statusLabel(snapshot?.status)}</p>
          {snapshot && snapshot.status === "running" ? (
            <button
              className="mt-3 rounded border border-red-200 px-2 py-1 text-xs text-red-700"
              onClick={() => void cancelActiveRun()}
            >
              取消当前运行
            </button>
          ) : null}
          <h2 className="mt-6 text-sm font-medium">公开进度</h2>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-ink-soft">{progressText}</pre>
          {visible.thinkingPreview ? (
            <>
              <h2 className="mt-6 text-sm font-medium">过程摘要</h2>
              <p className="mt-2 text-xs text-ink-soft" aria-live="polite">{visible.thinkingPreview}</p>
            </>
          ) : null}
          <h2 className="mt-6 text-sm font-medium">业务工作区</h2>
          {runtime.videoWorkspace ? (
            <VideoWorkspaceSnapshotPanel
              summary={runtime.videoWorkspace.summary}
              revision={runtime.videoWorkspace.revision}
              onCancelQuotaInterrupt={() => void cancelQuotaInterruptedPlan()}
            />
          ) : (
            <p className="mt-2 text-xs text-ink-soft">
              尚无可恢复的 Workspace 投影。当前不接入付费 Tool 或旧轮询。
            </p>
          )}
        </>
      )}
    />
  );
}
