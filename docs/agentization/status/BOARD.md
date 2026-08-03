# PixelFlow Agent 化总进度看板

> 更新时间：2026-08-03
>
> 原始设计基线：`02493711e8c9b74ec5f8e54cfadac3881297754c`；M00-A/M00-B 共同 Agent 基线：`8e626ae232d984f14fa9954b672b4e025894d426`。M00-I.1 不固定使用本页旧快照，必须在执行时重新 fetch 四条远端引用。
>
> 当前结论：既有 M13.2 / R2 已进入 Agent，代码状态为 `phase_integrated:R2`；唯一发布负责人于 2026-07-29 批准的生产切换因本机没有受控生产部署入口，在修改生产配置前失败关闭，发布状态为 `release_blocked:R2`。Task 14 视频 live Handler 与 status 402 增量已完成独立复审、合入最新 Agent 业务修复，并通过标准 M13/R2 八项候选门禁；当前只在标准 M13 分支准备新的维护检查点，尚未 push 或执行独立单槽集成。生产继续保持 R1 `assist / [] / 100 / true`，现有阶段工作流继续拥有业务推进权。自动化状态仍为 `automation_local_ready`；未执行生产迁移、真实付费 Provider、R2 发布、M13.3 或 Agent→dev 合并。

## 上线里程碑

| 批次 | 目标日 | 用户可见成果 | 当前状态 | 代码检查点 | 生产状态 |
| --- | ---: | --- | --- | --- | --- |
| R1 | D4 | 自动上下文压缩开始/完成提示、输入排队、刷新恢复、原任务继续 | `released` | M00、M01、M03、M04、M07、M12.3、M13.1 已进入 Agent；生产配置提交 `38a782b` | 已人工发布 `assist+100%`；负责人确认启动日志正常，未报告红线异常 |
| R2 | D9 | 视频会话 Agent：继续、修改、重生、重试、新建、切换、取消、追问 | `release_blocked` | M02、M05、M06、M11、M12.5、M13.2 已进入 Agent | 已获批准但缺少受控生产部署入口；修改配置前停止，保持 R1 |
| R3 | D13 | 图片/编辑、PPT、视频分析接入同一会话 Agent | `planned` | 待 M08/M09/M10/M13.3 | 未发布；目标 `primary(四类intent)+100%` |
| R4 | D16–D18 | 五流程全量门禁、回滚、新对话全面接管验收 | `planned` | 待全部模块和 M13.4–M13.5 | 未发布；保持R3范围100% |

| 模块 | 名称 | Owner | 当前状态 | 已完成切片 | 阻塞/前置 | 合并 SHA |
| --- | --- | --- | --- | ---: | --- | --- |
| M00 | 合同、分支自动化、中文工程门禁、feature flag、测试入口 | A+B | `merged` | 5/5 | 无；门禁基线已修复；`automation_local_ready` | `9b7a292`（验收实现）；`1aba4ae` + `4514ffe`（基线修复与审核加固） |
| M01 | 持久化、CAS、Inbox/Outbox | A | `merged` | 5/5 | 无；已进入 Agent | `337a191` |
| M02 | LangGraph 会话/Workflow 内核 | A | `merged` | 4/4 | 无；已进入 Agent | `e77bdcd` |
| M03 | 模型档案、预算、ContextEnvelope | A | `merged` | 4/4 | 无；已进入 Agent | `e43b5e9` |
| M04 | 全局上下文压缩 | A | `merged` | 5/5 | 无；已进入 Agent | `7e4f4c3` |
| M05 | Supervisor 决策与目标解析 | A | `merged` | 5/5 | 无；已进入 Agent | `2c0c0bc` |
| M06 | 持久化 External Job Coordinator | A | `merged` | 5/5 | 无；已进入 Agent | `e8ed2be` |
| M07 | 前端 Supervisor 事件 Runtime | B | `merged` | 5/5 | M00 | `a5a7b75` |
| M08 | 图片/图片编辑 Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M09 | PPT Adapter | B | `not_started` | 0/4 | M00；联调 M06 | — |
| M10 | 视频分析 Adapter | B | `not_started` | 0/4 | M00；联调 M03/M06 | — |
| M11 | 视频生成 Adapter | B | `merged` | 5/5 | 无；已进入 Agent，R2 真实联调可使用 M06 | `5ed26af` |
| M12 | 交互 UI 与 Legacy 迁移 | B | `merged` | 5/5 | 无；已进入 Agent | `e71cd8e` |
| M13 | R1–R4 增量 E2E、Shadow、全量发布、回滚 | A+B | `phase_integrated` | 2/5 | R1 已发布生产；既有 R2 已进入 Agent且生产发布失败关闭；Task 14 增量已通过候选门禁，正在准备标准 M13.2 维护检查点；R3–R4 尚未执行 | `95ef865` |

## 当前文件所有权

M00-A、M00-B 和 M00-I.1 写锁均已释放。M00-I.1 使用唯一新候选 `codex/integrate-m00-20260724-0043` 完成，没有复用上一条 blocked 候选。门禁基线修复使用独立候选 `codex/integrate-m00-gate-repair-20260724-164428` 和全局单槽锁，推送确认后释放。M03 使用全新候选 `codex/integrate-m03-20260724-101526-afe4c4f6` 完成最终集成。M01 首轮候选因权威清单未固化而安全阻塞；修复后使用全新候选 `codex/integrate-m01-20260724-114004-b292f538` 完成最终集成，没有复用原阻塞候选。M04 使用全新候选 `codex/integrate-m04-20260725-011234-0f2661e4` 完成最终集成；集成前仅以状态提交规范 checkpoint 元数据，没有改写 M04.5 业务实现。M02 使用全新候选 `codex/integrate-m02-20260727-224341-c8add0a7` 完成最终集成。M05 首次执行在候选创建前因状态占位值不规范而停止，第二条候选 `codex/integrate-m05-20260728-053559-3206adb1` 因本地临时门禁 wrapper 的 PowerShell 5.1 解析错误安全阻塞；修复入口并在保留候选复跑 Final 后，使用全新候选 `codex/integrate-m05-20260728-054138-49267ca5` 完成最终集成，没有复用 blocked 候选。M11 首次候选 `codex/integrate-m11-20260728-102519-7a52afec` 因缺少全新 worktree 的前端本地依赖而安全阻塞；补齐依赖并在保留候选复跑 Final 绿色后，只恢复模块入口，再使用全新候选 `codex/integrate-m11-20260728-110448-578e18ae` 完成最终集成，没有复用 blocked 候选。M06 首次候选 `codex/integrate-m06-20260728-112612-f2a7b3d2` 因 M11/M06 同时修改 `AGENTS.md` 和 `README.md` 的共享能力表而安全阻塞；在原模块分支纳入最新 Agent、保留两条能力说明并重复执行 Final 绿色后，使用全新候选 `codex/integrate-m06-20260728-121304-1f633dea` 完成最终集成，没有复用 blocked 候选。M12 首次候选 `codex/integrate-m12-20260729-004147-406e3815` 因 `WorkspacePage.tsx` 六处语义冲突安全阻塞；模块分支合入精确 Agent 基线、组合保留 R1 接力与 M12 Supervisor 投影并重复执行 Final 绿色后，使用全新候选 `codex/integrate-m12-20260729-011456-8d59d974` 完成最终集成，没有复用 blocked 候选。M13.2 使用全新候选 `codex/integrate-r2-m13-20260729-050341-ecd2fc89` 完成 R2 阶段集成，12 项权威非付费门禁全绿，三条冻结远端引用无漂移后原子更新，随后释放全局单槽锁；没有复用历史阻塞候选。M01/M02/M03/M04/M05/M06/M11/M12/M13.2 全局单槽锁和模块写锁均已释放；根工作区及原模块 worktree 中既有用户文件未被删除或纳入提交。

## 下一步

1. 既有 M13.2 / R2 检查点不得重复集成；Task 14 是 `last_integrated_commit` 之后的新维护增量，只能形成新的 M13.2 检查点并通过全新单槽候选，不能自动启动 M13.3。本次 R2 生产批准因缺少受控部署入口失败关闭，生产继续保持 R1；后续生产发布必须先补齐部署、重启、日志/指标和回滚入口，再由唯一发布负责人重新明确批准。
2. 当前自动化状态为 `automation_local_ready`。模块开工、阶段/最终集成和 dev→agent 漂移检查均人工触发仓库脚本；只有未来实际部署并验收远端 CI 后才能提升为 `automation_active`。
3. R1 已按运行手册 9.17 完成人工发布并保持 `assist+100%`；R2 既有代码已进入最新 Agent，但本次阻塞记录不授权继续发布、`primary(video)`、真实付费供应商测试、M13.3 或 Agent→dev 合并。
4. Task 14 已完成本地整改、独立复审和标准 M13/R2 八项候选门禁，Runtime 公开导出基线失败也已按 TDD 关闭。当前只允许准备本地标准 M13.2 维护检查点；push 和独立单槽集成仍按后续明确指令执行。

总看板在合法阶段检查点或最终模块通过闸门并由单槽候选合入 `feature/agent_0.8.4_boguan` 后更新；当前单槽候选由开发者人工触发。`phase_integrated` 只表示该批次增量已进入 Agent，不表示模块完成。模块分支内的逐切片实时进度写对应模块状态文件。
