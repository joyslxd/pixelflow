/** 右侧 Canvas 产物类型（与卡片打开目标对齐）。 */
export type NativeCanvasKind =
  | "script"
  | "scene_package"
  | "scene_asset"
  | "scene_video"
  | "quality_review"
  | "delivery"
  | "plan_markdown"
  | "legacy_canvas";

export interface NativeCanvasHeader {
  title: string;
  versionLabel?: string | null;
  statusLabel?: string | null;
  stepLabel?: string | null;
  saveStatus?: "idle" | "saving" | "saved" | "error";
  dirtySceneCount?: number;
  regenerateComplete?: boolean;
}

/** 单镜修改只合并对应 sceneId，不扩大重生范围。 */
export function markDirtySceneIds(
  current: readonly string[] | null | undefined,
  sceneId: string,
): string[] {
  const id = sceneId.trim();
  if (!id) return [...(current || [])];
  return Array.from(new Set([...(current || []), id]));
}

/** 脏镜头重生完成后清空标记，并给出用户可见文案。 */
export function clearDirtyScenesAfterRegenerate(
  dirtySceneIds: readonly string[] | null | undefined,
): { dirtySceneIds: string[]; message: string } {
  const count = (dirtySceneIds || []).length;
  return {
    dirtySceneIds: [],
    message:
      count > 0
        ? `重新生成完成（${count} 个镜头）。`
        : "重新生成完成。",
  };
}

export function resolveCanvasKindFromArtifact(
  artifact: Record<string, unknown> | null | undefined,
): NativeCanvasKind | null {
  if (!artifact || typeof artifact !== "object") return null;
  const type = typeof artifact.type === "string" ? artifact.type : "";
  if (artifact.plan || type === "plan" || type === "plan_markdown") {
    return "plan_markdown";
  }
  if (artifact.videoScenePackages || type === "video_scene_packages") {
    return "scene_package";
  }
  if (type === "video_quality_review" || artifact.qualityReview) {
    return "quality_review";
  }
  if (type === "video_result" || artifact.mergedVideo) {
    return "delivery";
  }
  if (type === "script" || type === "script_preview" || artifact.script) {
    return "script";
  }
  return null;
}
