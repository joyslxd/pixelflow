import { useState } from "react";
import {
  Plus,
  Video,
  Sparkles,
  Layers,
  Proportions,
  MonitorPlay,
  Clock,
  Hash,
  Volume2,
  SendHorizontal,
} from "lucide-react";
import { Chip } from "./Chip";
import type { GenParams } from "@/lib/types";

const DEFAULT_PARAMS: GenParams = {
  mode: "视频生成",
  model: "seedance-2.0",
  reference: "全能参考",
  ratio: "9:16",
  resolution: "1080p",
  durationSec: 5,
  count: 1,
  sound: true,
};

interface ComposerProps {
  onSubmit?: (prompt: string, params: GenParams) => void;
}

export function Composer({ onSubmit }: ComposerProps) {
  const [prompt, setPrompt] = useState("");
  const [params, setParams] = useState<GenParams>(DEFAULT_PARAMS);
  const canSend = prompt.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSubmit?.(prompt.trim(), params);
    setPrompt("");
  };

  return (
    <div className="rounded-[20px] border border-line bg-surface p-3 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_8px_24px_rgba(16,24,40,0.05)]">
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        }}
        placeholder="描述你想要的画面 / 分镜，支持 @ 引用素材。例：45度俯拍，黑色刀柄水果刀切开果肉，切口汁水淌在木板上…"
        className="h-[120px] w-full resize-none bg-transparent px-2 pt-1.5 text-[15px] leading-relaxed text-ink outline-none placeholder:text-ink-soft/60"
      />

      <div className="mt-1 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center rounded-full border border-line text-ink-soft hover:text-ink"
          aria-label="添加素材"
        >
          <Plus size={16} />
        </button>

        <Chip icon={Video} dropdown active>
          {params.mode}
        </Chip>
        <Chip icon={Sparkles} dropdown>
          {params.model}
        </Chip>
        <Chip icon={Layers} dropdown>
          {params.reference}
        </Chip>
        <Chip icon={Proportions} dropdown>
          {params.ratio}
        </Chip>
        <Chip icon={MonitorPlay} dropdown>
          {params.resolution}
        </Chip>
        <Chip icon={Clock} dropdown>
          {params.durationSec} 秒
        </Chip>
        <Chip icon={Hash} dropdown>
          {params.count} 个
        </Chip>
        <Chip
          icon={Volume2}
          active={params.sound}
          onClick={() => setParams((p) => ({ ...p, sound: !p.sound }))}
        >
          输出声音
        </Chip>

        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-full bg-brand text-white transition-opacity disabled:opacity-30"
          aria-label="生成"
        >
          <SendHorizontal size={17} />
        </button>
      </div>
    </div>
  );
}
