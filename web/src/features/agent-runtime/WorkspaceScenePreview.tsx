/** 用 Snapshot 下发的白名单 TOS 地址直连播放，不经 Gateway 中转成片字节。 */

type Props = {
  src: string;
  title: string;
};

export function WorkspaceScenePreview({ src, title }: Props) {
  if (!src) {
    return (
      <div className="mt-2 grid h-32 place-items-center rounded-lg border border-dashed border-line bg-canvas text-[10px] text-ink-soft">
        成片暂时无法预览
      </div>
    );
  }
  return (
    <video
      src={src}
      className="mt-2 w-full max-h-80 rounded-lg border border-line bg-black object-contain"
      controls
      playsInline
      preload="metadata"
      aria-label={title}
    />
  );
}
