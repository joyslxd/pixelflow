/** 公共 Workspace 查询 Client；浏览器不得手填 workspace_id。 */

import type { VideoWorkspaceProjectionV1 } from "./contracts";
import { agentRequest } from "./http";

export function getOrCreateVideoWorkspace(
  conversationId: string,
): Promise<VideoWorkspaceProjectionV1> {
  /** 打开对话时读取或创建当前会话的视频工作区，只返回公开摘要。 */

  return agentRequest<VideoWorkspaceProjectionV1>(
    `/conversations/${encodeURIComponent(conversationId)}/workspaces/video`,
  );
}
