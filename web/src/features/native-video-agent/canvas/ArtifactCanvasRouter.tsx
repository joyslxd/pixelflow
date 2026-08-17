import type { ReactNode } from "react";

import { VideoCanvasShell } from "./VideoCanvasShell";
import type { NativeCanvasHeader, NativeCanvasKind } from "./types";

interface ArtifactCanvasRouterProps {
  kind: NativeCanvasKind;
  header: NativeCanvasHeader;
  onClose?(): void;
  /** 既有全功能面板自带标题时关闭壳层标题，避免双层。 */
  showHeader?: boolean;
  script?: ReactNode;
  scenePackage?: ReactNode;
  sceneAsset?: ReactNode;
  sceneVideo?: ReactNode;
  qualityReview?: ReactNode;
  delivery?: ReactNode;
  planMarkdown?: ReactNode;
  legacyCanvas?: ReactNode;
}

/** 按产物类型路由到对应 Canvas 插槽；壳层统一头部。 */
export function ArtifactCanvasRouter({
  kind,
  header,
  onClose,
  showHeader,
  script,
  scenePackage,
  sceneAsset,
  sceneVideo,
  qualityReview,
  delivery,
  planMarkdown,
  legacyCanvas,
}: ArtifactCanvasRouterProps) {
  const body =
    kind === "script"
      ? script
      : kind === "scene_package"
        ? scenePackage
        : kind === "scene_asset"
          ? sceneAsset
          : kind === "scene_video"
            ? sceneVideo
            : kind === "quality_review"
              ? qualityReview
              : kind === "delivery"
                ? delivery
                : kind === "plan_markdown"
                  ? planMarkdown
                  : legacyCanvas;

  const resolvedShowHeader =
    showHeader ??
    (kind === "scene_video" || kind === "quality_review" || kind === "delivery");

  return (
    <VideoCanvasShell
      header={header}
      onClose={onClose}
      showHeader={resolvedShowHeader}
    >
      {body ?? (
        <div className="p-4 text-sm text-slate-500">暂无可用的工作台视图。</div>
      )}
    </VideoCanvasShell>
  );
}
