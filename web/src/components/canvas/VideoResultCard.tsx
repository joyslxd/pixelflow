import { Download, Film, Volume2, VolumeX } from "lucide-react";
import { useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { VideoResult } from "@/lib/types";

export function formatVideoDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "--:--";
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function videoDownloadName(result: VideoResult): string {
  const cleanId = (result.title || result.id).replace(/[^\w.-]+/g, "-") || "video";
  return cleanId.toLowerCase().endsWith(".mp4") ? cleanId : `${cleanId}.mp4`;
}

export function VideoResultCard({
  result,
  className,
  mediaClassName,
  onOpen,
}: {
  result: VideoResult;
  className?: string;
  mediaClassName?: string;
  onOpen?: (result: VideoResult) => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [metadataDuration, setMetadataDuration] = useState<number | null>(null);
  const [muted, setMuted] = useState(true);
  const duration = result.durationSec ?? metadataDuration;
  const hasVideo = Boolean(result.url);

  const playPreview = () => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = muted;
    void video.play().catch(() => {});
  };

  const pausePreview = () => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
  };

  const toggleMuted = () => {
    setMuted((current) => {
      const next = !current;
      if (videoRef.current) videoRef.current.muted = next;
      return next;
    });
  };

  return (
    <div
      role="button"
      tabIndex={0}
      className={cn("group/video relative aspect-[9/16] overflow-hidden rounded-lg border border-line bg-ink text-left focus:outline-none focus:ring-2 focus:ring-accent/40", className)}
      onPointerEnter={playPreview}
      onPointerLeave={pausePreview}
      onFocus={playPreview}
      onBlur={pausePreview}
      onClick={() => onOpen?.(result)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onOpen?.(result);
      }}
    >
      {hasVideo ? (
        <video
          ref={videoRef}
          src={result.url}
          poster={result.thumbUrl}
          className={cn("h-full w-full object-cover", mediaClassName)}
          muted={muted}
          playsInline
          preload="auto"
          onLoadedMetadata={(event) => {
            const nextDuration = event.currentTarget.duration;
            if (Number.isFinite(nextDuration)) setMetadataDuration(nextDuration);
          }}
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-[#111827] text-white/70">
          <Film size={22} />
          <span className="text-[12px]">{result.status === "pending" ? "生成中" : "暂无视频"}</span>
        </div>
      )}

      {hasVideo && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-2 pb-2 pt-10 text-white">
          <span className="rounded bg-black/35 px-1.5 py-0.5 text-[14px] font-medium leading-none">
            {formatVideoDuration(duration)}
          </span>
          <span className="flex items-center gap-1">
            <button
              type="button"
              aria-label={muted ? "开启声音" : "关闭声音"}
              title={muted ? "开启声音" : "关闭声音"}
              className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-md bg-black/45 text-white transition-colors hover:bg-black/65 focus:outline-none focus:ring-2 focus:ring-white/70"
              onClick={(event) => {
                event.stopPropagation();
                toggleMuted();
              }}
            >
              {muted ? <VolumeX size={15} strokeWidth={2.2} /> : <Volume2 size={15} strokeWidth={2.2} />}
            </button>
            <a
              href={result.url}
              download={videoDownloadName(result)}
              target="_blank"
              rel="noreferrer"
              aria-label="下载视频"
              title="下载视频"
              className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-md bg-black/45 text-white transition-colors hover:bg-black/65 focus:outline-none focus:ring-2 focus:ring-white/70"
              onClick={(event) => event.stopPropagation()}
            >
              <Download size={16} strokeWidth={2.2} />
            </a>
          </span>
        </div>
      )}
    </div>
  );
}
