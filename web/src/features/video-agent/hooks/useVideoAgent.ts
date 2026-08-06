import { useCallback, useMemo, useState } from "react";

import {
  resolveSelectedSceneId,
  selectSceneEvidence,
  type VideoWorkspaceProjectionState,
} from "../state/workspace";

interface VideoAgentSelection {
  workspaceId: string;
  sceneId: string;
}

export function useVideoAgent(workspaceState: VideoWorkspaceProjectionState) {
  const [selection, setSelection] = useState<VideoAgentSelection | null>(null);
  const workspace = workspaceState.current;
  const requestedSceneId = workspace
    && selection?.workspaceId === workspace.workspaceId
    ? selection.sceneId
    : null;
  const selectedSceneId = resolveSelectedSceneId(
    workspaceState,
    requestedSceneId,
  );
  const selectedEvidence = useMemo(
    () => selectedSceneId
      ? selectSceneEvidence(workspaceState, selectedSceneId)
      : null,
    [selectedSceneId, workspaceState],
  );
  const selectScene = useCallback((sceneId: string) => {
    const current = workspaceState.current;
    if (
      !current
      || resolveSelectedSceneId(workspaceState, sceneId) !== sceneId
    ) return;
    setSelection({ workspaceId: current.workspaceId, sceneId });
  }, [workspaceState]);

  return {
    workspace,
    selectedSceneId,
    selectedEvidence,
    selectScene,
  };
}
