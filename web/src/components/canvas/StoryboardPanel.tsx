import { ArrowLeft, Box, Download, ImageIcon, LoaderCircle, Maximize2, MapPin, Plus, Replace, Sparkles, Trash2, UserRound, X } from "lucide-react";
import { SceneMentionEditor } from "@/components/canvas/SceneMentionEditor";
import { SceneAssetReplacementPicker } from "@/components/canvas/SceneAssetReplacementPicker";
import type { ChatMessage } from "@/lib/chat";
import { buildMentionCandidates, normalizeShotMentions, type SceneMention } from "@/lib/sceneMentions";
import {
  collectSceneImageUrls,
  MAX_REFERENCE_IMAGE_COUNT,
  stringArray,
  type GlobalSceneAssetGroup,
  type GlobalSceneAssets,
  type SceneGlobalAssetReference,
  type SceneGlobalAssetReplacement,
  type ScenePackagePatch,
  type ScenePackageRecord,
} from "@/lib/scenePackages";
import { composeShotDescriptionFields, parseShotDescriptionFields, shotDescriptionHasStructuredFields } from "@/lib/shotDescriptionDisplay";
import { cn } from "@/lib/utils";
import { useEffect, useMemo, useRef, useState } from "react";

export interface StoryboardPanelProps {
  msg: ChatMessage;
  /** 正在生成分镜视频的 scene_id；主预览与缩略图盖灰蒙版+转圈。 */
  generatingSceneIds?: readonly string[];
  /** Workspace / 资产包已合并成功的成片 HTTPS URL。 */
  mergedVideoUrl?: string | null;
  onUpdateVideoScenePackage?: (sceneId: string, patch: ScenePackagePatch) => void | Promise<void>;
  deferSceneUpdates?: boolean;
  onReferenceGlobalAsset?: (asset: SceneGlobalAssetReference) => void;
  onDeleteGlobalAsset?: (asset: SceneGlobalAssetReference) => void;
  onReplaceGlobalAsset?: (asset: SceneGlobalAssetReference, replacement: SceneGlobalAssetReplacement) => void;
  onSupervisorReplaceGlobalAsset?: (asset: SceneGlobalAssetReference, replacement: SceneGlobalAssetReplacement) => void;
  onAddGlobalAsset?: (assetGroup: GlobalSceneAssetGroup, replacement: SceneGlobalAssetReplacement) => void;
  onGenerateVideo?: (sceneId?: string) => void;
  onRetrySceneAssets?: () => void;
  onSave?: () => void | Promise<void>;
  onClose?: () => void;
}

type AssetGroup = GlobalSceneAssetGroup;

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

function sceneWithDraft(
  scene: ScenePackageRecord | undefined,
  patch: ScenePackagePatch | undefined,
): ScenePackageRecord | undefined {
  if (!scene || !patch) return scene;
  return {
    ...scene,
    ...(typeof patch.storyline === "string" ? { storyline: patch.storyline } : {}),
    ...(typeof patch.narration === "string" ? { narration: patch.narration } : {}),
    ...(patch.shot_description ? { shot_description: patch.shot_description } : {}),
    ...(Array.isArray(patch.reference_asset_ids)
      ? { reference_asset_ids: patch.reference_asset_ids }
      : {}),
  };
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

function mentionsStillInText(
  mentions: SceneMention[],
  text: string,
): SceneMention[] {
  return mentions.filter((mention) => (
    text.includes(`@${mention.name}`) || text.includes(`@${mention.asset_id}`)
  ));
}

function ShotDescriptionStructuredEditor({
  text,
  mentions,
  candidates,
  shotDescription,
  onChange,
}: {
  text: string;
  mentions: SceneMention[];
  candidates: ReturnType<typeof buildMentionCandidates>;
  shotDescription: Record<string, unknown>;
  onChange: (next: { text: string; mentions: SceneMention[] }) => void;
}) {
  // 本地字段态：打字时不要每次 compose→parse 重建表格行，否则 contentEditable 光标会乱跳。
  const emittedTextRef = useRef(text);
  const [fields, setFields] = useState(() => parseShotDescriptionFields(text));

  useEffect(() => {
    if (text === emittedTextRef.current) return;
    emittedTextRef.current = text;
    setFields(parseShotDescriptionFields(text));
  }, [text]);

  if (fields.length === 0) return null;

  const updateField = (index: number, next: { text: string; mentions: SceneMention[] }) => {
    setFields((current) => {
      const nextFields = current.map((field, fieldIndex) => (
        fieldIndex === index ? { ...field, value: next.text } : field
      ));
      // live 模式：保留空行与空格，避免「删光字段 → 整行消失」和清洗导致的 DOM 重绘。
      const composed = composeShotDescriptionFields(nextFields, { mode: "live" });
      const mergedById = new Map<string, SceneMention>();
      for (const mention of [...mentions, ...next.mentions]) {
        mergedById.set(mention.asset_id, mention);
      }
      emittedTextRef.current = composed;
      onChange({
        text: composed,
        mentions: mentionsStillInText([...mergedById.values()], composed).slice(0, MAX_REFERENCE_IMAGE_COUNT),
      });
      return nextFields;
    });
  };

  return (
    <div className="grid gap-2">
      <div className="overflow-hidden rounded-xl border border-line bg-white">
        <table className="w-full border-collapse text-left text-[13px]">
          <tbody>
            {fields.map((field, index) => (
              <tr key={`${field.label}-${index}`} className="border-b border-line last:border-b-0">
                <th className="w-24 shrink-0 bg-canvas/70 px-3 py-2 align-top text-[12px] font-semibold text-ink-soft">
                  {field.label}
                </th>
                <td className="px-3 py-1.5 leading-relaxed text-ink">
                  <SceneMentionEditor
                    text={field.value}
                    shotDescription={{ ...shotDescription, text: field.value, mentions }}
                    candidates={candidates}
                    onChange={(next) => updateField(index, next)}
                    compact
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[12px] text-ink-soft">
        已关联 {mentions.length}/{MAX_REFERENCE_IMAGE_COUNT}
        {mentions.length >= MAX_REFERENCE_IMAGE_COUNT
          ? <span className="ml-2 text-amber">最多 9 张不同图片，已关联素材可重复引用</span>
          : <span className="ml-2">在任意字段输入 @ 关联参考图</span>}
      </div>
    </div>
  );
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
  const sceneVideos = generatedSceneVideos?.scene_videos || [];
  return (
    sceneVideos.find((video) => video.scene_id === scene.scene_id) ||
    sceneVideos.find((video) => Number(video.scene_index) === Number(scene.scene_index))
  );
}

function SceneVideoGeneratingOverlay({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className="absolute inset-0 z-10 flex items-center justify-center bg-black/45"
      aria-label="分镜视频生成中"
    >
      <LoaderCircle
        size={compact ? 18 : 28}
        className="animate-spin text-white"
        aria-hidden
      />
    </div>
  );
}

export function StoryboardPanel({
  msg,
  generatingSceneIds,
  mergedVideoUrl: mergedVideoUrlProp,
  onUpdateVideoScenePackage,
  deferSceneUpdates = false,
  onReferenceGlobalAsset,
  onDeleteGlobalAsset,
  onReplaceGlobalAsset,
  onSupervisorReplaceGlobalAsset,
  onAddGlobalAsset,
  onGenerateVideo: onGenerateVideoRequested,
  onRetrySceneAssets: onRetrySceneAssetsRequested,
  onSave: onSaveRequested,
  onClose,
}: StoryboardPanelProps) {
  const videoScenePackages = msg.artifact?.videoScenePackages;
  const generatedSceneVideos = msg.artifact?.generatedSceneVideos;
  const scenes = (videoScenePackages?.scene_packages || []) as ScenePackageRecord[];
  const assets = globalAssets(videoScenePackages?.global_assets);
  const generatingIdSet = useMemo(
    () => new Set((generatingSceneIds || []).map((id) => String(id || "").trim()).filter(Boolean)),
    [generatingSceneIds],
  );
  const mergedVideoUrl = useMemo(() => {
    const fromProp = typeof mergedVideoUrlProp === "string" ? mergedVideoUrlProp.trim() : "";
    if (fromProp.toLowerCase().startsWith("https://")) return fromProp;
    const fromArtifact = msg.artifact?.mergedVideo?.ok
      ? String(msg.artifact.mergedVideo.merged_video_url || "").trim()
      : "";
    return fromArtifact.toLowerCase().startsWith("https://") ? fromArtifact : "";
  }, [mergedVideoUrlProp, msg.artifact?.mergedVideo]);
  const [selectedSceneId, setSelectedSceneId] = useState(scenes[0]?.scene_id || "");
  const [sceneDraftPatches, setSceneDraftPatches] = useState<Record<string, ScenePackagePatch>>({});
  const [previewAsset, setPreviewAsset] = useState<SceneGlobalAssetReference | null>(null);
  const [replacementTarget, setReplacementTarget] = useState<SceneGlobalAssetReference | null>(null);
  const [additionTarget, setAdditionTarget] = useState<AssetGroup | null>(null);
  // 镜头预览放大：覆盖左侧对话与中栏素材编辑，返回后回到双栏分镜面。
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const [mergedPreviewOpen, setMergedPreviewOpen] = useState(false);

  useEffect(() => {
    if (!previewExpanded && !mergedPreviewOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreviewExpanded(false);
        setMergedPreviewOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [previewExpanded, mergedPreviewOpen]);
  const authoritativeSelectedScene = scenes.find((scene) => scene.scene_id === selectedSceneId) || scenes[0];
  const selectedScenePatch = authoritativeSelectedScene
    ? sceneDraftPatches[authoritativeSelectedScene.scene_id]
    : undefined;
  const selectedScene = sceneWithDraft(authoritativeSelectedScene, selectedScenePatch);
  const hasSceneDraft = Object.keys(sceneDraftPatches).length > 0;
  const dirtySceneIds = new Set(msg.artifact?.videoScenePackageEditedSceneIds || []);
  const selectedReferenceIds = stringArray(selectedScene?.reference_asset_ids);
  const shot = shotRecord(selectedScene);
  const shotText = shotDescriptionText(shot);
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
  const selectedSceneGenerating = Boolean(
    selectedScene?.scene_id && generatingIdSet.has(selectedScene.scene_id),
  );

  const updateScene = (patch: ScenePackagePatch) => {
    if (!selectedScene) return;
    if (deferSceneUpdates) {
      setSceneDraftPatches((current) => ({
        ...current,
        [selectedScene.scene_id]: {
          ...current[selectedScene.scene_id],
          ...patch,
        },
      }));
      return;
    }
    onUpdateVideoScenePackage?.(selectedScene.scene_id, patch);
  };

  const saveStoryboardDraft = async () => {
    if (deferSceneUpdates && authoritativeSelectedScene && selectedScenePatch) {
      await onUpdateVideoScenePackage?.(authoritativeSelectedScene.scene_id, selectedScenePatch);
    }
    await onSaveRequested?.();
  };
  const onSave = deferSceneUpdates ? saveStoryboardDraft : onSaveRequested;
  const onGenerateVideo = hasSceneDraft
    ? undefined
    : (onGenerateVideoRequested
      ? () => onGenerateVideoRequested(selectedScene?.scene_id)
      : undefined);
  const onRetrySceneAssets = hasSceneDraft ? undefined : onRetrySceneAssetsRequested;

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
    const id = assetId(record, fallback);
    const name = assetName(record, id);
    setPreviewAsset({
      ...record,
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

  const replacePreviewAsset = () => {
    if (!previewAsset) return;
    setReplacementTarget(previewAsset);
    setPreviewAsset(null);
  };

  const confirmReplacement = (replacement: SceneGlobalAssetReplacement) => {
    if (!replacementTarget) return;
    (onSupervisorReplaceGlobalAsset ?? onReplaceGlobalAsset)?.(replacementTarget, replacement);
    setReplacementTarget(null);
  };

  const confirmAddition = (replacement: SceneGlobalAssetReplacement) => {
    if (!additionTarget) return;
    onAddGlobalAsset?.(additionTarget, replacement);
    setAdditionTarget(null);
  };

  return (
    <aside className="fixed inset-0 z-50 flex h-full w-full min-w-0 max-w-none flex-col border-l border-line bg-[#f8fafc] xl:static xl:z-auto xl:w-[52vw] xl:min-w-[680px] xl:max-w-[980px]">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4">
        <button type="button" onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-full hover:bg-canvas" aria-label="返回">
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold text-ink">{msg.artifact?.title || "storyboard.json"}</div>
          <div className="text-[12px] text-ink-soft">共 {scenes.length} 个镜头</div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(280px,42%)]">
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
                      {items.map((asset, index) => {
                        const image = assetImage(asset);
                        const id = assetId(asset, `${group}-${index + 1}`);
                        return (
                          <button
                            key={id}
                            type="button"
                            onClick={() => openAssetPreview(group, asset, `${group}-${index + 1}`)}
                            className="w-24 shrink-0 overflow-hidden rounded-xl border border-line bg-canvas text-left transition-colors hover:border-accent"
                          >
                            {image ? (
                              <img src={image} alt={assetName(asset, id)} className="h-16 w-full object-cover" />
                            ) : (
                              <div className="flex h-16 items-center justify-center text-[11px] text-ink-soft">待生成</div>
                            )}
                            <div className="truncate px-2 py-1 text-[11px] text-ink-soft">@{assetName(asset, id)}</div>
                          </button>
                        );
                      })}
                      <button
                        type="button"
                        onClick={() => setAdditionTarget(group)}
                        className="w-24 shrink-0 overflow-hidden rounded-xl border border-dashed border-line bg-white text-left transition-colors hover:border-accent hover:bg-accent-soft/30"
                        title={`添加${assetGroupTitle[group]}素材`}
                        aria-label={`添加${assetGroupTitle[group]}素材`}
                      >
                        <div className="flex h-16 items-center justify-center text-accent">
                          <Plus size={22} />
                        </div>
                        <div className="truncate px-2 py-1 text-center text-[11px] text-ink-soft">添加素材</div>
                      </button>
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
                  disabled={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id}
                  title={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id
                    ? "请先保存当前分镜"
                    : undefined}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-[12px] disabled:cursor-not-allowed disabled:opacity-50",
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
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[12px] font-semibold text-ink">
                      镜头描述 <span className="font-normal text-ink-soft">点击字段直接编辑，输入 @ 添加参考图</span>
                    </span>
                  </div>
                  {shotDescriptionHasStructuredFields(shotText) ? (
                    <ShotDescriptionStructuredEditor
                      text={shotText}
                      mentions={shotMentions}
                      candidates={mentionCandidates}
                      shotDescription={{ ...shot, mentions: shotMentions }}
                      onChange={updateShotDescription}
                    />
                  ) : (
                    <SceneMentionEditor
                      text={shotText}
                      shotDescription={{ ...shot, mentions: shotMentions }}
                      candidates={mentionCandidates}
                      onChange={updateShotDescription}
                      placeholder="点击编辑镜头描述，输入 @ 关联参考图"
                    />
                  )}
                </div>
              </div>
            ) : null}
          </section>
        </div>

        <div className="flex min-h-0 flex-col border-l border-line bg-white">
          <div className="flex h-12 shrink-0 items-center justify-between gap-2 px-4 text-[13px] text-ink-soft">
            <button
              type="button"
              onClick={() => setPreviewExpanded(true)}
              className="inline-flex items-center gap-1.5 rounded-lg px-1.5 py-1 font-medium text-ink hover:bg-canvas"
              title="放大镜头预览"
              aria-label="放大镜头预览"
            >
              <span>镜头预览</span>
              <Maximize2 size={14} className="text-ink-soft" aria-hidden />
            </button>
            <span>共 {scenes.length} 个镜头</span>
          </div>
          <div className="flex min-h-0 flex-1 flex-col px-4 pb-4">
            <div className="relative flex min-h-[200px] flex-1 items-center justify-center overflow-hidden rounded-2xl border border-line bg-canvas xl:min-h-[360px]">
              {previewVideoUrl ? (
                <video
                  src={previewVideoUrl}
                  controls={!selectedSceneGenerating}
                  playsInline
                  preload="metadata"
                  className="max-h-full max-w-full rounded-xl object-contain"
                />
              ) : previewUrl ? (
                <img src={previewUrl} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <div className="text-[13px] text-ink-soft">暂无预览</div>
              )}
              {selectedSceneGenerating ? <SceneVideoGeneratingOverlay /> : null}
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {scenes.map((scene) => {
                const thumb = collectSceneImageUrls(scene, assets)[0] || "";
                const sceneVideo = sceneVideoForScene(scene, generatedSceneVideos);
                const sceneGenerating = generatingIdSet.has(scene.scene_id);
                return (
                  <button
                    key={scene.scene_id}
                    type="button"
                    onClick={() => setSelectedSceneId(scene.scene_id)}
                    disabled={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id}
                    title={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id
                      ? "请先保存当前分镜"
                      : (sceneGenerating ? "分镜视频生成中" : undefined)}
                    className={cn(
                      "w-32 shrink-0 overflow-hidden rounded-xl border bg-canvas text-left disabled:cursor-not-allowed disabled:opacity-50",
                      scene.scene_id === selectedScene?.scene_id ? "border-accent" : "border-line",
                    )}
                  >
                    <div className="relative h-20 w-full overflow-hidden bg-canvas">
                      {sceneVideo?.video_url ? (
                        <video src={sceneVideo.video_url} muted playsInline preload="metadata" className="h-20 w-full object-cover" />
                      ) : thumb ? (
                        <img src={thumb} alt="" className="h-20 w-full object-cover" />
                      ) : (
                        <div className="flex h-20 items-center justify-center text-[11px] text-ink-soft">分镜 {scene.scene_index}</div>
                      )}
                      {sceneGenerating ? <SceneVideoGeneratingOverlay compact /> : null}
                    </div>
                    <div className="truncate px-2 py-1.5 text-[12px] font-medium text-ink">
                      分镜 {scene.scene_index}
                      {sceneGenerating ? " · 生成中" : (dirtySceneIds.has(scene.scene_id) ? " · 已修改" : "")}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <div className="shrink-0 border-t border-line bg-white px-4 py-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            onClick={onSave}
            disabled={!onSave}
            title={onSave ? undefined : "当前运行模式不允许保存"}
            className="rounded-xl border border-line py-2.5 text-[13px] font-medium text-ink hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50"
          >
            保存
          </button>
          <button
            type="button"
            onClick={sceneAssetQuotaPaused ? onRetrySceneAssets : onGenerateVideo}
            disabled={
              sceneAssetQuotaPaused
                ? !onRetrySceneAssets
                : (
                  !onGenerateVideo
                  || Boolean(selectedScene && generatingIdSet.has(selectedScene.scene_id))
                )
            }
            title={
              sceneAssetQuotaPaused
                ? (onRetrySceneAssets ? undefined : "当前运行模式不允许继续生成参考图")
                : (
                  selectedScene && generatingIdSet.has(selectedScene.scene_id)
                    ? "当前分镜正在生成中"
                    : (onGenerateVideo ? undefined : "当前运行模式不允许生成视频")
                )
            }
            className="flex items-center justify-center gap-1.5 rounded-xl bg-brand py-2.5 text-[13px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles size={15} />
            {sceneAssetQuotaPaused
              ? "继续生成参考图"
              : (selectedScene && generatingIdSet.has(selectedScene.scene_id)
                ? `分镜 ${selectedScene.scene_index} 生成中…`
                : (selectedScene
                  ? `确认并生成分镜 ${selectedScene.scene_index}`
                  : "确认并生成视频"))}
          </button>
        </div>
        {mergedVideoUrl ? (
          <button
            type="button"
            onClick={() => setMergedPreviewOpen(true)}
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-xl border border-brand/30 bg-brand/5 py-2.5 text-[13px] font-medium text-brand hover:bg-brand/10"
          >
            查看合并后的视频
          </button>
        ) : null}
      </div>
      {mergedPreviewOpen && mergedVideoUrl ? (
        <div
          className="fixed inset-0 z-[70] flex flex-col bg-[#0b1220]"
          role="dialog"
          aria-modal="true"
          aria-label="合并成片预览"
        >
          <div className="flex h-14 shrink-0 items-center gap-3 border-b border-white/10 bg-black/40 px-4 text-white">
            <button
              type="button"
              onClick={() => setMergedPreviewOpen(false)}
              className="flex h-9 items-center gap-1.5 rounded-full px-3 text-[13px] font-medium hover:bg-white/10"
              aria-label="返回分镜编辑"
            >
              <ArrowLeft size={18} aria-hidden />
              返回
            </button>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-semibold">合并成片预览</div>
              <div className="text-[12px] text-white/65">Esc 也可返回</div>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center px-4 pb-4 pt-3 sm:px-8">
            <div className="relative flex max-h-full w-full max-w-5xl items-center justify-center overflow-hidden rounded-2xl border border-white/10 bg-black/35">
              <video
                src={mergedVideoUrl}
                controls
                playsInline
                preload="metadata"
                className="max-h-[min(80vh,720px)] max-w-full object-contain"
              />
            </div>
          </div>
        </div>
      ) : null}
      {previewExpanded ? (
        <div
          className="fixed inset-0 z-[70] flex flex-col bg-[#0b1220]"
          role="dialog"
          aria-modal="true"
          aria-label="放大镜头预览"
        >
          <div className="flex h-14 shrink-0 items-center gap-3 border-b border-white/10 bg-black/40 px-4 text-white">
            <button
              type="button"
              onClick={() => setPreviewExpanded(false)}
              className="flex h-9 items-center gap-1.5 rounded-full px-3 text-[13px] font-medium hover:bg-white/10"
              aria-label="返回分镜编辑"
            >
              <ArrowLeft size={18} aria-hidden />
              返回
            </button>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-semibold">
                镜头预览
                {selectedScene ? ` · 分镜 ${selectedScene.scene_index}` : ""}
              </div>
              <div className="text-[12px] text-white/65">共 {scenes.length} 个镜头 · Esc 也可返回</div>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col px-4 pb-4 pt-3 sm:px-8">
            <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-2xl border border-white/10 bg-black/35">
              {previewVideoUrl ? (
                <video
                  src={previewVideoUrl}
                  controls={!selectedSceneGenerating}
                  playsInline
                  preload="metadata"
                  className="max-h-full max-w-full object-contain"
                />
              ) : previewUrl ? (
                <img src={previewUrl} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <div className="text-[14px] text-white/60">暂无预览</div>
              )}
              {selectedSceneGenerating ? <SceneVideoGeneratingOverlay /> : null}
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {scenes.map((scene) => {
                const thumb = collectSceneImageUrls(scene, assets)[0] || "";
                const sceneVideo = sceneVideoForScene(scene, generatedSceneVideos);
                const sceneGenerating = generatingIdSet.has(scene.scene_id);
                return (
                  <button
                    key={`expanded-${scene.scene_id}`}
                    type="button"
                    onClick={() => setSelectedSceneId(scene.scene_id)}
                    disabled={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id}
                    title={deferSceneUpdates && hasSceneDraft && scene.scene_id !== selectedScene?.scene_id
                      ? "请先保存当前分镜"
                      : (sceneGenerating ? "分镜视频生成中" : undefined)}
                    className={cn(
                      "w-36 shrink-0 overflow-hidden rounded-xl border bg-white/5 text-left text-white disabled:cursor-not-allowed disabled:opacity-50",
                      scene.scene_id === selectedScene?.scene_id ? "border-accent" : "border-white/15",
                    )}
                  >
                    <div className="relative h-24 w-full overflow-hidden bg-black/40">
                      {sceneVideo?.video_url ? (
                        <video src={sceneVideo.video_url} muted playsInline preload="metadata" className="h-24 w-full object-cover" />
                      ) : thumb ? (
                        <img src={thumb} alt="" className="h-24 w-full object-cover" />
                      ) : (
                        <div className="flex h-24 items-center justify-center text-[11px] text-white/55">分镜 {scene.scene_index}</div>
                      )}
                      {sceneGenerating ? <SceneVideoGeneratingOverlay compact /> : null}
                    </div>
                    <div className="truncate px-2 py-1.5 text-[12px] font-medium">
                      分镜 {scene.scene_index}
                      {sceneGenerating ? " · 生成中" : (dirtySceneIds.has(scene.scene_id) ? " · 已修改" : "")}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
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
                {previewAsset.source_image_url ? (
                  <img src={previewAsset.source_image_url} alt={previewAsset.name} className="max-h-[420px] w-full object-contain" />
                ) : (
                  <div className="text-[13px] text-ink-soft">当前素材没有可用图片，可以直接删除后重新添加。</div>
                )}
                <div className="absolute right-4 top-4 flex overflow-hidden rounded-[8px] bg-ink/55 text-white backdrop-blur">
                  <button
                    type="button"
                    onClick={referencePreviewAsset}
                    disabled={!previewAsset.source_image_url}
                    className="flex h-10 w-10 items-center justify-center hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-40"
                    title="引用素材"
                    aria-label="引用素材"
                  >
                    <ImageIcon size={17} />
                  </button>
                  <button
                    type="button"
                    onClick={replacePreviewAsset}
                    disabled={!previewAsset.source_image_url}
                    className="flex h-10 w-10 items-center justify-center hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-40"
                    title="替换素材"
                    aria-label="替换素材"
                  >
                    <Replace size={17} />
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
                  {previewAsset.source_image_url ? (
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
                  ) : (
                    <button type="button" disabled className="flex h-10 w-10 cursor-not-allowed items-center justify-center opacity-40" aria-label="下载">
                      <Download size={17} />
                    </button>
                  )}
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
      {replacementTarget ? (
        <SceneAssetReplacementPicker
          open={Boolean(replacementTarget)}
          assetGroup={replacementTarget.asset_group}
          assetName={replacementTarget.name}
          onCancel={() => setReplacementTarget(null)}
          onConfirm={confirmReplacement}
        />
      ) : null}
      {additionTarget ? (
        <SceneAssetReplacementPicker
          open={Boolean(additionTarget)}
          operation="add"
          assetGroup={additionTarget}
          onCancel={() => setAdditionTarget(null)}
          onConfirm={confirmAddition}
        />
      ) : null}
    </aside>
  );
}
