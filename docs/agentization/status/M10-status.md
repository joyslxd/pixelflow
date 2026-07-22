# M10 视频分析 Workflow Adapter

- phase：`not_started`
- owner：B
- branch：计划 `codex/agent-0.8.4-m10-video-analysis`
- 依赖：M00；Context/job 联调依赖 M03/M06
- 当前切片：M10.1

## 切片

- [ ] M10.1 media/storyboard Service 提取（1.5h）
- [ ] M10.2 单/多视频分析子图（2.5h）
- [ ] M10.3 大结果外置和 evidence refs（2h）
- [ ] M10.4 继续/换目标/新流程/失败恢复（2h）

## 恢复提示

完整 storyboard 仍可供用户查看，但 Supervisor 默认只读取摘要和引用，防止每轮上下文膨胀。
