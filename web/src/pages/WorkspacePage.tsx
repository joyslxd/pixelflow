import { AgentWorkspace } from "@/features/agent-workspace/AgentWorkspace";
import { useParams } from "react-router-dom";

export function WorkspacePage() {
  const { conversationId } = useParams();
  return <AgentWorkspace conversationId={conversationId} />;
}
