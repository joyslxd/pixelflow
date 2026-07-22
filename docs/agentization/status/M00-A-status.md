# M00-A 开发线状态

- phase：`ready`
- owner：A
- branch：计划 `codex/agent-0.8.4-m00-a`
- base Agent SHA：待首次切片执行 dev→agent 预检后填写
- 当前切片：`M00-A.1`
- 当前唯一写入者：尚未领取
- locked files：尚未领取；范围限于后端合同/fixture、`scripts/agentization/**` 及其测试

## 切片进度

- [ ] M00-A.1 characterization tests（2h）
- [ ] M00-A.2 Python DTO/Ports/fakes/规范 fixture（3h）
- [ ] M00-A.3 dev→agent 同步、模块分支/worktree、单槽集成和中文提交/注释/配置说明门禁脚本（3h）

## 最后验证与交接

- 最后完成切片：无
- 测试证据：无
- commit/push：无
- 下一步第一动作：按运行手册 A 首次话术启动 `M00-A.1`
- 硬阻塞：无

每个 Codex 任务只执行一个切片。完成后停止，等待开发者手动发送“继续 M00-A 的下一个未完成切片”。
