import { useState } from "react";
import { Plus, SendHorizontal } from "lucide-react";

interface ComposerProps {
  onSubmit?: (text: string) => void;
  busy?: boolean;
}

/** 极简对话输入框。参数不在这里填 —— 检测到视频生成意图后再弹参数面板。 */
export function Composer({ onSubmit, busy }: ComposerProps) {
  const [text, setText] = useState("");
  const canSend = !busy && text.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSubmit?.(text.trim());
    setText("");
  };

  return (
    <div className="flex items-end gap-2 rounded-[20px] border border-line bg-surface p-2 pl-3 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_rgba(16,24,40,0.05)]">
      <button
        type="button"
        className="mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-soft hover:bg-canvas hover:text-ink"
        aria-label="添加素材"
      >
        <Plus size={18} />
      </button>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        rows={1}
        placeholder="说说你想做什么，例如：帮保温杯做一条冬季通勤的种草短视频"
        className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent py-2 text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-soft/60"
      />
      <button
        type="button"
        onClick={submit}
        disabled={!canSend}
        className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-white transition-opacity disabled:opacity-30"
        aria-label="发送"
      >
        <SendHorizontal size={17} />
      </button>
    </div>
  );
}
