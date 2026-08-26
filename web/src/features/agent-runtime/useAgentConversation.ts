/** 会话切换、Snapshot hydrate 与 SSE 生命周期 Hook。 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelHarnessRun,
  getHarnessSnapshot,
  startHarnessTurn,
} from "@/api/agentRuntime";
import type { TurnStartV1 } from "@/api/contracts";
import {
  createConversation,
  getConversation,
  listConversations,
  type ConversationDetailV1,
  type ConversationV1,
} from "@/api/conversations";

import { reconnectingEventStream } from "./eventStream";
import {
  applyPublicEvent,
  hydrateSnapshot,
  initialAgentWorkspaceState,
  isTerminalSnapshot,
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
    setRuntime(hydrateSnapshot(snapshot));
    return !isTerminalSnapshot(snapshot);
  }, []);

  const startEventStream = useCallback(async (conversationId: string, runId: string) => {
    /** 从已 hydrate 的 sequence 订阅，重连/gap 永远回读 Snapshot。 */

    stopStream();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    await reconnectingEventStream(conversationId, runId, controller.signal, {
      getAfterSequence: () => runtimeRef.current.snapshot?.last_sequence ?? 0,
      shouldContinue: () => !isTerminalSnapshot(runtimeRef.current.snapshot),
      onConnecting: (reconnecting) => {
        setRuntime((current) => ({ ...current, connection: reconnecting ? "reconnecting" : "connecting" }));
      },
      onEvent: async (event) => {
        const [next, result] = applyPublicEvent(runtimeRef.current, event);
        runtimeRef.current = next;
        setRuntime(next);
        if (result === "gap") {
          await hydrateRun(conversationId, runId);
          return "reload";
        }
        // 事件是刷新触发器而不是浏览器轮询；完整消息与业务投影只从 Snapshot 回读。
        await hydrateRun(conversationId, runId);
        return "continue";
      },
      onDisconnected: () => setRuntime((current) => ({ ...current, connection: "disconnected" })),
    });
  }, [hydrateRun, stopStream]);

  const openConversation = useCallback(async (conversationId: string) => {
    /** 使用 generation 防止 A/B 快速切换时旧请求覆盖当前会话。 */

    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    stopStream();
    setLoading(true);
    setError("");
    setRuntime(initialAgentWorkspaceState);
    try {
      const next = await getConversation(conversationId);
      if (generation !== requestGenerationRef.current) return;
      setDetail(next);
      const runId = latestRunId(next);
      if (runId !== null && await hydrateRun(conversationId, runId)) {
        if (generation === requestGenerationRef.current) void startEventStream(conversationId, runId);
      }
    } catch {
      if (generation === requestGenerationRef.current) setError("无法恢复对话，请稍后刷新。");
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
    } catch {
      setError("无法创建新对话，请检查登录状态。");
    }
  }, [openConversation, refreshConversations]);

  const submitTurn = useCallback(async (turn: TurnStartV1) => {
    /** 提交一个冻结 Turn；重复网络重试由调用方复用同一个 client_input_id。 */

    if (detail === null) throw new Error("conversation_unselected");
    setError("");
    const started = await startHarnessTurn(detail.conversation.conversation_id, turn);
    const nextDetail = await getConversation(detail.conversation.conversation_id);
    setDetail(nextDetail);
    await hydrateRun(detail.conversation.conversation_id, started.run_id);
    void startEventStream(detail.conversation.conversation_id, started.run_id);
  }, [detail, hydrateRun, startEventStream]);

  const refreshActiveRun = useCallback(async () => {
    /** 用户主动刷新可恢复事实，不创建新的 Run 或重新发送输入。 */

    if (detail === null || runtime.snapshot === null) return;
    try {
      const keepStreaming = await hydrateRun(detail.conversation.conversation_id, runtime.snapshot.run_id);
      if (keepStreaming) void startEventStream(detail.conversation.conversation_id, runtime.snapshot.run_id);
    } catch {
      setError("无法刷新当前运行状态。");
    }
  }, [detail, hydrateRun, runtime.snapshot, startEventStream]);

  const cancelActiveRun = useCallback(async () => {
    /** 取消只请求 Gateway；终态仍从 Snapshot/SSE 回填。 */

    if (detail === null || runtime.snapshot === null) return;
    try {
      await cancelHarnessRun(detail.conversation.conversation_id, runtime.snapshot.run_id);
      await refreshActiveRun();
    } catch {
      setError("取消请求未完成，请刷新确认当前状态。");
    }
  }, [detail, refreshActiveRun, runtime.snapshot]);

  useEffect(() => {
    void refreshConversations().catch(() => setError("无法加载对话，请检查登录状态。"));
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
    newConversation,
    openConversation,
    submitTurn,
    refreshActiveRun,
    cancelActiveRun,
  };
}
