# M06 持久化 External Job Coordinator

- phase：`not_started`
- owner：A
- branch：计划 `codex/agent-0.8.4-m06-external-jobs`
- 依赖：M01、M02
- 当前切片：M06.1

## 切片

- [ ] M06.1 operation 幂等与状态机（2.5h）
- [ ] M06.2 DB lease/heartbeat/接管（3h）
- [ ] M06.3 provider job adapter（2.5h）
- [ ] M06.4 graph resume/终态 claim/crash window（2.5h）
- [ ] M06.5 shutdown/restart/expired 恢复（2h）

## 恢复提示

不能只依赖 checkpoint 保证不重复计费；必须覆盖“供应商已成功、checkpoint 尚未写入时进程崩溃”的窗口。
