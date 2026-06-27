import { Check, FileText, FileVideo, Pencil, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat";
import { canAcceptImageResult } from "@/lib/imageReview";
import type { CreativeDirectionResponse } from "@/lib/api";

interface MessageBubbleProps {
  msg: ChatMessage;
  onOpenArtifact?: (msg: ChatMessage) => void;
  onSelectDirection?: (msg: ChatMessage, direction: CreativeDirectionResponse) => void;
  onApprovePlan?: (msg: ChatMessage) => void;
  onRevisePlan?: (msg: ChatMessage) => void;
  onGenerateImage?: (msg: ChatMessage) => void;
  onAcceptImageResult?: (msg: ChatMessage) => void;
  onReviseImageResult?: (msg: ChatMessage) => void;
  onGenerateVideoFromScenePackages?: (msg: ChatMessage) => void;
  onAcceptVideoResult?: (msg: ChatMessage) => void;
  onReviseVideoResult?: (msg: ChatMessage) => void;
  onRegenerateVideoWithRevision?: (msg: ChatMessage, useFlawAnalysis: boolean) => void;
  onRetryImageResult?: (msg: ChatMessage) => void;
  onRetrySceneAssets?: (msg: ChatMessage) => void;
  onRetryVideoMerge?: (msg: ChatMessage) => void;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function globalAssetRecords(globalAssets: unknown, collection: "characters" | "scenes" | "props"): Array<Record<string, unknown>> {
  return records(globalAssets && typeof globalAssets === "object" ? (globalAssets as Record<string, unknown>)[collection] : undefined);
}

function assetId(record: Record<string, unknown>): string {
  return stringValue(record.asset_id) || stringValue(record.id);
}

function assetTitle(record: Record<string, unknown>, fallback: string): string {
  return stringValue(record.name) || stringValue(record.description) || fallback;
}

function assetImage(record: Record<string, unknown>): string {
  return stringArray(record.three_view_images)[0] || stringArray(record.images)[0] || stringArray(record.image_urls)[0] || stringValue(record.url);
}

function materialUrl(record: Record<string, unknown>): string {
  return stringValue(record.url) || stringValue(record.path) || stringValue(record.image_url) || stringValue(record.imageUrl);
}

function materialName(record: Record<string, unknown>, index: number): string {
  return stringValue(record.name) || stringValue(record.filename) || `附件 ${index + 1}`;
}

function previewAssets(msg: ChatMessage): Array<{ id: string; title: string; image: string }> {
  const videoScenePackages = msg.artifact?.videoScenePackages;
  const globalAssets = videoScenePackages?.global_assets;
  const globalRecords = [
    ...globalAssetRecords(globalAssets, "characters"),
    ...globalAssetRecords(globalAssets, "scenes"),
    ...globalAssetRecords(globalAssets, "props"),
  ];
  const fromGlobal = globalRecords
    .map((asset, index) => ({ id: assetId(asset) || `asset-${index}`, title: assetTitle(asset, `素材 ${index + 1}`), image: assetImage(asset) }))
    .filter((item) => item.image);
  if (fromGlobal.length > 0) return fromGlobal.slice(0, 5);
  const fromScenes = records(videoScenePackages?.scene_packages)
    .flatMap((scene) => stringArray(scene.image_urls).map((image, index) => ({ id: `${stringValue(scene.scene_id) || "scene"}-${index}`, title: stringValue(scene.title) || "场景片段", image })));
  return fromScenes.slice(0, 5);
}

function quotaInsufficient(value: unknown): boolean {
  if (!value) return false;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (record.quota_insufficient === true) return true;
    return Object.values(record).some(quotaInsufficient);
  }
  const text = String(value);
  return ["额度不足", "余额不足", "没有有效的额度", "充值", "quota insufficient", "payment required"].some((keyword) => text.includes(keyword));
}

export function MessageBubble({
  msg,
  onOpenArtifact,
  onSelectDirection,
  onApprovePlan,
  onRevisePlan,
  onGenerateImage,
  onAcceptImageResult,
  onReviseImageResult,
  onGenerateVideoFromScenePackages,
  onAcceptVideoResult,
  onReviseVideoResult,
  onRegenerateVideoWithRevision,
  onRetryImageResult,
  onRetrySceneAssets,
  onRetryVideoMerge,
}: MessageBubbleProps) {
  const isUser = msg.role === "user";
  const planPreview = msg.artifact?.plan?.plan_markdown || "";
  const imagePrepareParams = msg.artifact?.imagePrepare?.params ? JSON.stringify(msg.artifact.imagePrepare.params, null, 2) : "";
  const scenePackages = msg.artifact?.videoScenePackages?.scene_packages || [];
  const videoAnalysisStoryboards = records(msg.artifact?.videoAnalysis?.storyboards);
  const messageMaterials = records(msg.materials);
  const sceneAssetQuotaPaused = quotaInsufficient(msg.artifact?.sceneAssetFailures);
  const imageQuotaPaused = quotaInsufficient(msg.artifact?.imageResult);
  const mergeQuotaPaused = quotaInsufficient(msg.artifact?.mergedVideo);
  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold",
          isUser ? "bg-accent text-white" : "bg-accent-soft text-accent",
        )}
      >
        {isUser ? "A" : <Sparkles size={15} />}
      </div>
      <div className={cn("flex max-w-[78%] flex-col", isUser ? "items-end" : "items-start")}>
        <div
          className={cn(
            "whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed",
            isUser
              ? "bg-accent-soft text-ink"
              : "border border-line bg-surface text-ink",
          )}
        >
          {msg.content}
        </div>
        {messageMaterials.length > 0 && (
          <div className={cn("mt-2 flex max-w-[520px] flex-wrap gap-2", isUser ? "justify-end" : "justify-start")}>
            {messageMaterials.map((material, index) => {
              const url = materialUrl(material);
              const name = materialName(material, index);
              const type = stringValue(material.type).toLowerCase();
              return (
                <a
                  key={`${url}-${index}`}
                  href={url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="flex max-w-[220px] items-center gap-2 rounded-xl border border-line bg-white px-2.5 py-1.5 text-[12px] text-ink hover:bg-canvas"
                >
                  {type === "image" && url ? (
                    <img src={url} alt="" className="h-8 w-8 shrink-0 rounded-md object-cover" />
                  ) : (
                    <FileText size={15} className="shrink-0 text-ink-soft" />
                  )}
                  <span className="truncate">{name}</span>
                </a>
              );
            })}
          </div>
        )}
        {msg.artifact?.type === "directions" && msg.artifact.directions ? (
          <div className="mt-2 w-full max-w-[520px] space-y-2 rounded-2xl border border-accent/20 bg-accent-soft/50 p-3">
            <div className="text-[13px] font-semibold text-ink">{msg.artifact.title}</div>
            <div className="text-[12px] text-ink-soft">{msg.artifact.description}</div>
            <div className="space-y-2">
              {msg.artifact.directions.map((direction) => (
                <div key={direction.direction_id} className="rounded-xl border border-line bg-white/80 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-ink">{direction.title}</span>
                        {direction.recommended && (
                          <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-medium text-white">
                            推荐
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onSelectDirection?.(msg, direction)}
                      className="shrink-0 rounded-lg bg-brand px-2.5 py-1.5 text-[12px] font-medium text-white hover:opacity-90"
                    >
                      选择
                    </button>
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-ink-soft">{direction.description}</p>
                  {direction.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {direction.tags.map((tag) => (
                        <span key={tag} className="rounded-full bg-canvas px-2 py-0.5 text-[11px] text-ink-soft">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : msg.artifact?.type === "plan" && msg.artifact.plan ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileText size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
            </div>
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {planPreview}
            </pre>
            {msg.artifact.plan.consistency_issues.length > 0 && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.plan.consistency_issues.join("；")}
              </div>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => onApprovePlan?.(msg)}
                className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Check size={15} />
                同意方案
              </button>
              <button
                type="button"
                onClick={() => onRevisePlan?.(msg)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line px-4 py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <Pencil size={15} />
                继续修改
              </button>
            </div>
          </div>
        ) : msg.artifact?.type === "image_prepare" && msg.artifact.imagePrepare ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileText size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.imagePrepare.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.imagePrepare.ok ? "可执行" : "需处理"}
              </span>
            </div>
            <div className="grid gap-2 text-[12px] text-ink-soft">
              <div>
                <span className="font-medium text-ink">接口：</span>
                {msg.artifact.imagePrepare.endpoint}
              </div>
              <div>
                <span className="font-medium text-ink">方式：</span>
                {msg.artifact.imagePrepare.method}
              </div>
              {msg.artifact.imagePrepare.message && (
                <div>
                  <span className="font-medium text-ink">提示：</span>
                  {msg.artifact.imagePrepare.message}
                </div>
              )}
            </div>
            <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {msg.artifact.imagePrepare.prompt}
            </pre>
            <pre className="max-h-[180px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
              {imagePrepareParams}
            </pre>
            {msg.artifact.imagePrepare.ok && (
              <button
                type="button"
                onClick={() => onGenerateImage?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                开始生成图片
              </button>
            )}
          </div>
        ) : msg.artifact?.type === "video_scene_packages" && msg.artifact.videoScenePackages ? (
          <div className="mt-2 w-full max-w-[560px] overflow-hidden rounded-2xl border border-line bg-surface">
            <div className="grid grid-cols-5 border-b border-line bg-canvas/60">
              {previewAssets(msg).length > 0 ? (
                previewAssets(msg).map((asset) => (
                  <div key={asset.id} className="border-r border-line last:border-r-0">
                    <img src={asset.image} alt={asset.title} className="aspect-[4/3] w-full object-cover" />
                  </div>
                ))
              ) : (
                Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="flex aspect-[4/3] items-center justify-center border-r border-line text-[11px] text-ink-soft last:border-r-0">
                    待生成
                  </div>
                ))
              )}
            </div>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1 py-3 pr-2">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-[14px] font-semibold text-ink">{msg.artifact.title || "创意 Storyboard"}</span>
                  <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">故事板</span>
                </span>
                <span className="mt-1 block text-[12px] leading-relaxed text-ink-soft">
                  {scenePackages.length} 个分镜片段，点击查看分镜后可编辑故事线、镜头描述、旁白和 @参考图。
                </span>
              </span>
            </div>
            {msg.artifact.sceneAssetFailures?.length ? (
              <div className="mx-3 mb-3 rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {sceneAssetQuotaPaused ? "参考图生成因额度不足暂停，充值后可继续。" : `${msg.artifact.sceneAssetFailures.length} 个参考图生成失败，可进入分镜检查。`}
              </div>
            ) : null}
            <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => onOpenArtifact?.(msg)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <FileText size={15} />
                查看分镜
              </button>
              {sceneAssetQuotaPaused ? (
                <button
                  type="button"
                  onClick={() => onRetrySceneAssets?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Sparkles size={15} />
                  继续生成参考图
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onGenerateVideoFromScenePackages?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Sparkles size={15} />
                  确认并生成视频
                </button>
              )}
            </div>
          </div>
        ) : msg.artifact?.type === "image_result" && msg.artifact.imageResult ? (
          <div className="mt-2 w-full max-w-[620px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <Sparkles size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.imageResult.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.imageResult.ok ? "已生成" : "失败"}
              </span>
            </div>
            {msg.artifact.imageResult.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.imageResult.error}
              </div>
            )}
            {msg.artifact.imageResult.images.length > 0 && (
              <div className="grid gap-3 sm:grid-cols-2">
                {msg.artifact.imageResult.images.map((image, index) => (
                  <a
                    key={image.asset_id || image.url || index}
                    href={image.download_url || image.url}
                    target="_blank"
                    rel="noreferrer"
                    className="overflow-hidden rounded-xl border border-line bg-canvas"
                  >
                    {image.url ? (
                      <img src={image.url} alt={`生成图片 ${index + 1}`} className="aspect-square w-full object-cover" />
                    ) : (
                      <div className="flex aspect-square items-center justify-center text-[12px] text-ink-soft">无图片 URL</div>
                    )}
                    <div className="truncate px-2 py-1.5 text-[11px] text-ink-soft">{image.url || image.asset_id || `图片 ${index + 1}`}</div>
                  </a>
                ))}
              </div>
            )}
            {imageQuotaPaused && (
              <button
                type="button"
                onClick={() => onRetryImageResult?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                充值后继续生成
              </button>
            )}
            {canAcceptImageResult(msg.artifact.imageResult) && (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onAcceptImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  满意，结束
                </button>
                <button
                  type="button"
                  onClick={() => onReviseImageResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <Pencil size={15} />
                  重新生成
                </button>
              </div>
            )}
          </div>
        ) : msg.artifact?.type === "video_analysis_result" && msg.artifact.videoAnalysis ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.videoAnalysis.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.videoAnalysis.ok ? "已完成" : "需补充"}
              </span>
            </div>
            {msg.artifact.videoAnalysis.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.videoAnalysis.error}
              </div>
            )}
            {msg.artifact.videoAnalysis.video_urls.length > 0 && (
              <div className="grid gap-2 text-[12px] text-ink-soft">
                {msg.artifact.videoAnalysis.video_urls.map((url, index) => (
                  <a key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer" className="truncate rounded-lg bg-canvas px-2 py-1.5 text-accent">
                    {index + 1}. {url}
                  </a>
                ))}
              </div>
            )}
            {videoAnalysisStoryboards.length > 0 && (
              <div className="space-y-2">
                {videoAnalysisStoryboards.slice(0, 4).map((storyboard, index) => {
                  const shots = records(storyboard.shots);
                  return (
                    <div key={`${String(storyboard.video_url || index)}-${index}`} className="rounded-xl border border-line bg-canvas p-2 text-[12px] text-ink-soft">
                      <div className="font-medium text-ink">{String(storyboard.video_url || storyboard.video_urls || `分析结果 ${index + 1}`)}</div>
                      {storyboard.analysis_markdown ? (
                        <div className="mt-1 whitespace-pre-wrap leading-relaxed">{String(storyboard.analysis_markdown)}</div>
                      ) : null}
                      {storyboard.generation_prompt ? (
                        <div className="mt-1 whitespace-pre-wrap leading-relaxed text-ink">生成建议：{String(storyboard.generation_prompt)}</div>
                      ) : null}
                      {shots.length > 0 ? (
                        <div className="mt-1 space-y-1">
                          {shots.slice(0, 3).map((shot, shotIndex) => (
                            <div key={`${String(shot.time_range || shotIndex)}-${shotIndex}`}>
                              {String(shot.time_range || `镜头 ${shotIndex + 1}`)}：{String(shot.visual_description || shot.description || "")}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="rounded-xl bg-canvas px-3 py-2 text-[12px] leading-relaxed text-ink-soft">
              调用链路：{msg.artifact.videoAnalysis.extract_endpoint} → {msg.artifact.videoAnalysis.endpoint || "未进入视频分析"}
            </div>
          </div>
        ) : msg.artifact?.type === "video_flaw_analysis" && msg.artifact.videoFlawAnalysis ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.videoFlawAnalysis.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.videoFlawAnalysis.ok ? "已分析" : "失败"}
              </span>
            </div>
            {msg.artifact.videoFlawAnalysis.flaw_analysis_markdown && (
              <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-3 text-[12px] leading-relaxed text-ink">
                {msg.artifact.videoFlawAnalysis.flaw_analysis_markdown}
              </pre>
            )}
            {msg.artifact.videoFlawAnalysis.affected_scene_ids.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {msg.artifact.videoFlawAnalysis.affected_scene_ids.map((sceneId) => (
                  <span key={sceneId} className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">
                    {sceneId}
                  </span>
                ))}
              </div>
            )}
            {msg.artifact.videoFlawAnalysis.issues.length > 0 && (
              <div className="space-y-2">
                {msg.artifact.videoFlawAnalysis.issues.slice(0, 4).map((issue, index) => (
                  <div key={`${String(issue.scene_id || index)}-${index}`} className="rounded-xl border border-line bg-canvas p-2 text-[12px] text-ink-soft">
                    <span className="font-medium text-ink">{String(issue.scene_id || `问题 ${index + 1}`)}</span>
                    <span className="ml-2">{String(issue.current || issue.description || "")}</span>
                    {issue.expected ? <span className="ml-2 text-ink">应为：{String(issue.expected)}</span> : null}
                  </div>
                ))}
              </div>
            )}
            {msg.artifact.videoRevisionFeedback && (
              <div className="rounded-xl bg-canvas px-3 py-2 text-[12px] leading-relaxed text-ink-soft">
                用户意见：{msg.artifact.videoRevisionFeedback}
              </div>
            )}
            <div className="grid gap-2 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => onRegenerateVideoWithRevision?.(msg, false)}
                className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
              >
                <Pencil size={15} />
                只按我的意见修改
              </button>
              <button
                type="button"
                onClick={() => onRegenerateVideoWithRevision?.(msg, true)}
                className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                结合穿帮信息修改
              </button>
            </div>
          </div>
        ) : msg.artifact?.type === "video_result" && (msg.artifact.mergedVideo || msg.artifact.generatedSceneVideos) ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium",
                msg.artifact.mergedVideo?.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber",
              )}>
                {msg.artifact.mergedVideo?.ok ? "已合并" : "失败"}
              </span>
            </div>
            {msg.artifact.mergedVideo?.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.mergedVideo.error}
              </div>
            )}
            {msg.artifact.mergedVideo?.merged_video_url && (
              <a
                href={msg.artifact.mergedVideo.merged_video_url}
                target="_blank"
                rel="noreferrer"
                className="block truncate rounded-xl border border-line bg-canvas px-3 py-2 text-[12px] text-accent hover:bg-accent-soft"
              >
                合并视频：{msg.artifact.mergedVideo.merged_video_url}
              </a>
            )}
            {msg.artifact.generatedSceneVideos?.scene_videos.length ? (
              <div className="grid gap-2 text-[12px] text-ink-soft">
                {msg.artifact.generatedSceneVideos.scene_videos.map((scene) => (
                  <a key={scene.scene_id} href={scene.video_url} target="_blank" rel="noreferrer" className="truncate rounded-lg bg-canvas px-2 py-1.5 text-accent">
                    {scene.scene_index}. {scene.video_url}
                  </a>
                ))}
              </div>
            ) : null}
            {msg.artifact.generatedSceneVideos?.failed_scenes.length ? (
              <div className="space-y-2 rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                <div className="font-medium">失败场景：{msg.artifact.generatedSceneVideos.failed_scenes.length} 个</div>
                {msg.artifact.generatedSceneVideos.failed_scenes.map((scene, index) => (
                  <details key={`${String(scene.scene_id || index)}-${index}`} className="rounded-lg bg-white/70 px-2 py-1.5">
                    <summary className="cursor-pointer text-amber">
                      {String(scene.scene_index || index + 1)}. {String(scene.scene_id || "未知场景")} · 查看失败原因
                    </summary>
                    <pre className="mt-2 max-h-[180px] overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-ink-soft">
                      {JSON.stringify(scene, null, 2)}
                    </pre>
                  </details>
                ))}
              </div>
            ) : null}
            {mergeQuotaPaused && msg.artifact.generatedSceneVideos?.scene_videos.length ? (
              <button
                type="button"
                onClick={() => onRetryVideoMerge?.(msg)}
                className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                继续合并视频
              </button>
            ) : null}
            {msg.artifact.mergedVideo?.ok && (
              <div className="grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => onAcceptVideoResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
                >
                  <Check size={15} />
                  无意见，结束
                </button>
                <button
                  type="button"
                  onClick={() => onReviseVideoResult?.(msg)}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas"
                >
                  <Pencil size={15} />
                  提出修改意见
                </button>
              </div>
            )}
          </div>
        ) : msg.artifact ? (
          <button
            type="button"
            onClick={() => onOpenArtifact?.(msg)}
            className="mt-2 flex w-full max-w-[320px] items-center gap-3 rounded-2xl border border-accent/20 bg-accent-soft/70 px-3 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent-soft"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/70 text-accent">
              <FileVideo size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
              <span className="mt-0.5 block truncate text-[12px] text-ink-soft">{msg.artifact.description}</span>
            </span>
            <span className="shrink-0 rounded-lg bg-white/70 px-2 py-1 text-[12px] font-medium text-accent">
              {msg.artifact.actionLabel}
            </span>
          </button>
        ) : null}
        <span className="mt-1 px-1 text-[11px] text-ink-soft/60">{msg.time}</span>
      </div>
    </div>
  );
}
