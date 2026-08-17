import type { ComponentProps } from "react";

import { AgentScriptPreviewPanel } from "@/features/video-agent/AgentScriptPreviewPanel";

/** 脚本 Canvas：复用既有预览/编辑面板，不重写编辑器。 */
export function ScriptCanvas(props: ComponentProps<typeof AgentScriptPreviewPanel>) {
  return <AgentScriptPreviewPanel {...props} />;
}
