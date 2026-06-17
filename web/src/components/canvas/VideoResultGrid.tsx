import { Download, Play } from "lucide-react";
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
  const openVideo = (url: string) => window.open(url, "_blank", "noopener,noreferrer");
  const finalVideos = results.filter((r) => r.assetType === "final_video");
  const segmentVideos = results.filter((r) => r.assetType !== "final_video");
  const renderCard = (r: VideoResult) => (
    <div
      key={r.id}
      className="group relative aspect-[3/4] overflow-hidden rounded-xl border border-line bg-ink/90"
    >
      {r.url ? (
        <video
          src={r.url}
          poster={r.thumbUrl}
          className="h-full w-full object-cover"
          controls
          muted
          playsInline
          onClick={() => openVideo(r.url)}
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

      {r.assetType === "final_video" && (
        <span className="absolute left-1.5 top-1.5 rounded bg-brand px-1.5 py-0.5 text-[11px] font-medium text-white">
          剪辑成片
        </span>
      )}
      {r.durationSec != null && (
        <span className="absolute bottom-1.5 left-1.5 rounded bg-black/55 px-1.5 py-0.5 text-[11px] font-medium text-white">
          0:{String(r.durationSec).padStart(2, "0")}
        </span>
      )}
      <div className="absolute bottom-1.5 right-1.5 flex gap-1">
        {r.url && (
          <button
            type="button"
            onClick={() => openVideo(r.url)}
            className="rounded bg-black/45 p-1 text-white/85 hover:bg-black/65"
            aria-label="播放"
          >
            <Play size={13} />
          </button>
        )}
        <a
          className={cn("rounded bg-black/45 p-1 text-white/85 hover:bg-black/65", !r.url && "pointer-events-none opacity-40")}
          href={r.url || undefined}
          download
          target="_blank"
          rel="noreferrer"
          aria-label="下载"
        >
          <Download size={13} />
        </a>
      </div>

      <span
        className={cn(
          "absolute left-1.5 top-1.5 h-2 w-2 rounded-full",
          r.assetType === "final_video" && "left-auto right-1.5 top-1.5",
          r.status === "success" && "bg-emerald",
          r.status === "pending" && "bg-amber animate-pulse",
          r.status === "failed" && "bg-rose-500",
        )}
      />
    </div>
  );
  return (
    <div className="space-y-3">
      <StatusLine results={results} />
      {finalVideos.length > 0 && (
        <section className="space-y-2">
          <div className="text-[13px] font-semibold text-ink">剪辑成片</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">{finalVideos.map(renderCard)}</div>
        </section>
      )}
      {segmentVideos.length > 0 && (
        <section className="space-y-2">
          <div className="text-[13px] font-semibold text-ink">分镜片段</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">{segmentVideos.map(renderCard)}</div>
        </section>
      )}
    </div>
  );
}
