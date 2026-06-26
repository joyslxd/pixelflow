import { Play } from "lucide-react";
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
  // 这里的 url 必须是浏览器可访问地址。若后端返回本地文件路径（如 FFmpeg 本地输出），
  // <video> 无法直接播放，需要后续 artifact/static 服务转换成 HTTP URL。
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
