/** 兼容入口：页面与 Hook 只从这里读取 reducer / 投影，不直接依赖旧 Snapshot 壳。 */

export {
  applyPublicEvent,
  foldAppliedEvent,
  initialAgentWorkspaceState,
  isRecoveryRequired,
  isTerminalSnapshot,
  isTerminalStatus,
  mergeMessages,
  normalizeEventType,
  normalizePublicEvent,
  replaceVideoWorkspace,
  setConnection,
  shouldReloadSnapshot,
  type AgentWorkspaceState,
  type ConnectionState,
  type EventApplyResult,
  type InputStatus,
} from "./reducer.js";
export { hydrateSnapshot, projectVisible, type VisibleProjection } from "./snapshotProjector.js";
