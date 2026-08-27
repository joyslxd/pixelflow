/** 会话切换、Snapshot hydrate 与 SSE 生命周期 Hook。 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  applyHarnessWorkspaceCommand,
  cancelHarnessRun,
  confirmHarnessInterrupt,
  getHarnessSnapshot,
  respondToHarnessInterrupt,
  startHarnessTurn,
  updateVideoPlanPublicGoal as requestVideoPlanPublicGoal,
} from "@/api/agentRuntime";
import type { InterruptResponseV1, TurnStartV1, WorkspaceCommandV1 } from "@/api/contracts";
import {
  HARNESS_ORCHESTRATION_MODE,
  createConversation,
  getConversation,
  listConversations,
  type ConversationDetailV1,
  type ConversationV1,
} from "@/api/conversations";
import { AgentApiError } from "@/api/http";
import { getOrCreateVideoWorkspace } from "@/api/workspaces";

import { reconnectingEventStream } from "./eventStream";
import { publicErrorMessage } from "./errors";
import {
  applyPublicEvent,
  hydrateSnapshot,
  initialAgentWorkspaceState,
  isTerminalSnapshot,
  replaceVideoWorkspace,
  setConnection,
  shouldReloadSnapshot,
  type AgentWorkspaceState,
} from "./state";

function latestRunId(detail: ConversationDetailV1): string | null {
  /** 只从服务端已持久化消息的 Harness 标识恢复，不扫描旧任务字段。 */

  for (const message of [...detail.messages].reverse()) {
    const runId = message.payload?.harness_run_id;
    if (typeof runId === "string" && /^hrun_[a-f0-9]{32}$/u.test(runId)) return runId;
  }
  return null;
}

function isHarnessConversation(detail: ConversationDetailV1 | null): boolean {
  return detail?.conversation.orchestration_mode === HARNESS_ORCHESTRATION_MODE;
}

export function useAgentConversation(initialConversationId?: string) {
  /** 协调纯传输状态；业务事实始终由 Snapshot 和有序公开事件产生。 */

  const [conversations, setConversations] = useState<ConversationV1[]>([]);
  const [detail, setDetail] = useState<ConversationDetailV1 | null>(null);
  const [runtime, setRuntime] = useState<AgentWorkspaceState>(initialAgentWorkspaceState);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);
  const runtimeRef = useRef(runtime);
  const pendingTurnRef = useRef<{ client_input_id: string; content: string } | null>(null);
  const confirmationResponseIdsRef = useRef(new Map<string, string>());
  const [confirmationSubmittingId, setConfirmationSubmittingId] = useState<string | null>(null);

  useEffect(() => {
    runtimeRef.current = runtime;
  }, [runtime]);

  const stopStream = useCallback(() => {
    /** 退出或切换会话时终止旧流，保证旧响应无法写回新会话。 */

    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
  }, []);

  const refreshConversations = useCallback(async () => {
    const items = await listConversations();
    setConversations(items);
  }, []);

  const hydrateRun = useCallback(async (conversationId: string, runId: string): Promise<boolean> => {
    /** 读取并校验完整 Snapshot；非法 sequence 直接提示而不是局部容错。 */

    const snapshot = await getHarnessSnapshot(conversationId, runId);
    setRuntime((current) => hydrateSnapshot(snapshot, {
      videoWorkspace: snapshot.workspace ?? current.videoWorkspace,
      messages: current.messages,
      connection: "connected",
    }));
    return !isTerminalSnapshot(snapshot);
  }, []);

  const startEventStream = useCallback(async (conversationId: string, runId: string) => {
    /** 从已 hydrate 的 sequence 订阅；仅 gap / Tool / Artifact / 终态回读 Snapshot。 */

    stopStream();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    await reconnectingEventStream(conversationId, runId, controller.signal, {
      getAfterSequence: () => runtimeRef.current.snapshot?.last_sequence ?? 0,
      shouldContinue: () => !isTerminalSnapshot(runtimeRef.current.snapshot),
      onConnecting: (reconnecting) => {
        setRuntime((current) => setConnection(current, reconnecting ? "reconnecting" : "connecting"));
      },
      onEvent: async (event) => {
        const [next, result] = applyPublicEvent(runtimeRef.current, event);
        runtimeRef.current = next;
        setRuntime(next);
        if (shouldReloadSnapshot(event, result)) {
          await hydrateRun(conversationId, runId);
          return "reload";
        }
        return "continue";
      },
      onDisconnected: () => setRuntime((current) => setConnection(current, "disconnected")),
    });
  }, [hydrateRun, stopStream]);

  const openConversation = useCallback(async (conversationId: string) => {
    /** 使用 generation 防止 A/B 快速切换时旧请求覆盖当前会话。 */

    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    stopStream();
    pendingTurnRef.current = null;
    confirmationResponseIdsRef.current.clear();
    setLoading(true);
    setError("");
    setRuntime(initialAgentWorkspaceState);
    try {
      const [next, workspace] = await Promise.all([
        getConversation(conversationId),
        getOrCreateVideoWorkspace(conversationId),
      ]);
      if (generation !== requestGenerationRef.current) return;
      setDetail(next);
      setRuntime((current) => replaceVideoWorkspace({
        ...current,
        conversationId,
        messages: next.messages.map((message) => ({
          message_id: message.message_id,
          role: message.role,
          content: message.content,
        })),
        connection: "idle",
      }, workspace));
      const runId = latestRunId(next);
      if (runId !== null && await hydrateRun(conversationId, runId)) {
        if (generation === requestGenerationRef.current) void startEventStream(conversationId, runId);
      }
    } catch (caught) {
      if (generation === requestGenerationRef.current) {
        setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
      }
    } finally {
      if (generation === requestGenerationRef.current) setLoading(false);
    }
  }, [hydrateRun, startEventStream, stopStream]);

  const newConversation = useCallback(async () => {
    /** 新会话创建完成后回读服务端状态，避免前端构造业务副本。 */

    setError("");
    try {
      const created = await createConversation();
      await refreshConversations();
      await openConversation(created.conversation_id);
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
    }
  }, [openConversation, refreshConversations]);

  const submitTurn = useCallback(async (content: string) => {
    /** 提交一个冻结 Turn；网络失败必须复用同一个 client_input_id。 */

    if (detail === null) throw new Error("conversation_unselected");
    if (!isHarnessConversation(detail)) {
      setError(publicErrorMessage("conversation_read_only"));
      throw new Error("conversation_read_only");
    }
    const workspace = runtime.videoWorkspace;
    if (workspace === null) {
      setError(publicErrorMessage("harness_workspace_not_found"));
      throw new Error("harness_workspace_not_found");
    }
    const trimmed = content.trim();
    const pending = pendingTurnRef.current;
    const clientInputId = pending !== null && pending.content === trimmed
      ? pending.client_input_id
      : crypto.randomUUID();
    pendingTurnRef.current = { client_input_id: clientInputId, content: trimmed };
    const turn: TurnStartV1 = {
      client_input_id: clientInputId,
      workspace_id: workspace.workspace_id,
      expected_workspace_revision: workspace.revision,
      content: trimmed,
    };
    setError("");
    setRuntime((current) => ({ ...current, inputStatus: "sending" }));
    try {
      const started = await startHarnessTurn(detail.conversation.conversation_id, turn);
      pendingTurnRef.current = null;
      const nextDetail = await getConversation(detail.conversation.conversation_id);
      setDetail(nextDetail);
      setRuntime((current) => replaceVideoWorkspace(current, {
        ...workspace,
        revision: started.workspace_revision,
      }));
      await hydrateRun(detail.conversation.conversation_id, started.run_id);
      void startEventStream(detail.conversation.conversation_id, started.run_id);
    } catch (caught) {
      const code = caught instanceof AgentApiError ? caught.code : undefined;
      if (code === "harness_workspace_revision_conflict") {
        try {
          const latest = await getOrCreateVideoWorkspace(detail.conversation.conversation_id);
          setRuntime((current) => replaceVideoWorkspace(current, latest));
        } catch {
          // 保留原错误提示；工作区刷新失败不覆盖 revision 冲突语义。
        }
      }
      setRuntime((current) => ({ ...current, inputStatus: "idle" }));
      setError(publicErrorMessage(code));
      throw caught;
    }
  }, [detail, hydrateRun, runtime.videoWorkspace, startEventStream]);

  const refreshActiveRun = useCallback(async () => {
    /** 用户主动刷新可恢复事实，不创建新的 Run 或重新发送输入。 */

    if (detail === null || runtime.snapshot === null) return;
    try {
      const keepStreaming = await hydrateRun(detail.conversation.conversation_id, runtime.snapshot.run_id);
      if (keepStreaming) void startEventStream(detail.conversation.conversation_id, runtime.snapshot.run_id);
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
    }
  }, [detail, hydrateRun, runtime.snapshot, startEventStream]);

  const cancelActiveRun = useCallback(async () => {
    /** 取消只请求 Gateway；终态仍从 Snapshot/SSE 回填。 */

    if (detail === null || runtime.snapshot === null) return;
    try {
      await cancelHarnessRun(detail.conversation.conversation_id, runtime.snapshot.run_id);
      await refreshActiveRun();
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
    }
  }, [detail, refreshActiveRun, runtime.snapshot]);

  const submitWorkspaceCommand = useCallback(async (command: WorkspaceCommandV1) => {
    /** 命令成功后立即回读 Snapshot，浏览器不以返回体维护第二份工作区状态。 */

    if (detail === null) throw new Error("conversation_unselected");
    try {
      const result = await applyHarnessWorkspaceCommand(detail.conversation.conversation_id, command);
      setRuntime((current) => replaceVideoWorkspace(current, result.workspace));
      await refreshActiveRun();
    } catch {
      setError("工作区修改未完成，请刷新后确认当前版本。");
      throw new Error("workspace_command_failed");
    }
  }, [detail, refreshActiveRun]);

  const updateScript = useCallback(async (content: string) => {
    /** 脚本复用现有 Workspace Command，严格以当前 Workspace revision 写入。 */

    const workspace = runtime.videoWorkspace;
    if (workspace === null) throw new Error("harness_workspace_not_found");
    await submitWorkspaceCommand({
      client_command_id: crypto.randomUUID(),
      workspace_id: workspace.workspace_id,
      expected_workspace_revision: workspace.revision,
      patch: { script: { content: content.trim(), status: "已编辑" } },
    });
  }, [runtime.videoWorkspace, submitWorkspaceCommand]);

  const updatePlanPublicGoal = useCallback(async (
    planId: string,
    expectedRevision: number,
    publicGoal: string | null,
  ) => {
    /** Plan 不混入 Workspace patch；成功响应只更新来自 Gateway 的同一投影。 */

    if (detail === null) throw new Error("conversation_unselected");
    try {
      const updated = await requestVideoPlanPublicGoal(
        detail.conversation.conversation_id,
        planId,
        { expected_revision: expectedRevision, public_goal: publicGoal },
      );
      setRuntime((current) => {
        const workspace = current.videoWorkspace;
        const activePlan = workspace?.summary.active_plan;
        if (
          workspace === null
          || typeof activePlan !== "object"
          || activePlan === null
          || (activePlan as Record<string, unknown>).plan_id !== updated.plan_id
        ) return current;
        return replaceVideoWorkspace(current, {
          ...workspace,
          summary: {
            ...workspace.summary,
            active_plan: {
              ...(activePlan as Record<string, unknown>),
              revision: updated.revision,
              goal: updated.public_goal,
            },
          },
        });
      });
    } catch (caught) {
      const apiError = caught instanceof AgentApiError ? caught : null;
      if (apiError?.code === "video_plan_revision_conflict") {
        setError(`执行计划已更新到版本 ${apiError.currentRevision ?? "新"}，请刷新后重试。`);
      } else {
        setError("执行计划修改未完成，请刷新后确认当前版本。");
      }
      throw caught;
    }
  }, [detail]);

  const cancelQuotaInterrupt = useCallback(async (
    workspaceId: string,
    interruptId: string,
    response: InterruptResponseV1,
  ) => {
    /** 额度中断取消完成后统一从 Snapshot 恢复 Workspace 与任务进度。 */

    if (detail === null) throw new Error("conversation_unselected");
    try {
      await respondToHarnessInterrupt(
        detail.conversation.conversation_id,
        workspaceId,
        interruptId,
        response,
      );
      await refreshActiveRun();
    } catch {
      setError("额度中断取消未完成，请刷新后确认当前状态。");
      throw new Error("interrupt_response_failed");
    }
  }, [detail, refreshActiveRun]);

  const confirmInterrupt = useCallback(async (interruptId: string) => {
    /** 同一中断重试复用 client_response_id；409 会刷新权威 revision，但不丢弃提交身份。 */

    const workspace = runtime.videoWorkspace;
    if (detail === null || workspace === null) throw new Error("harness_workspace_not_found");
    const clientResponseId = confirmationResponseIdsRef.current.get(interruptId) ?? crypto.randomUUID();
    confirmationResponseIdsRef.current.set(interruptId, clientResponseId);
    setConfirmationSubmittingId(interruptId);
    setError("");
    try {
      const confirmed = await confirmHarnessInterrupt(
        detail.conversation.conversation_id,
        workspace.workspace_id,
        interruptId,
        { client_response_id: clientResponseId, expected_workspace_revision: workspace.revision },
      );
      confirmationResponseIdsRef.current.delete(interruptId);
      setRuntime((current) => replaceVideoWorkspace(current, {
        ...workspace,
        revision: confirmed.workspace_revision,
      }));
      await hydrateRun(detail.conversation.conversation_id, confirmed.run_id);
      void startEventStream(detail.conversation.conversation_id, confirmed.run_id);
    } catch (caught) {
      const apiError = caught instanceof AgentApiError ? caught : null;
      if (apiError?.status === 409) {
        try {
          const latest = await getOrCreateVideoWorkspace(detail.conversation.conversation_id);
          setRuntime((current) => replaceVideoWorkspace(current, latest));
        } catch {
          // 保留稳定错误码；刷新失败不得清空当前中断或提交身份。
        }
      }
      setError(publicErrorMessage(apiError?.code));
      throw caught;
    } finally {
      setConfirmationSubmittingId(null);
    }
  }, [detail, hydrateRun, runtime.videoWorkspace, startEventStream]);

  useEffect(() => {
    void refreshConversations().catch((caught) => {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
    });
    return stopStream;
  }, [refreshConversations, stopStream]);

  useEffect(() => {
    if (initialConversationId) void openConversation(initialConversationId);
  }, [initialConversationId, openConversation]);

  return {
    conversations,
    detail,
    runtime,
    error,
    loading,
    canSend: isHarnessConversation(detail),
    newConversation,
    openConversation,
    submitTurn,
    submitWorkspaceCommand,
    updateScript,
    updatePlanPublicGoal,
    cancelQuotaInterrupt,
    confirmInterrupt,
    confirmationSubmittingId,
    refreshActiveRun,
    cancelActiveRun,
  };
}
