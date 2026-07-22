# M12 交互 UI、双运行时与 Legacy 迁移

- phase：`not_started`
- owner：B
- branch：计划 `codex/agent-0.8.4-m12-workspace-ui`
- 依赖：M07
- 当前切片：M12.1

## 切片

- [ ] M12.1 orchestration mode 双运行时挂载（2h）
- [ ] M12.2 拆分 busy/action policy（2h）
- [ ] M12.3 压缩 Notice/排队 badge（2h）
- [ ] M12.4 reply/artifact/interrupt/mention 元数据（2.5h）
- [ ] M12.5 消息/进度/历史/task board 投影（2.5h）

## 恢复提示

不要同时大拆 `MessageBubble`、GenParamsDialog、StoryboardPanel。先把 `WorkspacePage` 变成 runtime 选择和 ViewModel 挂载点。
