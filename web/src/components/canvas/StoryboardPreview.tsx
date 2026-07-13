import { useMemo, useState } from "react";
import { Box, Check, Clock3, Image as ImageIcon, MapPin, Package, Palette, Sparkles, UserRound, WandSparkles } from "lucide-react";
import type { Brief } from "@/lib/chat";
import { cn } from "@/lib/utils";

interface StoryboardPreviewProps {
  brief: Brief;
  productImageUrl?: string;
  productName?: string;
  onConfirm: () => void;
  onBackToBrief?: () => void;
}

function shotLabel(value: string, fallback: string) {
  return value.trim() || fallback;
}

const VIDEO_STYLES = ["信息流广告风格", "生活种草", "产品演示", "电影质感", "UGC 手持"];

export function StoryboardPreview({ brief, productImageUrl, productName, onConfirm, onBackToBrief }: StoryboardPreviewProps) {
  const [activeShotId, setActiveShotId] = useState(brief.shots[0]?.shotId || "");
  const [videoStyle, setVideoStyle] = useState(brief.globalVisual?.overallStyle || VIDEO_STYLES[0]);
  const [imageMode, setImageMode] = useState<"reference" | "generate">(productImageUrl ? "reference" : "generate");
  const activeShot = useMemo(
    () => brief.shots.find((shot) => shot.shotId === activeShotId) || brief.shots[0],
    [activeShotId, brief.shots],
  );
  const assetCards = useMemo(
    () => [
      {
        key: "role",
        title: "出场角色",
        icon: UserRound,
        text: brief.globalVisual?.characterStyle || brief.globalVisual?.subjectType || "按 Brief 生成统一角色设定",
      },
      {
        key: "scene",
        title: "场景",
        icon: MapPin,
        text: brief.globalVisual?.environment || activeShot?.visualDescription || "按分镜生成场景",
      },
      {
        key: "product",
        title: "产品",
        icon: Package,
        text: productName || brief.globalVisual?.subjectType || "商品主体",
        image: productImageUrl,
      },
      {
        key: "props",
        title: "道具",
        icon: Box,
        text: activeShot?.assetStrategy || "保留商品、道具与关键参考素材",
        image: productImageUrl,
      },
      {
        key: "background",
        title: "背景",
        icon: ImageIcon,
        text: brief.globalVisual?.lighting || brief.globalVisual?.environment || "按视频风格生成背景",
      },
      {
        key: "style",
        title: "视频风格",
        icon: Palette,
        text: videoStyle,
      },
    ],
    [activeShot?.assetStrategy, activeShot?.visualDescription, brief.globalVisual, productImageUrl, productName, videoStyle],
  );

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface">
        <div className="border-b border-line px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[14px] font-semibold text-ink">视频场景包</div>
              <div className="mt-0.5 text-[12px] text-ink-soft">
                {brief.shots.length} 个分镜片段 · {brief.durationSec || 0}s · {brief.ratio || "9:16"}
              </div>
            </div>
            <span className="shrink-0 rounded-md border border-accent/20 bg-accent-soft px-2 py-1 text-[12px] font-medium text-accent">
              待确认
            </span>
          </div>
        </div>

        <div className="border-b border-line p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="text-[13px] font-semibold text-ink">参考资产包</div>
              <div className="mt-0.5 text-[12px] text-ink-soft">角色、产品、道具、背景和视频风格会作为后续生图/生视频的一致性约束。</div>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {assetCards.map((asset) => {
              const Icon = asset.icon;
              return (
                <div key={asset.key} className="rounded-lg border border-line bg-canvas p-3">
                  <div className="flex items-start gap-2">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface text-ink-soft">
                      <Icon size={14} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-[12px] font-semibold text-ink">{asset.title}</div>
                      <div className="mt-1 line-clamp-2 text-[12px] leading-5 text-ink-soft">{asset.text}</div>
                    </div>
                    {asset.image && <img src={asset.image} alt="" className="h-12 w-12 shrink-0 rounded-md border border-line object-cover" />}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {VIDEO_STYLES.map((style) => (
              <button
                key={style}
                type="button"
                onClick={() => setVideoStyle(style)}
                className={cn(
                  "h-8 rounded-full border px-3 text-[12px] font-medium transition-colors",
                  videoStyle === style ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-ink-soft hover:text-ink",
                )}
              >
                {style}
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_230px]">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {brief.shots.map((shot, index) => (
                <button
                  key={shot.shotId}
                  type="button"
                  onClick={() => setActiveShotId(shot.shotId)}
                  className={cn(
                    "h-8 rounded-full border px-3 text-[12px] font-medium transition-colors",
                    activeShot?.shotId === shot.shotId
                      ? "border-accent bg-accent-soft text-accent"
                      : "border-line bg-surface text-ink-soft hover:text-ink",
                  )}
                >
                  分镜 {index + 1}
                </button>
              ))}
            </div>

            {activeShot && (
              <div className="space-y-3">
                <div>
                  <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-ink-soft">
                    <Sparkles size={13} />
                    故事线
                  </div>
                  <div className="rounded-lg border border-line bg-canvas px-3 py-3 text-[13px] font-semibold leading-6 text-ink">
                    {shotLabel(activeShot.visualDescription || "", activeShot.narration || "等待补充分镜描述。")}
                  </div>
                </div>

                <div>
                  <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-ink-soft">
                    <Clock3 size={13} />
                    镜头描述
                  </div>
                  <div className="rounded-lg border border-line bg-surface px-3 py-3 text-[13px] leading-6 text-ink/85">
                    <div className="font-medium text-ink">
                      {activeShot.timeRange || `${activeShot.durationSec}s`} · {activeShot.shotType || "镜头"} · {activeShot.cameraMovement || "固定"}
                    </div>
                    <p className="mt-1">{activeShot.generationPrompt || activeShot.visualDescription || "暂无生成提示词。"}</p>
                  </div>
                </div>

                <div>
                  <div className="mb-1 text-[12px] font-medium text-ink-soft">旁白 / 屏幕文案</div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="rounded-lg border border-line bg-surface px-3 py-3 text-[13px] leading-5 text-ink/85">
                      {activeShot.narration || "无旁白"}
                    </div>
                    <div className="rounded-lg border border-line bg-surface px-3 py-3 text-[13px] leading-5 text-ink/85">
                      {activeShot.onscreen || "无屏幕文字"}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                disabled={!productImageUrl}
                onClick={() => setImageMode("reference")}
                className={cn(
                  "rounded-lg border px-2 py-2 text-[12px] font-medium transition-colors",
                  imageMode === "reference" ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-ink-soft hover:text-ink",
                  !productImageUrl && "cursor-not-allowed opacity-40",
                )}
              >
                使用上传图
              </button>
              <button
                type="button"
                onClick={() => setImageMode("generate")}
                className={cn(
                  "rounded-lg border px-2 py-2 text-[12px] font-medium transition-colors",
                  imageMode === "generate" ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-ink-soft hover:text-ink",
                )}
              >
                生成分镜图
              </button>
            </div>
            <div className="overflow-hidden rounded-lg border border-line bg-canvas">
              {imageMode === "reference" && productImageUrl ? (
                <img src={productImageUrl} alt="商品参考图" className="aspect-[3/4] w-full object-cover" />
              ) : (
                <div className="flex aspect-[3/4] w-full flex-col items-center justify-center text-ink-soft">
                  <WandSparkles size={24} className="mb-2 opacity-60" />
                  <span className="text-[12px]">{imageMode === "generate" ? "分镜图待生成" : "暂无上传图"}</span>
                  <span className="mt-1 px-3 text-center text-[11px] leading-4 text-ink-soft/75">
                    可接入 Seedream 生成该分镜参考图
                  </span>
                </div>
              )}
            </div>
            <div className="rounded-lg border border-line bg-surface p-3">
              <div className="text-[12px] font-medium text-ink">当前分镜参考</div>
              <div className="mt-1 text-[12px] leading-5 text-ink-soft">
                {productName || "商品"} · {videoStyle} · {imageMode === "reference" ? "使用上传参考图" : "生成分镜图"}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={onBackToBrief}
          className="flex-1 rounded-xl border border-line bg-surface py-2.5 text-[14px] font-medium text-ink hover:bg-canvas"
        >
          返回 Brief
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="flex flex-[1.4] items-center justify-center gap-2 rounded-xl bg-brand py-2.5 text-[14px] font-medium text-white hover:opacity-90"
        >
          <Check size={16} />
          确认并生成视频
        </button>
      </div>
    </div>
  );
}
