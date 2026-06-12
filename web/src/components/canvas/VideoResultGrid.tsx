import { Download, Play, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import type { VideoResult } from "@/lib/types";

function StatusLine({ results }: { results: VideoResult[] }) {
  const ok = results.filter((r) => r.status === "success").length;
  const pending = results.filter((r) => r.status === "pending").length;
  const failed = results.filter((r) => r.status === "failed").length;
  return (
    <p className="text-[13px] text-ink-soft">
      本次任务共 {results.length} 条结果:{pending} 生成中,{ok} 条成功,{failed} 条失败。
    </p>
  );
}

export function VideoResultGrid({ results }: { results: VideoResult[] }) {
  return (
    <div className="space-y-3">
      <StatusLine results={results} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {results.map((r) => (
          <div
            key={r.id}
            className="group relative aspect-[3/4] overflow-hidden rounded-xl border border-line bg-ink/90"
          >
            {r.url ? (
              <video
                src={r.url}
                poster={r.thumbUrl}
                className="h-full w-full object-cover"
                muted
                playsInline
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-slate-700 to-slate-900">
                {r.status === "pending" ? (
                  <span className="text-[12px] text-white/70">生成中…</span>
                ) : (
                  <Play size={22} className="text-white/80" />
                )}
              </div>
            )}

            {r.durationSec != null && (
              <span className="absolute bottom-1.5 left-1.5 rounded bg-black/55 px-1.5 py-0.5 text-[11px] font-medium text-white">
                0:{String(r.durationSec).padStart(2, "0")}
              </span>
            )}
            <div className="absolute bottom-1.5 right-1.5 flex gap-1">
              <button className="rounded bg-black/45 p-1 text-white/85 hover:bg-black/65" aria-label="静音">
                <VolumeX size={13} />
              </button>
              <button className="rounded bg-black/45 p-1 text-white/85 hover:bg-black/65" aria-label="下载">
                <Download size={13} />
              </button>
            </div>

            <span
              className={cn(
                "absolute left-1.5 top-1.5 h-2 w-2 rounded-full",
                r.status === "success" && "bg-emerald",
                r.status === "pending" && "bg-amber animate-pulse",
                r.status === "failed" && "bg-rose-500",
              )}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
