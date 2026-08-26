/** 新 Runtime 工作台组合根：消息、公开进度与只读 Workspace 均从 Snapshot 投影。 */

import { type FormEvent, useMemo, useState } from "react";

import type { InterruptResponseV1, TurnStartV1, WorkspaceCommandV1 } from "@/api/contracts";

import { useAgentConversation } from "./useAgentConversation";

type AgentWorkspaceProps = {
  conversationId?: string;
};

function statusLabel(status: string | undefined): string {
  /** 把固定 Run 状态映射为用户可读文本，不暴露 Harness 内部概念。 */

  return ({ accepted: "已受理", running: "正在处理", completed: "已完成", failed: "处理失败", cancelled: "已取消" } as Record<string, string>)[status ?? ""] ?? "未启动";
}

/** 只消费 Gateway 公开 Snapshot/SSE 的新工作台，不保留旧任务轮询或业务状态副本。 */
export function AgentWorkspace({ conversationId }: AgentWorkspaceProps) {
  const {
    conversations,
    detail,
    runtime,
    error,
    loading,
    newConversation,
    openConversation,
    submitTurn,
    submitWorkspaceCommand,
    cancelQuotaInterrupt,
    refreshActiveRun,
    cancelActiveRun,
  } = useAgentConversation(conversationId);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceRevision, setWorkspaceRevision] = useState("1");
  const [input, setInput] = useState("");
  const [workspacePatch, setWorkspacePatch] = useState("{}");
  const [sending, setSending] = useState(false);
  const [savingWorkspace, setSavingWorkspace] = useState(false);
  const snapshot = runtime.snapshot;
  const milestones = useMemo(
    () => snapshot?.events.map((event) => `${event.sequence}. ${event.type}`).join("\n") ?? "等待公开进度",
    [snapshot],
  );

  const submit = async (event: FormEvent) => {
    /** 同一提交只创建一次 UUID；请求失败后不清空输入，调用方可复用原草稿重试。 */

    event.preventDefault();
    if (!detail || !input.trim() || !workspaceId.trim() || sending) return;
    const revision = Number(workspaceRevision);
    if (!Number.isSafeInteger(revision) || revision < 1) return;
    const turn: TurnStartV1 = {
      client_input_id: crypto.randomUUID(),
      workspace_id: workspaceId.trim(),
      expected_workspace_revision: revision,
      content: input.trim(),
    };
    setSending(true);
    try {
      await submitTurn(turn);
      setInput("");
    } catch {
      // Hook 已保留权威 Snapshot；草稿留在输入框供用户显式重试。
    } finally {
      setSending(false);
    }
  };

  const submitWorkspacePatch = async (event: FormEvent) => {
    /** Workspace Command 只提交 JSON patch；成功后由 Hook 回读唯一 Snapshot。 */

    event.preventDefault();
    const workspace = snapshot?.workspace;
    if (!workspace || savingWorkspace) return;
    let patch: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(workspacePatch);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return;
      patch = parsed as Record<string, unknown>;
    } catch {
      return;
    }
    const command: WorkspaceCommandV1 = {
      client_command_id: crypto.randomUUID(),
      workspace_id: workspace.workspace_id,
      expected_workspace_revision: workspace.revision,
      patch,
    };
    setSavingWorkspace(true);
    try {
      await submitWorkspaceCommand(command);
      setWorkspacePatch("{}");
    } catch {
      // Hook 已保留安全错误提示；用户可在刷新 Snapshot 后修正 patch 并重试。
    } finally {
      setSavingWorkspace(false);
    }
  };

  const cancelQuotaInterruptedPlan = async () => {
    /** 仅为已投影的 M06 额度中断创建 cancel_workflow 响应。 */

    const workspace = snapshot?.workspace;
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
    <main className="grid h-full min-h-0 grid-cols-[220px_minmax(0,1fr)_300px] gap-px bg-line">
      <aside className="min-h-0 overflow-y-auto bg-surface p-4">
        <button className="w-full rounded-lg bg-brand px-3 py-2 text-sm text-white" onClick={() => void newConversation()}>
          新建对话
        </button>
        <div className="mt-4 space-y-1">
          {conversations.map((item) => (
            <button
              key={item.conversation_id}
              onClick={() => void openConversation(item.conversation_id)}
              className={`block w-full rounded px-2 py-2 text-left text-sm hover:bg-accent-soft ${detail?.conversation.conversation_id === item.conversation_id ? "bg-accent-soft" : ""}`}
            >
              {item.title || item.conversation_id}
            </button>
          ))}
        </div>
      </aside>
      <section className="flex min-w-0 flex-col bg-surface">
        <header className="flex items-center justify-between border-b border-line px-5 py-3 text-sm">
          <span className="truncate font-medium">{detail?.conversation.title || "选择或新建对话"}</span>
          <span className="text-ink-soft">{runtime.connection === "reconnecting" ? "正在重连" : runtime.connection === "connected" ? "已连接" : runtime.connection === "disconnected" ? "连接已断开" : ""}</span>
        </header>
        <div className="flex-1 space-y-3 overflow-y-auto p-6">
          {loading ? <p className="text-sm text-ink-soft">正在恢复权威状态…</p> : null}
          {detail?.messages.map((message) => (
            <p key={message.message_id} className={message.role === "user" ? "text-right" : "text-left"}>{message.content}</p>
          ))}
          {snapshot?.messages.map((message, index) => (
            <p key={message.message_id ?? `${snapshot.run_id}-${index}`} className="text-left">{message.content}</p>
          ))}
        </div>
        <div className="border-t border-line p-4">
          <div className="mb-2 rounded bg-accent-soft px-3 py-2 text-xs text-ink-soft" aria-live="polite">
            任务看板：{statusLabel(snapshot?.status)}
          </div>
          <form onSubmit={submit}>
            <div className="mb-2 grid grid-cols-2 gap-2">
              <input value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} placeholder="工作区 ID" className="rounded border border-line px-3 py-2 text-sm" />
              <input value={workspaceRevision} onChange={(event) => setWorkspaceRevision(event.target.value)} inputMode="numeric" placeholder="工作区 revision" className="rounded border border-line px-3 py-2 text-sm" />
            </div>
            <div className="flex gap-2">
              <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入给 Agent" className="min-w-0 flex-1 rounded border border-line px-3 py-2" />
              <button disabled={!detail || sending} className="rounded bg-accent px-4 text-white disabled:opacity-50">发送</button>
            </div>
          </form>
          {error ? <p className="mt-2 text-sm text-red-600" role="alert">{error}</p> : null}
        </div>
      </section>
      <aside className="min-h-0 overflow-y-auto bg-surface p-4 text-sm">
        <div className="flex items-center justify-between gap-2">
          <p>运行：{snapshot?.run_id ?? "未启动"}</p>
          <button className="rounded border border-line px-2 py-1 text-xs" onClick={() => void refreshActiveRun()} disabled={!snapshot}>刷新</button>
        </div>
        <p className="mt-1">状态：{statusLabel(snapshot?.status)}</p>
        {snapshot && snapshot.status === "running" ? <button className="mt-3 rounded border border-red-200 px-2 py-1 text-xs text-red-700" onClick={() => void cancelActiveRun()}>取消当前运行</button> : null}
        <h2 className="mt-6 text-sm font-medium">公开进度</h2>
        <pre className="mt-2 whitespace-pre-wrap text-xs text-ink-soft">{milestones}</pre>
        <h2 className="mt-6 text-sm font-medium">业务工作区</h2>
        {snapshot?.workspace ? (
          <div className="mt-2 space-y-2 text-xs text-ink-soft">
            <p>版本：{snapshot.workspace.revision}</p>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-canvas p-2">{JSON.stringify(snapshot.workspace.summary, null, 2)}</pre>
            {typeof snapshot.workspace.summary.quota_interrupt_id === "string" ? (
              <div className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
                <p>额度中断：{String(snapshot.workspace.summary.quota_interrupt_reason_code ?? "需要人工处理")}</p>
                <button className="mt-2 rounded border border-amber-300 px-2 py-1" onClick={() => void cancelQuotaInterruptedPlan()}>取消该任务</button>
              </div>
            ) : null}
            <form className="space-y-2" onSubmit={submitWorkspacePatch}>
              <label className="block font-medium text-ink">工作区修改（JSON patch）</label>
              <textarea value={workspacePatch} onChange={(event) => setWorkspacePatch(event.target.value)} rows={4} className="w-full rounded border border-line p-2 font-mono text-xs" />
              <button disabled={savingWorkspace} className="rounded border border-line px-2 py-1 disabled:opacity-50">提交工作区修改</button>
            </form>
          </div>
        ) : <p className="mt-2 text-xs text-ink-soft">尚无可恢复的 Workspace 投影。Workspace Command 与 Interrupt 会在对应公开 Gateway 合同上线后接入，当前不会回退旧流程或旧轮询。</p>}
      </aside>
    </main>
  );
}
