# Agent 化集成合并日志

> 只有当周集成人更新。当前尚未合并任何实现模块。

## 记录格式

每次合并追加一节，包含：

- sequence / timestamp
- module / source branch / source SHA
- integration target before / after SHA
- dependency SHAs
- latest dev SHA / latest agent SHA used to build candidate
- integration candidate branch and dev-sync ancestor check
- contract/design base SHA（M00）
- M00-A/M00-B 共同祖先与固定合并顺序（仅 M00）；普通模块记录单一模块分支
- `release_id`、`checkpoint_slice`、`checkpoint_commit`、`last_integrated_commit` 和集成增量范围
- `ready_for_phase_integration | ready_for_integration` 触发、单槽 queue/job 和最终 `phase_integrated | phase_integration_blocked | merged | integration_blocked` 状态
- 文件所有权/locked paths 越界检查
- feature flag 状态
- 生产运行模式、`enabled_intents` 或 Feature Flag 变更的人工批准人、时间和目标值；只合代码未发布时明确记录“未发布”
- 测试报告链接和复核人
- 冲突及解决方式
- migration/配置变化
- 合并后 smoke 结果
- 回滚方式或 revert SHA
- 同步的设计/README/AGENTS/content-app 文档

## 记录

暂无。
