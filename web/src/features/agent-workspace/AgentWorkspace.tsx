/** 新 Runtime 工作台组合根：消息、公开进度与只读 Workspace 均从 Snapshot 投影。 */

import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useAgentConversation } from "@/features/agent-runtime/useAgentConversation";
import { isRecoveryRequired, projectVisible } from "@/features/agent-runtime/state";
import { WorkspaceV2Panel } from "@/features/agent-runtime/WorkspaceV2Panel";
import { ConversationList } from "@/features/conversations/ConversationList";
import { ConversationMessages } from "@/features/conversations/ConversationMessages";

import { AgentTaskBoard } from "./AgentTaskBoard";
import { Composer } from "./Composer";
import { ConnectionNotice } from "./ConnectionNotice";
import { InterruptHost } from "./InterruptHost";
import { OperationProgress } from "./OperationProgress";
import { WorkspaceShell } from "./WorkspaceShell";

type AgentWorkspaceProps = {
  conversationId?: string;
};

/** 只消费 Gateway 公开 Snapshot/SSE 的新工作台，不保留旧任务轮询或业务状态副本。 */
export function AgentWorkspace({ conversationId }: AgentWorkspaceProps) {
  const { conversationId: routeConversationId } = useParams();
  const navigate = useNavigate();
  const {
    conversations,
    detail,
    runtime,
    error,
    loading,
    canSend,
    newConversation,
    renameConversation,
    submitTurn,
    confirmInterrupt,
    submitFormInterrupt,
    resumeAuthorizationInterrupt,
    confirmationSubmittingId,
    recoverActiveRun,
    recoveringRunId,
  } = useAgentConversation(conversationId ?? routeConversationId);
  const snapshot = runtime.snapshot;
  const visible = useMemo(() => projectVisible(runtime), [runtime]);
  const recoveryRequired = isRecoveryRequired(snapshot)
    && detail?.latest_harness_run_is_user_turn === true;
  const quotaInterrupt = (() => {
    const workspace = runtime.videoWorkspace ?? snapshot?.workspace;
    const interruptId = workspace?.summary.quota_interrupt_id;
    if (!workspace || typeof interruptId !== "string" || !interruptId) return null;
    return {
      interrupt_id: interruptId,
      kind: "quota" as const,
      title: "额度不足",
      description: typeof workspace.summary.quota_interrupt_reason_code === "string"
        ? workspace.summary.quota_interrupt_reason_code
        : "当前任务已暂停。",
      status: "open" as const,
    };
  })();
  const interrupts = quotaInterrupt === null
    ? runtime.interrupts
    : [...runtime.interrupts.filter((item) => item.interrupt_id !== quotaInterrupt.interrupt_id), quotaInterrupt];
  const openFromList = (nextConversationId: string) => {
    /** URL 是最后停留会话的唯一浏览器恢复来源，刷新后不应回跳旧会话。 */

    navigate(`/c/${encodeURIComponent(nextConversationId)}`);
  };
  const createFromList = async () => {
    /** 新会话创建后同步 URL，避免刷新时仍恢复创建前的历史会话。 */

    const created = await newConversation();
    navigate(`/c/${encodeURIComponent(created.conversation_id)}`, { replace: true });
  };
  const renameFromList = (conversation: typeof conversations[number]) => {
    const title = window.prompt("请输入会话名称", conversation.title);
    if (title !== null) void renameConversation(conversation, title);
  };

  return (
    <WorkspaceShell
      sidebar={(
        <ConversationList
          conversations={conversations}
          activeConversationId={detail?.conversation.conversation_id}
          onCreate={() => void createFromList()}
          onOpen={openFromList}
          onRename={renameFromList}
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
          executionSummary={visible.thinkingPreview}
          processing={runtime.inputStatus === "sending" || runtime.inputStatus === "queued" || runtime.inputStatus === "processing"}
          loading={loading}
        />
      )}
      composer={(
        <>
          <AgentTaskBoard
            status={snapshot?.status}
            latestProgress={visible.progressLines.at(-1)}
            recoveryRequired={recoveryRequired}
            recovering={recoveringRunId === snapshot?.run_id}
            onRecover={() => void recoverActiveRun()}
          />
          <InterruptHost
            interrupts={interrupts}
            confirmationSubmittingId={confirmationSubmittingId}
            onConfirm={confirmInterrupt}
            onResumeAuthorization={resumeAuthorizationInterrupt}
            onSubmitForm={submitFormInterrupt}
          />
          <OperationProgress operations={runtime.operations} />
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
          {runtime.videoWorkspace ? (
            <WorkspaceV2Panel
              summary={runtime.videoWorkspace.summary}
              conversationId={detail?.conversation.conversation_id ?? ""}
              workspaceId={runtime.videoWorkspace.workspace_id}
              revision={runtime.videoWorkspace.revision}
              operations={runtime.operations}
            />
          ) : (
            <p className="text-xs text-ink-soft">
              尚无可查看的 Workspace 投影。
            </p>
          )}
        </>
      )}
    />
  );
}
