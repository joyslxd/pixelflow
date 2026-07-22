# PixelFlow Agent 化总进度看板

> 更新时间：2026-07-22
>
> 基线：`02493711e8c9b74ec5f8e54cfadac3881297754c`
>
> 当前结论：设计包已确认“模块之间并行、模块内部切片严格串行”。M00 使用 A/B 两条开发分支并行、各自内部串行，首次集成手动启动一次；M01–M12 普通模块最后一片后由远端单槽流水线自动集成。所有实现模块尚未开工。

| 模块 | 名称 | Owner | 当前状态 | 已完成切片 | 阻塞/前置 | 合并 SHA |
| --- | --- | --- | --- | ---: | --- | --- |
| M00 | 合同、分支自动化、feature flag、测试入口 | A+B | `ready` | 0/5 | 设计评审 | — |
| M01 | 持久化、CAS、Inbox/Outbox | A | `not_started` | 0/5 | M00 | — |
| M02 | LangGraph 会话/Workflow 内核 | A | `not_started` | 0/4 | M00、M01 | — |
| M03 | 模型档案、预算、ContextEnvelope | A | `not_started` | 0/4 | M00 | — |
| M04 | 全局上下文压缩 | A | `not_started` | 0/5 | M01、M03 | — |
| M05 | Supervisor 决策与目标解析 | A | `not_started` | 0/5 | M02–M04 | — |
| M06 | 持久化 External Job Coordinator | A | `not_started` | 0/5 | M01、M02 | — |
| M07 | 前端 Supervisor 事件 Runtime | B | `not_started` | 0/5 | M00 | — |
| M08 | 图片/图片编辑 Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M09 | PPT Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M10 | 视频分析 Adapter | B | `not_started` | 0/4 | M00；联调 M03/M06 | — |
| M11 | 视频生成 Adapter | B | `not_started` | 0/5 | M00；联调 M05/M06 | — |
| M12 | 交互 UI 与 Legacy 迁移 | B | `not_started` | 0/5 | M07 | — |
| M13 | E2E、Shadow、灰度、回滚 | A+B | `not_started` | 0/5 | M01–M12 | — |

## 当前文件所有权

尚未领取任何实现文件。设计文档由本轮设计任务创建；`scripts/__pycache__/` 是设计前已存在的未跟踪用户文件，不属于本项目任务。

## 下一步

1. 确认 A/B 人员对应关系；日常分支固定为 `feature/dev_0.8.4_boguan`，集成分支固定为 `feature/agent_0.8.4_boguan`。
2. 使用 `superpowers:writing-plans` 为 M00 生成逐文件 TDD 实施计划，计划必须写明 M00-A/M00-B 双分支、各线内部串行、共享文件所有权和首次集成引导步骤。
3. 分别用运行手册的 A/B 首次话术同时启动 `M00-A.1`、`M00-B.1`；每个 Codex 任务只执行一个切片，完成后由开发者手动说“继续下一个未完成切片”。
4. A/B 两线完成后手动启动 `M00-I.1`，验证脚本、Gitee/Jenkins 定时任务、普通模块 `ready_for_integration` 自动集成和每日 02:00 漂移检查；实际远端配置通过前状态不得超过 `automation_local_ready`。

总看板只在模块通过闸门并由单槽候选合入 `feature/agent_0.8.4_boguan` 后更新；模块分支内的逐切片实时进度写对应模块状态文件。
