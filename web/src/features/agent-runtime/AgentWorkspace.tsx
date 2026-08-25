import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { getBrowserAuthorization } from "@/lib/authStorage";
import type { AgentSnapshotV1, TurnStartV1 } from "@/api/contracts";

type Conversation = {
  conversation_id: string;
  title: string;
  revision: number;
};

type Message = {
  message_id: string;
  role: "user" | "assistant" | "system";
  content: string;
};

type Snapshot = AgentSnapshotV1;

const AGENT_BASE = "/agent/conversations";

function headers(): HeadersInit {
  const authorization = getBrowserAuthorization();
  return authorization ? { Authorization: authorization } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${AGENT_BASE}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers || {}) },
  });
  if (!response.ok) {
    throw new Error(`请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

/** 只消费 Gateway 公开 Snapshot/SSE 的新工作台，不保留旧任务轮询状态。 */
export function AgentWorkspace() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceRevision, setWorkspaceRevision] = useState("1");
  const [input, setInput] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const activeRunId = snapshot?.run_id || "";
  const eventSummary = useMemo(
    () => snapshot?.events.map((event) => `${event.sequence}. ${event.type}`).join("\n") || "等待 Run 事件",
    [snapshot],
  );

  const loadConversations = async () => {
    const result = await request<{ items: Conversation[] }>("?page_size=20");
    setConversations(result.items);
  };

  const loadConversation = async (conversationId: string) => {
    const detail = await request<{ conversation: Conversation; messages: Message[] }>(`/${conversationId}`);
    setConversation(detail.conversation);
    setMessages(detail.messages);
    setSnapshot(null);
    abortRef.current?.abort();
  };

  useEffect(() => {
    void loadConversations().catch(() => setError("无法加载对话，请检查登录状态。"));
    return () => abortRef.current?.abort();
  }, []);

  const createConversation = async () => {
    const created = await request<Conversation>("", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "新的 Harness 对话" }),
    });
    await loadConversations();
    await loadConversation(created.conversation_id);
  };

  const refreshSnapshot = async (conversationId: string, runId: string) => {
    const next = await request<Snapshot>(`/${conversationId}/harness-runs/${runId}/snapshot`);
    setSnapshot(next);
  };

  const consumeEvents = async (conversationId: string, runId: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const response = await fetch(
      `${AGENT_BASE}/${conversationId}/harness-runs/${runId}/events?after_sequence=0`,
      { headers: headers(), signal: controller.signal },
    );
    if (!response.ok || !response.body) throw new Error("无法连接 Harness 事件流。");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!controller.signal.aborted) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        const data = frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
        if (!data) continue;
        const event = JSON.parse(data) as { sequence: number };
        setSnapshot((current) => current && event.sequence > current.last_sequence
          ? { ...current, last_sequence: event.sequence }
          : current);
        await refreshSnapshot(conversationId, runId);
      }
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!conversation || !input.trim() || !workspaceId.trim()) return;
    setSending(true);
    setError("");
    try {
      const turn: TurnStartV1 = {
        client_input_id: crypto.randomUUID(),
        workspace_id: workspaceId.trim(),
        expected_workspace_revision: Number(workspaceRevision),
        content: input.trim(),
      };
      const started = await request<{ run_id: string }>(`/${conversation.conversation_id}/harness-turns/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(turn),
      });
      setMessages((current) => [...current, { message_id: crypto.randomUUID(), role: "user", content: input.trim() }]);
      setInput("");
      await refreshSnapshot(conversation.conversation_id, started.run_id);
      void consumeEvents(conversation.conversation_id, started.run_id).catch((reason) => {
        if ((reason as Error).name !== "AbortError") setError("事件流已断开，可刷新 Snapshot 恢复。");
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "启动 Harness Run 失败。");
    } finally {
      setSending(false);
    }
  };

  return (
    <main className="grid h-full grid-cols-[240px_minmax(0,1fr)_280px] gap-px bg-line">
      <aside className="bg-surface p-4">
        <button className="w-full rounded-lg bg-brand px-3 py-2 text-sm text-white" onClick={() => void createConversation()}>
          新建对话
        </button>
        <div className="mt-4 space-y-1">
          {conversations.map((item) => (
            <button key={item.conversation_id} onClick={() => void loadConversation(item.conversation_id)} className="block w-full rounded px-2 py-2 text-left text-sm hover:bg-accent-soft">
              {item.title || item.conversation_id}
            </button>
          ))}
        </div>
      </aside>
      <section className="flex min-w-0 flex-col bg-surface">
        <div className="flex-1 space-y-3 overflow-y-auto p-6">
          {messages.map((message) => <p key={message.message_id} className={message.role === "user" ? "text-right" : "text-left"}>{message.content}</p>)}
          {snapshot?.messages.map((message, index) => <p key={`${snapshot.run_id}-${index}`} className="text-left">{message.content}</p>)}
        </div>
        <form onSubmit={submit} className="border-t border-line p-4">
          <div className="mb-2 grid grid-cols-2 gap-2">
            <input value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} placeholder="工作区 ID" className="rounded border border-line px-3 py-2 text-sm" />
            <input value={workspaceRevision} onChange={(event) => setWorkspaceRevision(event.target.value)} inputMode="numeric" placeholder="工作区 revision" className="rounded border border-line px-3 py-2 text-sm" />
          </div>
          <div className="flex gap-2"><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入给 Harness Agent" className="min-w-0 flex-1 rounded border border-line px-3 py-2" /><button disabled={sending} className="rounded bg-accent px-4 text-white disabled:opacity-50">发送</button></div>
          {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
        </form>
      </section>
      <aside className="bg-surface p-4 text-sm"><p>Run：{activeRunId || "未启动"}</p><p>状态：{snapshot?.status || "idle"}</p><pre className="mt-3 whitespace-pre-wrap text-xs text-ink-soft">{eventSummary}</pre></aside>
    </main>
  );
}
