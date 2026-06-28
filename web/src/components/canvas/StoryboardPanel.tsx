import { ArrowLeft, Box, ImageIcon, MapPin, Sparkles, UserRound } from "lucide-react";
import { SceneMentionEditor } from "@/components/canvas/SceneMentionEditor";
import type { ChatMessage } from "@/lib/chat";
import { buildMentionCandidates, normalizeShotMentions, type SceneMention } from "@/lib/sceneMentions";
import {
  collectSceneImageUrls,
  stringArray,
  type GlobalSceneAssets,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";

interface StoryboardPanelProps {
  msg: ChatMessage;
  onUpdateVideoScenePackage?: (sceneId: string, patch: ScenePackagePatch) => void;
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

export function StoryboardPanel({
  msg,
  onUpdateVideoScenePackage,
  onGenerateVideo,
  onRetrySceneAssets,
  onClose,
}: StoryboardPanelProps) {
  const videoScenePackages = msg.artifact?.videoScenePackages;
  const scenes = (videoScenePackages?.scene_packages || []) as ScenePackageRecord[];
  const assets = globalAssets(videoScenePackages?.global_assets);
  const [selectedSceneId, setSelectedSceneId] = useState(scenes[0]?.scene_id || "");
  const selectedScene = scenes.find((scene) => scene.scene_id === selectedSceneId) || scenes[0];
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
                            <div key={id} className="w-24 shrink-0 overflow-hidden rounded-xl border border-line bg-canvas">
                              {image ? (
                                <img src={image} alt={assetName(asset, id)} className="h-16 w-full object-cover" />
                              ) : (
                                <div className="flex h-16 items-center justify-center text-[11px] text-ink-soft">待生成</div>
                              )}
                              <div className="truncate px-2 py-1 text-[11px] text-ink-soft">@{assetName(asset, id)}</div>
                            </div>
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
                  分镜 {scene.scene_index}
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
              {previewUrl ? (
                <img src={previewUrl} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <div className="text-[13px] text-ink-soft">暂无预览图</div>
              )}
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {scenes.map((scene) => {
                const thumb = collectSceneImageUrls(scene, assets)[0] || "";
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
                    {thumb ? <img src={thumb} alt="" className="h-20 w-full object-cover" /> : <div className="flex h-20 items-center justify-center text-[11px] text-ink-soft">分镜 {scene.scene_index}</div>}
                    <div className="truncate px-2 py-1.5 text-[12px] font-medium text-ink">分镜 {scene.scene_index}</div>
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
                {sceneAssetQuotaPaused ? "继续生成参考图" : "保存并生成分镜"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
