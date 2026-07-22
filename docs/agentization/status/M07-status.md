# M07 前端 Supervisor 事件 Runtime

- phase：`not_started`
- owner：B
- branch：计划 `codex/agent-0.8.4-m07-web-runtime`
- 依赖：M00
- 当前切片：M07.1

## 切片

- [ ] M07.1 API transport（2h）
- [ ] M07.2 SSE/cursor/gap/reconnect（2.5h）
- [ ] M07.3 reducer 四维状态机（2.5h）
- [ ] M07.4 conversation hook/Abort 隔离（2h）
- [ ] M07.5 legacy snapshot adapter（2h）

## 恢复提示

本模块不改 `WorkspacePage.tsx`。全部使用 fixture/mock server 开发，先证明重复/乱序事件和切换对话安全。
