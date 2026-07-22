# M08 图片/图片编辑 Workflow Adapter

- phase：`not_started`
- owner：B
- branch：计划 `codex/agent-0.8.4-m08-image-workflow`
- 依赖：M00；真实 job 联调依赖 M06
- 当前切片：M08.1

## 切片

- [ ] M08.1 ImageStage Service 提取与等价测试（2h）
- [ ] M08.2 普通图片子图（2.5h）
- [ ] M08.3 直接编辑/参数/失败恢复（2.5h）
- [ ] M08.4 审核/重生成/60 秒/下载投影（2h）

## 恢复提示

旧 Router 和新 Adapter 共用 Service；Adapter 不 import Router。必须保留 `size` 与 `imageSize` 分离。
