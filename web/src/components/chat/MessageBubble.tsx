import { Check, FileText, FileVideo, Pencil, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/chat";
import { canAcceptImageResult } from "@/lib/imageReview";
import type { CreativeDirectionResponse } from "@/lib/api";
import type { SceneAssetCollection, ScenePackagePatch } from "@/lib/scenePackages";

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
  onUpdateVideoScenePackage?: (msg: ChatMessage, sceneId: string, patch: ScenePackagePatch) => void;
  onUpdateVideoSceneAssetField?: (
    msg: ChatMessage,
    sceneId: string,
    collection: SceneAssetCollection,
    index: number,
    field: string,
    value: string,
  ) => void;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sceneAssetPreviews(scene: Record<string, unknown>): Array<{ key: string; label: string; url: string }> {
  const previews: Array<{ key: string; label: string; url: string }> = [];
  const pushUrls = (label: string, urls: string[]) => {
    urls.forEach((url, index) => previews.push({ key: `${label}-${index}-${url}`, label, url }));
  };
  pushUrls("参考图", stringArray(scene.image_urls));
  const collect = (items: unknown, field: string, labelFor: (record: Record<string, unknown>, index: number) => string) => {
    if (!Array.isArray(items)) return;
    items.forEach((item, index) => {
      if (!item || typeof item !== "object") return;
      const record = item as Record<string, unknown>;
      pushUrls(labelFor(record, index), stringArray(record[field]));
    });
  };
  collect(scene.characters, "three_view_images", (record, index) => `${stringValue(record.name) || `角色 ${index + 1}`} 三视图`);
  collect(scene.scene_images, "images", (record, index) => stringValue(record.description) || `场景图 ${index + 1}`);
  collect(scene.prop_images, "images", (record, index) => `${stringValue(record.name) || `道具 ${index + 1}`} 图`);
  return previews.slice(0, 8);
}

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function materialUrl(record: Record<string, unknown>): string {
  return stringValue(record.url) || stringValue(record.path) || stringValue(record.image_url) || stringValue(record.imageUrl);
}

function materialName(record: Record<string, unknown>, index: number): string {
  return stringValue(record.name) || stringValue(record.filename) || `附件 ${index + 1}`;
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
  onUpdateVideoScenePackage,
  onUpdateVideoSceneAssetField,
}: MessageBubbleProps) {
  const isUser = msg.role === "user";
  const planPreview = msg.artifact?.plan?.plan_markdown || "";
  const imagePrepareParams = msg.artifact?.imagePrepare?.params ? JSON.stringify(msg.artifact.imagePrepare.params, null, 2) : "";
  const scenePackages = msg.artifact?.videoScenePackages?.scene_packages || [];
  const videoAnalysisStoryboards = records(msg.artifact?.videoAnalysis?.storyboards);
  const messageMaterials = records(msg.materials);
  const inputClass = "min-w-0 rounded-lg border border-line bg-white px-2 py-1.5 text-[12px] text-ink outline-none focus:border-accent";
  const textareaClass = "min-h-16 rounded-lg border border-line bg-white px-2 py-1.5 text-[12px] leading-relaxed text-ink outline-none focus:border-accent";
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
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">需确认</span>
            </div>
            <div className="space-y-2">
              {scenePackages.map((scene) => {
                const assetPreviews = sceneAssetPreviews(scene as unknown as Record<string, unknown>);
                return (
                  <div key={scene.scene_id} className="rounded-xl border border-line bg-canvas p-3">
                    <div className="flex flex-wrap items-center gap-2 text-[12px] font-semibold text-ink">
                      <span>{scene.scene_index}. {scene.title || scene.scene_id}</span>
                      <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-ink-soft">
                        {(scene.duration_ms / 1000).toFixed(1)}s
                      </span>
                    </div>
                    {scene.storyline && <p className="mt-1 text-[12px] leading-relaxed text-ink-soft">{scene.storyline}</p>}
                    {scene.narration && (
                      <p className="mt-2 rounded-lg bg-white px-2 py-1.5 text-[12px] leading-relaxed text-ink">
                        {scene.narration}
                      </p>
                    )}
                    <div className="mt-3 grid gap-2">
                      <div className="grid gap-2 sm:grid-cols-[1fr_112px]">
                        <label className="grid gap-1 text-[11px] text-ink-soft">
                          场景标题
                          <input
                            value={scene.title || ""}
                            onChange={(event) => onUpdateVideoScenePackage?.(msg, scene.scene_id, { title: event.currentTarget.value })}
                            className={inputClass}
                          />
                        </label>
                        <label className="grid gap-1 text-[11px] text-ink-soft">
                          时长(ms)
                          <input
                            type="number"
                            min={1}
                            max={10000}
                            value={scene.duration_ms}
                            onChange={(event) => onUpdateVideoScenePackage?.(msg, scene.scene_id, { duration_ms: event.currentTarget.value })}
                            className={inputClass}
                          />
                        </label>
                      </div>
                      <label className="grid gap-1 text-[11px] text-ink-soft">
                        故事线
                        <textarea
                          value={scene.storyline || ""}
                          onChange={(event) => onUpdateVideoScenePackage?.(msg, scene.scene_id, { storyline: event.currentTarget.value })}
                          className={textareaClass}
                        />
                      </label>
                      <label className="grid gap-1 text-[11px] text-ink-soft">
                        分镜片段创作提示词
                        <textarea
                          value={scene.prompt}
                          onChange={(event) => onUpdateVideoScenePackage?.(msg, scene.scene_id, { prompt: event.currentTarget.value })}
                          className={textareaClass}
                        />
                      </label>
                      <label className="grid gap-1 text-[11px] text-ink-soft">
                        旁白
                        <textarea
                          value={scene.narration || ""}
                          onChange={(event) => onUpdateVideoScenePackage?.(msg, scene.scene_id, { narration: event.currentTarget.value })}
                          className={textareaClass}
                        />
                      </label>
                      <div className="grid gap-2 rounded-lg border border-line bg-white/70 p-2">
                        <div className="text-[11px] font-medium text-ink">角色三视图</div>
                        {records(scene.characters).map((character, index) => (
                          <div key={`${scene.scene_id}-character-${index}`} className="grid gap-2 sm:grid-cols-2">
                            <input
                              aria-label="角色名称"
                              value={stringValue(character.name)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "characters", index, "name", event.currentTarget.value)}
                              className={inputClass}
                            />
                            <input
                              aria-label="角色描述"
                              value={stringValue(character.description)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "characters", index, "description", event.currentTarget.value)}
                              className={inputClass}
                            />
                            <textarea
                              aria-label="角色三视图提示词"
                              value={stringValue(character.three_view_prompt)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "characters", index, "three_view_prompt", event.currentTarget.value)}
                              className={`${textareaClass} sm:col-span-2`}
                            />
                          </div>
                        ))}
                      </div>
                      <div className="grid gap-2 rounded-lg border border-line bg-white/70 p-2">
                        <div className="text-[11px] font-medium text-ink">场景图</div>
                        {records(scene.scene_images).map((sceneImage, index) => (
                          <div key={`${scene.scene_id}-scene-image-${index}`} className="grid gap-2">
                            <input
                              aria-label="场景图描述"
                              value={stringValue(sceneImage.description)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "scene_images", index, "description", event.currentTarget.value)}
                              className={inputClass}
                            />
                            <textarea
                              aria-label="场景图提示词"
                              value={stringValue(sceneImage.image_prompt)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "scene_images", index, "image_prompt", event.currentTarget.value)}
                              className={textareaClass}
                            />
                          </div>
                        ))}
                      </div>
                      <div className="grid gap-2 rounded-lg border border-line bg-white/70 p-2">
                        <div className="text-[11px] font-medium text-ink">道具图</div>
                        {records(scene.prop_images).map((propImage, index) => (
                          <div key={`${scene.scene_id}-prop-image-${index}`} className="grid gap-2 sm:grid-cols-2">
                            <input
                              aria-label="道具名称"
                              value={stringValue(propImage.name)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "prop_images", index, "name", event.currentTarget.value)}
                              className={inputClass}
                            />
                            <input
                              aria-label="道具描述"
                              value={stringValue(propImage.description)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "prop_images", index, "description", event.currentTarget.value)}
                              className={inputClass}
                            />
                            <textarea
                              aria-label="道具图提示词"
                              value={stringValue(propImage.image_prompt)}
                              onChange={(event) => onUpdateVideoSceneAssetField?.(msg, scene.scene_id, "prop_images", index, "image_prompt", event.currentTarget.value)}
                              className={`${textareaClass} sm:col-span-2`}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                    {assetPreviews.length > 0 && (
                      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                        {assetPreviews.map((asset) => (
                          <a
                            key={asset.key}
                            href={asset.url}
                            target="_blank"
                            rel="noreferrer"
                            className="overflow-hidden rounded-lg border border-line bg-white"
                          >
                            <img src={asset.url} alt={asset.label} className="aspect-square w-full object-cover" />
                            <div className="truncate px-2 py-1 text-[11px] text-ink-soft">{asset.label}</div>
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            {msg.artifact.sceneAssetFailures?.length ? (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.sceneAssetFailures.length} 个参考图生成失败，确认前请检查场景包或稍后重试。
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => onGenerateVideoFromScenePackages?.(msg)}
              className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
            >
              <Sparkles size={15} />
              确认场景包并生成视频
            </button>
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
        ) : msg.artifact?.type === "video_result" && msg.artifact.mergedVideo ? (
          <div className="mt-2 w-full max-w-[680px] space-y-3 rounded-2xl border border-line bg-surface p-3">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
                <FileVideo size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{msg.artifact.title}</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-ink-soft">{msg.artifact.description}</span>
              </span>
              <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium", msg.artifact.mergedVideo.ok ? "bg-emerald/10 text-emerald" : "bg-amber/10 text-amber")}>
                {msg.artifact.mergedVideo.ok ? "已合并" : "失败"}
              </span>
            </div>
            {msg.artifact.mergedVideo.error && (
              <div className="rounded-xl border border-amber/30 bg-amber/10 p-2 text-[12px] text-ink">
                {msg.artifact.mergedVideo.error}
              </div>
            )}
            {msg.artifact.mergedVideo.merged_video_url && (
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
            {msg.artifact.mergedVideo.ok && (
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
