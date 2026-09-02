/** 会话切换、Snapshot hydrate 与 SSE 生命周期 Hook。 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  applyHarnessWorkspaceCommand,
  cancelHarnessRun,
  recoverHarnessRun,
  resumeHarnessInterruptAuthorization,
  getHarnessSnapshot,
  respondToHarnessInterrupt,
  startHarnessTurn,
  updateVideoPlanPublicGoal as requestVideoPlanPublicGoal,
} from "@/api/agentRuntime";
import { createClientUuid } from "@/lib/uuid";
import type { AgentSnapshotV1, PublicMessageV1, TurnMaterialV1, TurnStartV1, WorkspaceCommandV1 } from "@/api/contracts";
import {
  HARNESS_ORCHESTRATION_MODE,
  createConversation,
  getConversation,
  listConversations,
  updateConversationTitle,
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
  /** 优先采用 Gateway 按公开 Outbox 回读的最新 Run；恢复 Run 不必伪造用户消息。 */

  if (typeof detail.latest_harness_run_id === "string" && /^hrun_[a-f0-9]{32}$/u.test(detail.latest_harness_run_id)) {
    return detail.latest_harness_run_id;
  }

  for (const message of [...detail.messages].reverse()) {
    const runId = message.payload?.harness_run_id;
    if (typeof runId === "string" && /^hrun_[a-f0-9]{32}$/u.test(runId)) return runId;
  }
  return null;
}

function isHarnessConversation(detail: ConversationDetailV1 | null): boolean {
  return detail?.conversation.orchestration_mode === HARNESS_ORCHESTRATION_MODE;
}

const DEFAULT_CONVERSATION_TITLES = new Set(["新的 Harness 对话", "新的对话"]);

function titleFromFirstTurn(content: string): string {
  /** 导航标题仅作摘要，不改写用户原始消息。 */

  const normalized = content.replace(/\s+/gu, " ").trim();
  return [...normalized].slice(0, 24).join("") || "新的 Harness 对话";
}

function acceptedRunSnapshot(
  conversationId: string,
  runId: string,
  workspace: NonNullable<AgentSnapshotV1["workspace"]>,
): AgentSnapshotV1 {
  /** 首次 Snapshot 回读尚未返回时的传输态占位，不承载任何业务事实。 */

  return {
    run_id: runId,
    conversation_id: conversationId,
    status: "accepted",
    last_sequence: 0,
    last_cursor: "",
    context_version: 0,
    events: [],
    messages: [],
    workspace,
  };
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
  const pendingRuntimeRenderRef = useRef<AgentWorkspaceState | null>(null);
  const runtimeRenderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingTurnRef = useRef<{ client_input_id: string; content: string } | null>(null);
  const confirmationResponseIdsRef = useRef(new Map<string, string>());
  const [confirmationSubmittingId, setConfirmationSubmittingId] = useState<string | null>(null);
  const [recoveringRunId, setRecoveringRunId] = useState<string | null>(null);

  useEffect(() => {
    runtimeRef.current = runtime;
  }, [runtime]);

  const flushRuntimeRender = useCallback(() => {
    if (runtimeRenderTimerRef.current !== null) {
      clearTimeout(runtimeRenderTimerRef.current);
      runtimeRenderTimerRef.current = null;
    }
    const pending = pendingRuntimeRenderRef.current;
    pendingRuntimeRenderRef.current = null;
    if (pending !== null) setRuntime(pending);
  }, []);

  const queueRuntimeRender = useCallback((next: AgentWorkspaceState) => {
    pendingRuntimeRenderRef.current = next;
    if (runtimeRenderTimerRef.current !== null) return;
    // 模型 delta 可能按单字/子词到达；限制视觉提交频率，仍在 runtimeRef 中完整累积事件。
    runtimeRenderTimerRef.current = setTimeout(flushRuntimeRender, 50);
  }, [flushRuntimeRender]);

  const stopStream = useCallback(() => {
    /** 退出或切换会话时终止旧流，保证旧响应无法写回新会话。 */

    flushRuntimeRender();
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
  }, [flushRuntimeRender]);

  const refreshConversations = useCallback(async () => {
    const items = await listConversations();
    setConversations(items);
  }, []);

  const hydrateRun = useCallback(async (conversationId: string, runId: string): Promise<boolean> => {
    /** 读取并校验完整 Snapshot；非法 sequence 直接提示而不是局部容错。 */

    const snapshot = await getHarnessSnapshot(conversationId, runId);
    const current = runtimeRef.current;
    const next = hydrateSnapshot(snapshot, {
      videoWorkspace: current.videoWorkspace,
      messages: current.messages,
      connection: "connected",
    });
    runtimeRef.current = next;
    // Snapshot 是权威收敛点，先提交并清空尚未绘制的 delta，避免旧批次覆盖权威状态。
    flushRuntimeRender();
    setRuntime(next);
    return !isTerminalSnapshot(snapshot);
  }, [flushRuntimeRender]);

  useEffect(() => {
    /** 生成挂起后 SSE 会停；工作台只回读 Gateway 公开 Workspace，不自建任务进度。 */

    const conversationId = detail?.conversation.conversation_id;
    if (!conversationId || runtime.snapshot?.status !== "suspended_operation") return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const refresh = async (): Promise<void> => {
      try {
        const latest = await getOrCreateVideoWorkspace(conversationId);
        if (cancelled) return;
        const next = replaceVideoWorkspace(runtimeRef.current, latest);
        runtimeRef.current = next;
        setRuntime(next);
      } catch {
        // 挂起期间单次回读失败不得中断后续轮询，否则工作台会停在过期摘要。
      }
    };
    void refresh();
    timer = setInterval(() => {
      void refresh();
    }, 2_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearInterval(timer);
    };
  }, [detail?.conversation.conversation_id, runtime.snapshot?.status]);

  const findNewerRun = useCallback(async (conversationId: string, runId: string): Promise<string | null> => {
    /** 仅在当前 SSE 结束后回读一次会话索引，自动恢复 Run 不再触发定时完整会话轮询。 */

    const latest = await getConversation(conversationId);
    const nextRunId = latestRunId(latest);
    if (nextRunId === null || nextRunId === runId) return null;
    setDetail((current) => current?.conversation.conversation_id === conversationId ? latest : current);
    return nextRunId;
  }, []);

  const startEventStream = useCallback(async (conversationId: string, runId: string) => {
    /** 从已 hydrate 的 sequence 订阅；实时更新只消费 SSE，Snapshot 仅用于恢复与权威收敛。 */

    stopStream();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    try {
      await reconnectingEventStream(conversationId, runId, controller.signal, {
        getAfterSequence: () => runtimeRef.current.snapshot?.last_sequence ?? 0,
        shouldContinue: () => !isTerminalSnapshot(runtimeRef.current.snapshot),
        onConnecting: (reconnecting) => {
          setRuntime((current) => setConnection(current, reconnecting ? "reconnecting" : "connecting"));
        },
        onEvent: async (event) => {
          const [next, result] = applyPublicEvent(runtimeRef.current, event);
          runtimeRef.current = next;
          queueRuntimeRender(next);
          if (shouldReloadSnapshot(event, result)) {
            const keepStreaming = await hydrateRun(conversationId, runId);
            // Tool 完成只需以 Snapshot 收敛 Workspace，当前 SSE 仍可继续消费后续 delta。
            // 仅 sequence gap 或 Run 已终态时才断开重连，避免浏览器持续显示 canceled。
            return result === "gap" || !keepStreaming ? "reload" : "continue";
          }
          return "continue";
        },
        onDisconnected: () => setRuntime((current) => setConnection(current, "disconnected")),
      });
    } finally {
      // SSE 在代理断开或终态事件丢失时仍须回读权威 Snapshot，避免任务看板永久停在运行中。
      const active = runtimeRef.current.snapshot;
      if (!controller.signal.aborted && active?.run_id === runId) {
        try {
          const keepStreaming = await hydrateRun(conversationId, runId);
          if (keepStreaming) return;
          const newerRunId = await findNewerRun(conversationId, runId);
          if (newerRunId === null) return;
          if (await hydrateRun(conversationId, newerRunId)) {
            void startEventStream(conversationId, newerRunId);
          }
        } catch {
          // 流已经结束时不让补偿读取变成未处理 Promise；用户仍可使用“刷新”回读。
          setRuntime((current) => setConnection(current, "disconnected"));
        }
      }
    }
  }, [findNewerRun, hydrateRun, queueRuntimeRender, stopStream]);

  const openConversation = useCallback(async (conversationId: string) => {
    /** 使用 generation 防止 A/B 快速切换时旧请求覆盖当前会话。 */

    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    stopStream();
    pendingTurnRef.current = null;
    confirmationResponseIdsRef.current.clear();
    setLoading(true);
    setError("");
    runtimeRef.current = initialAgentWorkspaceState;
    setRuntime(initialAgentWorkspaceState);
    try {
      const [next, workspace] = await Promise.all([
        getConversation(conversationId),
        getOrCreateVideoWorkspace(conversationId),
      ]);
      if (generation !== requestGenerationRef.current) return;
      setDetail(next);
      const nextRuntime = replaceVideoWorkspace({
        ...runtimeRef.current,
        conversationId,
        messages: next.messages.map((message) => ({
          message_id: message.message_id,
          role: message.role,
          content: message.content,
        })),
        connection: "idle",
      }, workspace);
      runtimeRef.current = nextRuntime;
      setRuntime(nextRuntime);
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

  const newConversation = useCallback(async (): Promise<ConversationV1> => {
    /** 新会话创建完成后回读服务端状态，避免前端构造业务副本。 */

    setError("");
    try {
      const created = await createConversation();
      await refreshConversations();
      await openConversation(created.conversation_id);
      return created;
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
      throw caught;
    }
  }, [openConversation, refreshConversations]);

  const renameConversation = useCallback(async (conversation: ConversationV1, title: string) => {
    /** 会话标题是导航元数据，可独立于 Workspace 以 revision 安全更新。 */

    const normalized = title.replace(/\s+/gu, " ").trim().slice(0, 80);
    if (!normalized || normalized === conversation.title) return;
    try {
      const updated = await updateConversationTitle(conversation.conversation_id, normalized, conversation.revision);
      setConversations((items) => items.map((item) => item.conversation_id === updated.conversation_id ? updated : item));
      setDetail((current) => current?.conversation.conversation_id === updated.conversation_id
        ? { ...current, conversation: updated }
        : current);
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
      throw caught;
    }
  }, []);

  const submitTurn = useCallback(async (content: string, materials: TurnMaterialV1[] = []) => {
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
    const shouldGenerateTitle = DEFAULT_CONVERSATION_TITLES.has(detail.conversation.title) && trimmed.length > 0;
    if (shouldGenerateTitle) {
      try {
        await renameConversation(detail.conversation, titleFromFirstTurn(trimmed));
      } catch {
        // 标题仅用于导航；失败不能阻断用户的主请求。
      }
    }
    const pending = pendingTurnRef.current;
    const clientInputId = pending !== null && pending.content === trimmed
      ? pending.client_input_id
      : createClientUuid();
    pendingTurnRef.current = { client_input_id: clientInputId, content: trimmed };
    const turn: TurnStartV1 = {
      client_input_id: clientInputId,
      workspace_id: workspace.workspace_id,
      expected_workspace_revision: workspace.revision,
      content: trimmed,
      ...(materials.length > 0 ? { materials } : {}),
    };
    setError("");
    const optimisticMessage: PublicMessageV1 = { message_id: `pending:${clientInputId}`, role: "user", content: trimmed };
    const optimisticRuntime = {
      ...runtimeRef.current,
      inputStatus: "sending" as const,
      messages: [...runtimeRef.current.messages, optimisticMessage],
    };
    runtimeRef.current = optimisticRuntime;
    setRuntime(optimisticRuntime);
    try {
      const started = await startHarnessTurn(detail.conversation.conversation_id, turn);
      pendingTurnRef.current = null;
      const acceptedRuntime = replaceVideoWorkspace(runtimeRef.current, {
        ...workspace,
        revision: started.workspace_revision,
      });
      const acceptedWorkspace = {
        ...workspace,
        revision: started.workspace_revision,
      };
      // 不等待 Snapshot 首次回读才建立可见状态。即便网络暂时阻塞，用户也能看到
      // 任务已受理；后续 SSE/Snapshot 会以 Gateway 权威数据覆盖这份传输态占位。
      const acceptedWithRun: AgentWorkspaceState = {
        ...acceptedRuntime,
        snapshot: acceptedRunSnapshot(
          detail.conversation.conversation_id,
          started.run_id,
          acceptedWorkspace,
        ),
        currentRun: { runId: started.run_id, status: "accepted" },
        inputStatus: "processing",
        thinkingStreamsByRun: {
          ...acceptedRuntime.thinkingStreamsByRun,
          [started.run_id]: "任务已受理，正在分析你的请求并核对工作区。",
        },
      };
      runtimeRef.current = acceptedWithRun;
      setRuntime(acceptedWithRun);
      void getConversation(detail.conversation.conversation_id).then((latestDetail) => {
        setDetail(latestDetail);
        const persistedMessages = latestDetail.messages.map((message) => ({
          message_id: message.message_id,
          role: message.role,
          content: message.content,
        }));
        const next = {
          ...runtimeRef.current,
          messages: [
            ...runtimeRef.current.messages.filter((message) => !(
              message.message_id === `pending:${clientInputId}`
              && persistedMessages.some((persisted) => persisted.role === "user" && persisted.content === message.content)
            )),
            ...persistedMessages.filter((persisted) => !runtimeRef.current.messages.some((message) => message.message_id === persisted.message_id)),
          ],
        };
        runtimeRef.current = next;
        setRuntime(next);
      }).catch(() => undefined);
      void (async () => {
        // SSE 从零开始可在 Snapshot 慢或暂时不可用时持续展示真实公开事件；
        // Snapshot 仅用于权威收敛，失败不能让用户界面停在无反馈状态。
        void startEventStream(detail.conversation.conversation_id, started.run_id);
        try {
          await hydrateRun(detail.conversation.conversation_id, started.run_id);
        } catch {
          const reconnecting = setConnection(runtimeRef.current, "reconnecting");
          runtimeRef.current = reconnecting;
          setRuntime(reconnecting);
        }
      })();
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
  }, [detail, hydrateRun, renameConversation, runtime.videoWorkspace, startEventStream]);

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

  const recoverActiveRun = useCallback(async () => {
    /** 只由用户显式触发恢复；后端会以 recovery_event_id 去重并校验旧 Run 无副作用。 */

    if (detail === null || runtime.snapshot === null) return;
    const { conversation_id: conversationId } = detail.conversation;
    const { run_id: runId } = runtime.snapshot;
    setRecoveringRunId(runId);
    setError("");
    try {
      const recovered = await recoverHarnessRun(conversationId, runId);
      if (await hydrateRun(conversationId, recovered.recovery_run_id)) {
        void startEventStream(conversationId, recovered.recovery_run_id);
      }
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
      throw caught;
    } finally {
      setRecoveringRunId(null);
    }
  }, [detail, hydrateRun, runtime.snapshot, startEventStream]);

  const submitWorkspaceCommand = useCallback(async (command: WorkspaceCommandV1) => {
    /** 命令成功后立即回读 Snapshot，浏览器不以返回体维护第二份工作区状态。 */

    if (detail === null) throw new Error("conversation_unselected");
    try {
      const result = await applyHarnessWorkspaceCommand(detail.conversation.conversation_id, command);
      setRuntime((current) => replaceVideoWorkspace(current, result.workspace));
      await refreshActiveRun();
    } catch (caught) {
      const apiError = caught instanceof AgentApiError ? caught : null;
      if (apiError?.code === "harness_workspace_revision_conflict") {
        try {
          const latest = await getOrCreateVideoWorkspace(detail.conversation.conversation_id);
          const next = replaceVideoWorkspace(runtimeRef.current, latest);
          runtimeRef.current = next;
          setRuntime(next);
        } catch {
          // 仍保留原始冲突语义，面板负责保留本地草稿并提示人工合并。
        }
        setError("Workspace 已更新到新版本，本地草稿已保留，请合并后重试。");
      } else {
        setError("工作区修改未完成，请刷新后确认当前版本。");
      }
      throw caught;
    }
  }, [detail, refreshActiveRun]);

  const updateScript = useCallback(async (content: string) => {
    /** 脚本复用现有 Workspace Command，严格以当前 Workspace revision 写入。 */

    const workspace = runtime.videoWorkspace;
    if (workspace === null) throw new Error("harness_workspace_not_found");
    await submitWorkspaceCommand({
      client_command_id: createClientUuid(),
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

  const confirmInterrupt = useCallback(async (interruptId: string) => {
    /** 同一中断重试复用 client_response_id；409 会刷新权威 revision，但不丢弃提交身份。 */

    const workspace = runtime.videoWorkspace;
    if (detail === null || workspace === null) throw new Error("harness_workspace_not_found");
    const clientResponseId = confirmationResponseIdsRef.current.get(interruptId) ?? createClientUuid();
    confirmationResponseIdsRef.current.set(interruptId, clientResponseId);
    setConfirmationSubmittingId(interruptId);
    setError("");
    try {
      const confirmed = await respondToHarnessInterrupt(
        detail.conversation.conversation_id,
        workspace.workspace_id,
        interruptId,
        {
          client_response_id: clientResponseId,
          expected_workspace_revision: workspace.revision,
          action: "confirm",
        },
      );
      confirmationResponseIdsRef.current.delete(interruptId);
      if (confirmed.run_id === null) throw new Error("harness_interrupt_resume_missing");
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

  const submitFormInterrupt = useCallback(async (
    interruptId: string,
    content: string,
    cancelled: boolean,
  ) => {
    /** 表单关闭与提交共用同一幂等响应身份；关闭不会创建恢复 Run。 */

    const workspace = runtime.videoWorkspace;
    if (detail === null || workspace === null) throw new Error("harness_workspace_not_found");
    const clientResponseId = confirmationResponseIdsRef.current.get(interruptId) ?? createClientUuid();
    confirmationResponseIdsRef.current.set(interruptId, clientResponseId);
    setConfirmationSubmittingId(interruptId);
    setError("");
    try {
      const resumed = await respondToHarnessInterrupt(
        detail.conversation.conversation_id,
        workspace.workspace_id,
        interruptId,
        {
          client_response_id: clientResponseId,
          expected_workspace_revision: workspace.revision,
          action: cancelled ? "form_cancelled" : "submit",
          ...(cancelled ? {} : { content: content.trim() }),
        },
      );
      confirmationResponseIdsRef.current.delete(interruptId);
      if (resumed.run_id !== null) {
        await hydrateRun(detail.conversation.conversation_id, resumed.run_id);
        void startEventStream(detail.conversation.conversation_id, resumed.run_id);
      } else {
        await refreshActiveRun();
      }
    } catch (caught) {
      const apiError = caught instanceof AgentApiError ? caught : null;
      if (apiError?.status === 409) {
        try {
          const latest = await getOrCreateVideoWorkspace(detail.conversation.conversation_id);
          setRuntime((current) => replaceVideoWorkspace(current, latest));
        } catch {
          // 保留草稿和稳定提交身份，供用户在刷新后显式重试。
        }
      }
      setError(publicErrorMessage(apiError?.code));
      throw caught;
    } finally {
      setConfirmationSubmittingId(null);
    }
  }, [detail, hydrateRun, refreshActiveRun, runtime.videoWorkspace, startEventStream]);

  const resumeAuthorizationInterrupt = useCallback(async (interruptId: string) => {
    const workspace = runtime.videoWorkspace;
    if (detail === null || workspace === null) throw new Error("harness_workspace_not_found");
    const clientResponseId = confirmationResponseIdsRef.current.get(interruptId) ?? createClientUuid();
    confirmationResponseIdsRef.current.set(interruptId, clientResponseId);
    setConfirmationSubmittingId(interruptId);
    try {
      const resumed = await resumeHarnessInterruptAuthorization(
        detail.conversation.conversation_id, workspace.workspace_id, interruptId,
        { client_response_id: clientResponseId, expected_workspace_revision: workspace.revision },
      );
      confirmationResponseIdsRef.current.delete(interruptId);
      if (resumed.run_id !== null) {
        await hydrateRun(detail.conversation.conversation_id, resumed.run_id);
        void startEventStream(detail.conversation.conversation_id, resumed.run_id);
      }
    } catch (caught) {
      setError(publicErrorMessage(caught instanceof AgentApiError ? caught.code : undefined));
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
    renameConversation,
    openConversation,
    submitTurn,
    submitWorkspaceCommand,
    updateScript,
    updatePlanPublicGoal,
    confirmInterrupt,
    submitFormInterrupt,
    resumeAuthorizationInterrupt,
    confirmationSubmittingId,
    refreshActiveRun,
    cancelActiveRun,
    recoverActiveRun,
    recoveringRunId,
  };
}
