import { ArrowLeft, Box, Download, ImageIcon, MapPin, Sparkles, Trash2, UserRound, X } from "lucide-react";
import { SceneMentionEditor } from "@/components/canvas/SceneMentionEditor";
import type { ChatMessage } from "@/lib/chat";
import { buildMentionCandidates, normalizeShotMentions, type SceneMention } from "@/lib/sceneMentions";
import {
  collectSceneImageUrls,
  stringArray,
  type GlobalSceneAssets,
  type SceneGlobalAssetReference,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";

interface StoryboardPanelProps {
  msg: ChatMessage;
  onUpdateVideoScenePackage?: (sceneId: string, patch: ScenePackagePatch) => void;
  onReferenceGlobalAsset?: (asset: SceneGlobalAssetReference) => void;
  onDeleteGlobalAsset?: (asset: SceneGlobalAssetReference) => void;
  onGenerateVideo?: () => void;
  onRetrySceneAssets?: () => void;
  onClose?: () => void;
}

type AssetGroup = "characters" | "scenes" | "props";

const assetGroupTitle: Record<AssetGroup, string> = {
  characters: "出场角色",
  scenes: "场景",
  props: "道具",
};

const assetGroupIcon: Record<AssetGroup, typeof UserRound> = {
  characters: UserRound,
  scenes: MapPin,
  props: Box,
};

function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function globalAssets(value: unknown): GlobalSceneAssets {
  return value && typeof value === "object" ? (value as GlobalSceneAssets) : {};
}

function assetId(record: Record<string, unknown>, fallback: string): string {
  return stringValue(record.asset_id) || stringValue(record.id) || fallback;
}

function assetName(record: Record<string, unknown>, fallback: string): string {
  return stringValue(record.name) || stringValue(record.description) || fallback;
}

function assetImage(record: Record<string, unknown>): string {
  return stringArray(record.images)[0] || stringArray(record.image_urls)[0] || stringArray(record.three_view_images)[0] || stringValue(record.url);
}

function assetDescription(record: Record<string, unknown>): string {
  return stringValue(record.description) || stringValue(record.prompt) || stringValue(record.image_prompt) || stringValue(record.three_view_prompt);
}

function globalAssetRecords(assets: GlobalSceneAssets, group: AssetGroup): Array<Record<string, unknown>> {
  return records(assets[group]);
}

function shotRecord(scene: ScenePackageRecord | undefined): Record<string, unknown> {
  const value = scene?.shot_description;
  return value && typeof value === "object" ? value : {};
}

function textareaClass(extra = "") {
  return cn("min-h-24 w-full resize-none rounded-xl border border-line bg-white px-3 py-2 text-[13px] leading-relaxed text-ink outline-none focus:border-accent", extra);
}

function shotDescriptionText(shot: Record<string, unknown>): string {
  const direct = stringValue(shot.text) || stringValue(shot.description_text) || stringValue(shot.shotText);
  if (direct) return direct;
  const legacyParts = [
    stringValue(shot.time_range) || stringValue(shot.timeRange),
    stringValue(shot.location) ? `地点:${stringValue(shot.location)} 中,` : "",
    stringArray(shot.characters).length > 0 ? `角色:${stringArray(shot.characters).join("、")}` : "",
    stringValue(shot.description),
    stringArray(shot.props).length > 0 ? `道具:${stringArray(shot.props).join("、")}` : "",
    stringValue(shot.shot_size) || stringValue(shot.shotSize),
    stringValue(shot.visual_style) ? `视觉风格:${stringValue(shot.visual_style)}` : "",
  ];
  return legacyParts.filter(Boolean).join("");
}

function quotaInsufficient(value: unknown): boolean {
  if (!value) return false;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (record.quota_insufficient === true) return true;
    return Object.values(record).some(quotaInsufficient);
  }
  return String(value).includes("额度不足") || String(value).includes("充值");
}

function sceneVideoForScene(
  scene: ScenePackageRecord | undefined,
  generatedSceneVideos: NonNullable<ChatMessage["artifact"]>["generatedSceneVideos"],
) {
  if (!scene) return undefined;
  return (generatedSceneVideos?.scene_videos || []).find((video) => video.scene_id === scene.scene_id);
}

export function StoryboardPanel({
  msg,
  onUpdateVideoScenePackage,
  onReferenceGlobalAsset,
  onDeleteGlobalAsset,
  onGenerateVideo,
  onRetrySceneAssets,
  onClose,
}: StoryboardPanelProps) {
  const videoScenePackages = msg.artifact?.videoScenePackages;
  const generatedSceneVideos = msg.artifact?.generatedSceneVideos;
  const scenes = (videoScenePackages?.scene_packages || []) as ScenePackageRecord[];
  const assets = globalAssets(videoScenePackages?.global_assets);
  const [selectedSceneId, setSelectedSceneId] = useState(scenes[0]?.scene_id || "");
  const [previewAsset, setPreviewAsset] = useState<SceneGlobalAssetReference | null>(null);
  const selectedScene = scenes.find((scene) => scene.scene_id === selectedSceneId) || scenes[0];
  const dirtySceneIds = new Set(msg.artifact?.videoScenePackageEditedSceneIds || []);
  const selectedReferenceIds = stringArray(selectedScene?.reference_asset_ids);
  const shot = shotRecord(selectedScene);
  const mentionCandidates = useMemo(() => buildMentionCandidates(assets), [assets]);
  const shotMentions = useMemo(
    () => normalizeShotMentions(shot, selectedReferenceIds, assets),
    [assets, selectedReferenceIds, shot],
  );
  const allReferenceAssets = useMemo(
    () =>
      (["characters", "scenes", "props"] as AssetGroup[]).flatMap((group) =>
        globalAssetRecords(assets, group).map((record, index) => ({
          group,
          record,
          id: assetId(record, `${group}-${index + 1}`),
          name: assetName(record, `${assetGroupTitle[group]} ${index + 1}`),
          image: assetImage(record),
        })),
      ),
    [assets],
  );
  const previewUrls = selectedScene ? collectSceneImageUrls(selectedScene, assets) : [];
  const previewUrl = previewUrls[0] || allReferenceAssets.find((asset) => asset.image)?.image || "";
  const selectedSceneVideo = sceneVideoForScene(selectedScene, generatedSceneVideos);
  const previewVideoUrl = selectedSceneVideo?.video_url || "";
  const sceneAssetQuotaPaused = quotaInsufficient(msg.artifact?.sceneAssetFailures);

  const updateScene = (patch: ScenePackagePatch) => {
    if (!selectedScene) return;
    onUpdateVideoScenePackage?.(selectedScene.scene_id, patch);
  };

  const updateShotDescription = (next: { text: string; mentions: SceneMention[] }) => {
    updateScene({
      shot_description: {
        ...shot,
        text: next.text,
        mentions: next.mentions,
      },
      reference_asset_ids: next.mentions.map((mention) => mention.asset_id),
    });
  };

  const openAssetPreview = (group: AssetGroup, record: Record<string, unknown>, fallback: string) => {
    const image = assetImage(record);
    if (!image) return;
    const id = assetId(record, fallback);
    const name = assetName(record, id);
    setPreviewAsset({
      source: "scene_global_asset",
      asset_id: id,
      asset_group: group,
      name,
      source_image_url: image,
      url: image,
      type: "image",
      filename: `${name}.png`,
      description: assetDescription(record),
    });
  };

  const referencePreviewAsset = () => {
    if (!previewAsset) return;
    onReferenceGlobalAsset?.(previewAsset);
    setPreviewAsset(null);
  };

  const deletePreviewAsset = () => {
    if (!previewAsset) return;
    onDeleteGlobalAsset?.({ ...previewAsset, scene_global_asset_action: "delete" });
    setPreviewAsset(null);
  };

  return (
    <aside className="flex h-full w-[52vw] min-w-[680px] max-w-[980px] flex-col border-l border-line bg-[#f8fafc]">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4">
        <button type="button" onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-canvas" aria-label="返回">
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold text-ink">{msg.artifact?.title || "storyboard.json"}</div>
          <div className="text-[12px] text-ink-soft">共 {scenes.length} 个镜头</div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(280px,42%)]">
        <div className="min-h-0 overflow-y-auto px-4 py-4">
          <section className="rounded-2xl border border-line bg-white p-4">
            <div className="mb-3 text-[14px] font-semibold text-ink">全局素材</div>
            <div className="space-y-4">
              {(["characters", "scenes", "props"] as AssetGroup[]).map((group) => {
                const Icon = assetGroupIcon[group];
                const items = globalAssetRecords(assets, group);
                return (
                  <div key={group} className="grid grid-cols-[84px_minmax(0,1fr)] gap-3">
                    <div className="flex items-center gap-1.5 text-[12px] font-medium text-ink-soft">
                      <Icon size={15} />
                      {assetGroupTitle[group]}
                    </div>
                    <div className="flex gap-2 overflow-x-auto pb-1">
                      {items.length > 0 ? (
                        items.map((asset, index) => {
                          const image = assetImage(asset);
                          const id = assetId(asset, `${group}-${index + 1}`);
                          return (
                            <button
                              key={id}
                              type="button"
                              onClick={() => openAssetPreview(group, asset, `${group}-${index + 1}`)}
                              disabled={!image}
                              className="w-24 shrink-0 overflow-hidden rounded-xl border border-line bg-canvas text-left transition-colors hover:border-accent disabled:cursor-default disabled:hover:border-line"
                            >
                              {image ? (
                                <img src={image} alt={assetName(asset, id)} className="h-16 w-full object-cover" />
                              ) : (
                                <div className="flex h-16 items-center justify-center text-[11px] text-ink-soft">待生成</div>
                              )}
                              <div className="truncate px-2 py-1 text-[11px] text-ink-soft">@{assetName(asset, id)}</div>
                            </button>
                          );
                        })
                      ) : (
                        <div className="text-[12px] text-ink-soft">暂无素材</div>
                      )}
                    </div>
                  </div>
                );
              })}
              <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-3">
                <div className="flex items-center gap-1.5 text-[12px] font-medium text-ink-soft">
                  <ImageIcon size={15} />
                  视觉风格
                </div>
                <div className="rounded-xl bg-canvas px-3 py-2 text-[13px] leading-relaxed text-ink">
                  {stringValue(assets.visual_style?.name) || stringValue(assets.visual_style?.description) || "整片统一视觉风格"}
                </div>
              </div>
            </div>
          </section>

          <section className="mt-4 rounded-2xl border border-line bg-white p-4">
            <div className="mb-3 flex flex-wrap gap-2">
              {scenes.map((scene) => (
                <button
                  key={scene.scene_id}
                  type="button"
                  onClick={() => setSelectedSceneId(scene.scene_id)}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[12px]",
                    scene.scene_id === selectedScene?.scene_id ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-soft hover:bg-canvas",
                  )}
                >
                  分镜 {scene.scene_index}{dirtySceneIds.has(scene.scene_id) ? " · 已修改" : ""}
                </button>
              ))}
            </div>
            {selectedScene ? (
              <div className="space-y-4">
                <label className="grid gap-1.5 text-[12px] font-medium text-ink-soft">
                  故事线
                  <textarea value={selectedScene.storyline || ""} onChange={(event) => updateScene({ storyline: event.currentTarget.value })} className={textareaClass()} />
                </label>
                <div className="grid gap-3 rounded-2xl border border-line bg-canvas p-3">
                  <label className="grid gap-1.5 text-[12px] text-ink-soft">
                    <span className="font-semibold text-ink">镜头描述 <span className="font-normal text-ink-soft">可以通过 @ 来添加参考</span></span>
                    <SceneMentionEditor
                      text={shotDescriptionText(shot)}
                      shotDescription={{ ...shot, mentions: shotMentions }}
                      candidates={mentionCandidates}
                      onChange={updateShotDescription}
                    />
                  </label>
                </div>
                <label className="grid gap-1.5 text-[12px] font-medium text-ink-soft">
                  旁白
                  <textarea value={selectedScene.narration || ""} onChange={(event) => updateScene({ narration: event.currentTarget.value })} className={textareaClass()} />
                </label>
              </div>
            ) : null}
          </section>
        </div>

        <div className="flex min-h-0 flex-col border-l border-line bg-white">
          <div className="flex h-12 shrink-0 items-center justify-between px-4 text-[13px] text-ink-soft">
            <span>镜头预览</span>
            <span>共 {scenes.length} 个镜头</span>
          </div>
          <div className="flex min-h-0 flex-1 flex-col px-4 pb-4">
            <div className="flex min-h-[360px] flex-1 items-center justify-center rounded-2xl border border-line bg-canvas">
              {previewVideoUrl ? (
                <video
                  src={previewVideoUrl}
                  controls
                  playsInline
                  preload="metadata"
                  className="max-h-full max-w-full rounded-xl object-contain"
                />
              ) : previewUrl ? (
                <img src={previewUrl} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <div className="text-[13px] text-ink-soft">暂无预览</div>
              )}
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {scenes.map((scene) => {
                const thumb = collectSceneImageUrls(scene, assets)[0] || "";
                const sceneVideo = sceneVideoForScene(scene, generatedSceneVideos);
                return (
                  <button
                    key={scene.scene_id}
                    type="button"
                    onClick={() => setSelectedSceneId(scene.scene_id)}
                    className={cn(
                      "w-32 shrink-0 overflow-hidden rounded-xl border bg-canvas text-left",
                      scene.scene_id === selectedScene?.scene_id ? "border-accent" : "border-line",
                    )}
                  >
                    {sceneVideo?.video_url ? (
                      <video src={sceneVideo.video_url} muted playsInline preload="metadata" className="h-20 w-full object-cover" />
                    ) : thumb ? (
                      <img src={thumb} alt="" className="h-20 w-full object-cover" />
                    ) : (
                      <div className="flex h-20 items-center justify-center text-[11px] text-ink-soft">分镜 {scene.scene_index}</div>
                    )}
                    <div className="truncate px-2 py-1.5 text-[12px] font-medium text-ink">
                      分镜 {scene.scene_index}{dirtySceneIds.has(scene.scene_id) ? " · 已修改" : ""}
                    </div>
                  </button>
                );
              })}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <button type="button" onClick={onClose} className="rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas">
                保存
              </button>
              <button
                type="button"
                onClick={sceneAssetQuotaPaused ? onRetrySceneAssets : onGenerateVideo}
                className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90"
              >
                <Sparkles size={15} />
                {sceneAssetQuotaPaused ? "继续生成参考图" : "确认并生成视频"}
              </button>
            </div>
          </div>
        </div>
      </div>
      {previewAsset ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 px-4" role="dialog" aria-modal="true">
          <div className="relative w-full max-w-[672px] rounded-[8px] bg-white p-8 shadow-[0_24px_80px_rgba(15,23,42,0.28)]">
            <button
              type="button"
              onClick={() => setPreviewAsset(null)}
              className="absolute right-8 top-8 flex h-9 w-9 items-center justify-center rounded-full text-ink hover:bg-canvas"
              aria-label="关闭"
            >
              <X size={22} />
            </button>
            <div className="pr-12 text-[22px] font-semibold text-ink">{previewAsset.name}</div>
            <div className="mt-5 overflow-hidden rounded-[8px] bg-canvas">
              <div className="relative mx-auto flex max-h-[420px] min-h-[260px] items-center justify-center">
                <img src={previewAsset.source_image_url} alt={previewAsset.name} className="max-h-[420px] w-full object-contain" />
                <div className="absolute right-4 top-4 flex overflow-hidden rounded-[8px] bg-ink/55 text-white backdrop-blur">
                  <button
                    type="button"
                    onClick={referencePreviewAsset}
                    className="flex h-10 w-10 items-center justify-center hover:bg-white/15"
                    title="引用素材"
                    aria-label="引用素材"
                  >
                    <ImageIcon size={17} />
                  </button>
                  <button
                    type="button"
                    onClick={deletePreviewAsset}
                    className="flex h-10 w-10 items-center justify-center hover:bg-white/15"
                    title="删除素材"
                    aria-label="删除素材"
                  >
                    <Trash2 size={17} />
                  </button>
                  <a
                    href={previewAsset.source_image_url}
                    download={previewAsset.filename}
                    target="_blank"
                    rel="noreferrer"
                    className="flex h-10 w-10 items-center justify-center hover:bg-white/15"
                    title="下载"
                    aria-label="下载"
                  >
                    <Download size={17} />
                  </a>
                </div>
              </div>
            </div>
            <div className="mt-5 flex items-baseline gap-2">
              <span className="text-[15px] font-semibold text-ink">{previewAsset.name}</span>
              <span className="text-[12px] text-ink-soft">{previewAsset.asset_id}</span>
            </div>
            {previewAsset.description ? (
              <div className="mt-3 text-[13px] leading-relaxed text-ink-soft">{previewAsset.description}</div>
            ) : null}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
