/** 原生 Turn 组选择器。 */

import type { NativeAgentTurn, NativeVideoAgentUiState } from "./contracts.js";

/** 按 turnOrder 返回本对话全部 Turn 组。 */
export function selectNativeAgentTurns(
  state: NativeVideoAgentUiState,
): NativeAgentTurn[] {
  return state.turnOrder
    .map((turnId) => state.turns[turnId])
    .filter((turn): turn is NativeAgentTurn => Boolean(turn));
}

/** 当前仍在流式思考或回复、或有 running 工具的 Turn。 */
export function selectActiveNativeAgentTurn(
  state: NativeVideoAgentUiState,
): NativeAgentTurn | null {
  const turns = selectNativeAgentTurns(state);
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (
      turn.reasoningStatus === "streaming"
      || turn.responseStatus === "streaming"
      || turn.tools.some((tool) => tool.status === "running")
    ) {
      return turn;
    }
  }
  return null;
}

/** 展示顺序校验：思考 → 计划 → 活动 → 回答（是否有内容）。 */
export function nativeTurnSectionPresence(turn: NativeAgentTurn): {
  hasReasoning: boolean;
  hasPlan: boolean;
  hasActivity: boolean;
  hasResponse: boolean;
} {
  return {
    hasReasoning: turn.reasoningStatus !== "idle" || Boolean(turn.reasoningText.trim()),
    hasPlan: Boolean(turn.planId) || turn.planSteps.length > 0,
    hasActivity: turn.tools.length > 0,
    hasResponse: turn.responseStatus !== "idle" || Boolean(turn.responseText.trim()),
  };
}

/** 脚本相关工具：完成后结论气泡可提供「在右侧查看脚本」。 */
const SCRIPT_PREVIEW_TOOL_NAMES = new Set([
  "import_script",
  "apply_production_fields",
  "brainstorm_script",
]);

const SCRIPT_PREVIEW_RESPONSE_RE =
  /脚本已就绪|已导入脚本|已更新脚本|已补全生产字段|生产字段已齐|脚本预览|在右侧查看脚本|脚本方案/;

/** 场景包相关：完成后结论气泡应提供「查看分镜」，打开分镜资产包而非脚本预览。 */
const SCENE_PACKAGE_STORYBOARD_TOOL_NAMES = new Set([
  "prepare_scene_packages",
  "patch_scene",
]);

const SCENE_PACKAGE_STORYBOARD_RESPONSE_RE =
  /视频场景包已就绪|已根据脚本预览|分镜资产包|视频场景包|请打开下方卡片查看|请打开卡片查看|已更新分镜|待重新生成/;

/**
 * 本轮回答已落定，且工具或文案表明脚本可预览时，展示打开右侧脚本预览的入口。
 * 预览默认收起，必须由对话内显式入口打开。
 */
export function turnOffersScriptPreview(turn: NativeAgentTurn): boolean {
  if (!turn.responseCompleted && turn.responseStatus !== "completed") {
    return false;
  }
  if (turnOffersScenePackageStoryboard(turn)) {
    return false;
  }
  const toolsOk = turn.tools.some(
    (tool) => SCRIPT_PREVIEW_TOOL_NAMES.has(tool.toolName) && tool.status === "completed",
  );
  const textOk = SCRIPT_PREVIEW_RESPONSE_RE.test(turn.responseText);
  return toolsOk || textOk;
}

/**
 * 本轮已生成/投影视频场景包时，展示「查看分镜」打开分镜资产包画布。
 */
export function turnOffersScenePackageStoryboard(turn: NativeAgentTurn): boolean {
  if (!turn.responseCompleted && turn.responseStatus !== "completed") {
    return false;
  }
  const toolsOk = turn.tools.some(
    (tool) => SCENE_PACKAGE_STORYBOARD_TOOL_NAMES.has(tool.toolName) && tool.status === "completed",
  );
  const textOk = SCENE_PACKAGE_STORYBOARD_RESPONSE_RE.test(turn.responseText);
  return toolsOk || textOk;
}
