import { LegacyWorkspace } from "@/features/legacy-workspace/LegacyWorkspace";

/** P0 继续复用成熟工作台，同时逐步把状态与交互迁入 VideoAgent 边界。 */
export function VideoAgentWorkspace() {
  return <LegacyWorkspace />;
}
