import { ArrowLeft, Download, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { VideoResult } from "@/lib/types";
import { formatVideoDuration, videoDownloadName } from "./VideoResultCard";

interface VideoPreviewPanelProps {
  video: VideoResult;
  onBack: () => void;
  onDownload?: (video: VideoResult) => void;
}

export function VideoPreviewPanel({ video, onBack, onDownload }: VideoPreviewPanelProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hasPlaybackStartedRef = useRef(false);
  const [playbackUrl, setPlaybackUrl] = useState(video.url);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(video.durationSec || 0);

  useEffect(() => {
    hasPlaybackStartedRef.current = false;
    setPlaybackUrl(video.url);
    setPlaying(false);
    setCurrentTime(0);
    setDuration(video.durationSec || 0);
    setMuted(false);
  }, [video.id, video.url, video.durationSec]);

  useEffect(() => {
    const sourceUrl = video.url;
    if (!sourceUrl || sourceUrl.startsWith("blob:") || sourceUrl.startsWith("data:")) return;

    const controller = new AbortController();
    let objectUrl: string | null = null;

    void (async () => {
      try {
        const response = await fetch(sourceUrl, { cache: "force-cache", signal: controller.signal });
        if (!response.ok) return;
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        if (hasPlaybackStartedRef.current) {
          URL.revokeObjectURL(objectUrl);
          objectUrl = null;
          return;
        }
        setPlaybackUrl(objectUrl);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setPlaybackUrl(sourceUrl);
      }
    })();

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [video.url]);

  const togglePlaying = () => {
    const element = videoRef.current;
    if (!element) return;
    if (element.paused) {
      void element.play().catch(() => {});
    } else {
      element.pause();
    }
  };

  const toggleMuted = () => {
    setMuted((value) => {
      const next = !value;
      if (videoRef.current) videoRef.current.muted = next;
      return next;
    });
  };

  const seek = (value: number) => {
    const element = videoRef.current;
    if (!element) return;
    element.currentTime = value;
    setCurrentTime(value);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <div className="flex h-14 shrink-0 items-center justify-between gap-4 px-6">
        <div className="flex min-w-0 items-center gap-5">
          <button
            type="button"
            onClick={onBack}
            className="flex h-9 shrink-0 items-center gap-2 rounded-lg px-1 text-[14px] font-semibold text-ink hover:bg-white"
          >
            <ArrowLeft size={18} />
            返回
          </button>
          <h2 className="truncate text-[16px] font-semibold text-ink">{video.title || videoDownloadName(video)}</h2>
        </div>
        {video.url && (
          <a
            href={video.url}
            download={videoDownloadName(video)}
            target="_blank"
            rel="noreferrer"
            onClick={() => onDownload?.(video)}
            className="flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-black px-3 text-[14px] font-semibold text-white hover:bg-black/85"
          >
            <Download size={16} />
            下载
          </a>
        )}
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center px-6 pb-16 pt-2">
        <video
          ref={videoRef}
          src={playbackUrl}
          poster={video.thumbUrl}
          className="max-h-full max-w-full bg-black object-contain"
          playsInline
          preload="auto"
          muted={muted}
          onPlay={() => {
            hasPlaybackStartedRef.current = true;
            setPlaying(true);
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
          onLoadedMetadata={(event) => {
            const nextDuration = event.currentTarget.duration;
            if (Number.isFinite(nextDuration)) setDuration(nextDuration);
          }}
        />
      </div>

      <div className="shrink-0 px-6 pb-5">
        <div className="flex items-center gap-2.5 text-ink">
          <button
            type="button"
            onClick={togglePlaying}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink hover:bg-white"
            aria-label={playing ? "暂停" : "播放"}
            title={playing ? "暂停" : "播放"}
          >
            {playing ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}
          </button>
          <span className="w-11 text-[14px] font-medium tabular-nums">{formatVideoDuration(currentTime)}</span>
          <input
            type="range"
            min={0}
            max={Math.max(duration, 0)}
            step={0.01}
            value={Math.min(currentTime, duration || currentTime)}
            onChange={(event) => seek(Number(event.currentTarget.value))}
            className="h-1 min-w-0 flex-1 accent-black"
            aria-label="视频进度"
          />
          <span className="w-12 text-right text-[14px] font-medium tabular-nums text-ink-soft">
            {formatVideoDuration(duration)}
          </span>
          <button
            type="button"
            onClick={toggleMuted}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink hover:bg-white"
            aria-label={muted ? "开启声音" : "关闭声音"}
            title={muted ? "开启声音" : "关闭声音"}
          >
            {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
          </button>
        </div>
      </div>
    </div>
  );
}
