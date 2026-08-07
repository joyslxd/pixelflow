import {
  StoryboardPanel,
  type StoryboardPanelProps,
} from "@/components/canvas/StoryboardPanel";

/** VideoAgent 功能边界统一承接完整分镜编辑器，旧工作台只负责提供兼容数据和动作。 */
export function VideoAgentStoryboardSurface(props: StoryboardPanelProps) {
  return <StoryboardPanel {...props} />;
}
