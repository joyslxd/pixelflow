# Mxx 模块状态

- phase：`not_started | ready | in_progress | blocked | review | ready_for_phase_integration | phase_integration_blocked | phase_integrated | ready_for_integration | integration_blocked | merged | canary | done`
- owner：
- backup/reviewer：
- base SHA：
- module/integration branch：
- contract freeze SHA（非合同模块填 `—`）：
- 当前切片：
- 开始时间：
- 最后更新时间：
- feature flag 状态：
- release_id：`— | R1 | R2 | R3 | R4`
- checkpoint_slice：
- checkpoint_commit：
- last_integrated_commit：
- checkpoint_status：`— | pending | ready | integrated | blocked`
- 依赖模块/SHA：
- latest dev SHA included：
- latest agent SHA at module start：
- automation state：`design_only | automation_local_ready | automation_active`
- 文件锁：
- 当前唯一模块分支写入者：
- integration queue/job：
- integration failure evidence：

## 切片进度

- [ ] Mxx.1
- [ ] Mxx.2
- [ ] Mxx.3
- [ ] Mxx.4

## 已修改文件

暂无。

## 已完成验证

暂无。每条记录命令、exit code、通过/失败/跳过数量、是否 mock、耗时。

## 决策与原因

暂无。

## 已知问题/阻塞

暂无。

## 交接信息

- 工作区是否干净：
- 未提交或半成品：
- 下一步第一条命令：
- 下一步预期失败测试：
- 尚未运行的测试及原因：
- 回滚注意事项：

禁止写入 Authorization、token、供应商 key、完整带查询参数 URL 或用户原始长 prompt。

同一模块所有切片必须串行并复用本模块分支/worktree。每个 Codex 任务只执行一个切片；只有 `phased-rollout-plan.md` 列出的中间检查点可写 `ready_for_phase_integration`，最后一个切片写 `ready_for_integration`。两者写入并 push 后 Codex 都必须停止；当前 `automation_local_ready` 时由开发者按执行手册 9.10A 手动启动单槽集成，未来 `automation_active` 时才由远端 CI 触发。`phase_integrated` 不代表模块完成，下一切片仍由开发者手动启动。
