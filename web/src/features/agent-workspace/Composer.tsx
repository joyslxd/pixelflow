/** F1 输入框：不接手填 workspace_id；旧对话只读，运行中新输入显示已排队。 */

import { useEffect, useRef, useState } from "react";

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
  const formRef = useRef<HTMLFormElement | null>(null);
  const submittingRef = useRef(false);

  const submitContent = async (form: HTMLFormElement) => {
    /** 浏览器恢复的输入值可能尚未触发 React onChange，发送时以表单当前值为准。 */

    const value = new FormData(form).get("content");
    const content = typeof value === "string" ? value.trim() : input.trim();
    if (!canSend || sending || submittingRef.current || !content) return;
    submittingRef.current = true;
    try {
      await onSubmit(content);
      setInput("");
    } catch {
      // 权威状态由 Hook 保留；草稿留在输入框供用户显式重试。
    } finally {
      submittingRef.current = false;
    }
  };

  useEffect(() => {
    /** 直接监听原生 submit，兼容浏览器恢复表单和 React 委托事件不可达的场景。 */

    const form = formRef.current;
    if (form === null) return undefined;
    const handleSubmit = (event: SubmitEvent) => {
      event.preventDefault();
      void submitContent(form);
    };
    form.addEventListener("submit", handleSubmit);
    return () => form.removeEventListener("submit", handleSubmit);
  }, [submitContent]);

  if (!canSend) {
    return (
      <p className="rounded border border-line px-3 py-2 text-sm text-ink-soft">
        {disabledReason || "旧对话仅供查看，请基于产物创建新对话。"}
      </p>
    );
  }

  return (
    <form ref={formRef}>
      {inputStatus === "queued" ? (
        <p className="mb-2 text-xs text-ink-soft" aria-live="polite">新输入已排队</p>
      ) : null}
      <div className="flex gap-2">
        <input
          name="content"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入给 Agent"
          className="min-w-0 flex-1 rounded border border-line px-3 py-2"
        />
        <button
          type="submit"
          disabled={sending}
          className="rounded bg-accent px-4 text-white disabled:opacity-50"
        >
          发送
        </button>
      </div>
    </form>
  );
}
