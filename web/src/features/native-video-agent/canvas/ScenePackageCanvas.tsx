import type { ComponentProps } from "react";

import { VideoAgentStoryboardSurface } from "@/features/video-agent/VideoAgentStoryboardSurface";

/** 场景包 Canvas：复用分镜编辑面。 */
export function ScenePackageCanvas(
  props: ComponentProps<typeof VideoAgentStoryboardSurface>,
) {
  return <VideoAgentStoryboardSurface {...props} />;
}
