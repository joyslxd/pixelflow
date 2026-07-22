# M02 LangGraph 会话/Workflow 内核

- phase：`not_started`
- owner：A
- branch：计划 `codex/agent-0.8.4-m02-graph-kernel`
- 依赖：M00、M01
- 当前切片：M02.1

## 切片

- [ ] M02.1 State/reducer/namespace（2h）
- [ ] M02.2 fake registry/dispatcher（2.5h）
- [ ] M02.3 interrupt/resume/projection 顺序（2.5h）
- [ ] M02.4 composition/graph ID/lifespan（2h）

## 恢复提示

不得替换旧 `backend/pixelflow/graph.py` 或旧 graph ID。先基于 fake workflow 证明新 thread 可重启恢复。
