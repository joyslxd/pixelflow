/** F1 输入框：不接手填 workspace_id；旧对话只读，运行中新输入显示已排队。 */

import { type FormEvent, useState } from "react";

import type { InputStatus } from "@/features/agent-runtime/state";

type ComposerProps = {
  canSend: boolean;
  sending: boolean;
  inputStatus: InputStatus;
  disabledReason?: string;
  onSubmit: (content: string) => Promise<void>;
};

export function Composer({
  canSend,
  sending,
  inputStatus,
  disabledReason,
  onSubmit,
}: ComposerProps) {
  const [input, setInput] = useState("");

  const submit = async (event: FormEvent) => {
    /** 发送失败保留草稿，由 Hook 复用同一 client_input_id。 */

    event.preventDefault();
    if (!canSend || sending || !input.trim()) return;
    try {
      await onSubmit(input);
      setInput("");
    } catch {
      // 权威状态由 Hook 保留；草稿留在输入框供用户显式重试。
    }
  };

  if (!canSend) {
    return (
      <p className="rounded border border-line px-3 py-2 text-sm text-ink-soft">
        {disabledReason || "旧对话仅供查看，请基于产物创建新对话。"}
      </p>
    );
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      {inputStatus === "queued" ? (
        <p className="mb-2 text-xs text-ink-soft" aria-live="polite">新输入已排队</p>
      ) : null}
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入给 Agent"
          className="min-w-0 flex-1 rounded border border-line px-3 py-2"
        />
        <button disabled={sending} className="rounded bg-accent px-4 text-white disabled:opacity-50">
          发送
        </button>
      </div>
    </form>
  );
}
