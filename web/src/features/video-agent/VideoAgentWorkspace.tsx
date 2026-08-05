import { LegacyWorkspace } from "@/features/legacy-workspace/LegacyWorkspace";

/**
 * P0 keeps the proven scene-package experience intact while its state and
 * interactions are migrated behind the VideoAgent feature boundary.
 */
export function VideoAgentWorkspace() {
  return <LegacyWorkspace />;
}
