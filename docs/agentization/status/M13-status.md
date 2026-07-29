# M13 集成、Shadow、全量发布、回滚与交付

- phase：`phase_integrated`
- owner：A+B；当周单一集成人
- branch：`codex/agent-0.8.4-m13-integration`
- 依赖：按 R1–R4 增量满足；最终收口依赖 M01–M12
- 当前切片：`M13.3`
- base Agent SHA：`2b7bd44813dbbe63836e8fd2434c0b9be08af404`
- 当前唯一写入者：`尚未领取`
- 开始时间：`2026-07-25 13:38:00 +08:00`
- M13.2 已释放文件：R2 Runtime/Graph/创建路由、dev 配置、前端接力、定向测试、AGENTS/README/最新设计、状态和测试报告全部解除写锁
- release_id：`R2`
- checkpoint_slice：`M13.2`
- checkpoint_commit：`d2a5970fa2c61ab7974451b38cc3bd8fbefa6b56`
- last_integrated_commit：`95ef865f2a084ce57b91be5eb326e1045247d4a0`
- checkpoint_status：`phase_integrated:R2`
- 当前发布门禁：`released:R1 / phase_integrated:R2 / awaiting_release_approval:R2`；R2 代码已进入 Agent，但生产继续保持 R1，只有唯一发布负责人另行明确批准后才允许发布 `primary(video)`
- 生产配置：`assist / [] / 100 / true`；只影响新对话，历史对话和运行中任务不迁移

## 切片

- [x] M13.1 / R1 assist、压缩 UI/恢复、旧流程等价、全部新对话100%（2.5h）
- [x] M13.2 / R2 视频 replay/shadow/黄金对话/mock E2E、`primary(video)+100%`（3h）
- [ ] M13.3 / R3 图片/编辑、PPT、视频分析 mock E2E、`primary(四类intent)+100%`（3h）
- [ ] M13.4 / R4 五流程全量、保持100%、kill switch/回滚（2.5h）
- [ ] M13.5 / R4 经批准真实冒烟/文档/发布签字（2h）

## 启动与发布规则

- M13.1→M13.5 在同一分支/worktree 严格串行，每个切片都由开发者手动启动一次；直接复制[执行手册第9节](../branch-and-codex-runbook.md#codex-prompts)对应话术。
- M13.1 最早在 M00-I.1，以及 M01/M03/M04/M07/M12.3 的 R1 增量进入 Agent、最新 dev→agent 绿色后启动。
- M13.1–M13.4 默认只生成候选和执行非付费门禁，切片通过后写 `ready_for_phase_integration:R*` 并停止；当前由开发者按执行手册人工启动单槽集成，候选绿色进入 Agent 后写 `phase_integrated:R*` 和 `awaiting_release_approval:R*`，但不自动修改生产运行模式、`enabled_intents` 或 Feature Flag。当前各阶段比例固定100%，无用户白名单。
- R1 生产上线需要唯一发布负责人另行复制执行手册 9.17 的明确批准话术；以后每个批次和每次比例变化同样单独批准。
- “手动批准”不要求发布负责人亲自编辑配置文件；Codex/受控流水线获批后执行配置、部署、验证、记录和异常回滚。生产平台强制二次认证或人工审批按钮除外。

## 发布记录

| 批次 | 候选状态 | 人工批准 | 生产值/比例 | 发布证据 |
| --- | --- | --- | --- | --- |
| R1 | `released:R1` | 已批准（2026-07-27） | `assist / [] / 100 / true` | 生产配置提交 `38a782b`；发布负责人确认上传、重启和启动日志正常；详见 [R1 生产发布记录](../test-reports/M13.1-R1-production-release.md) |
| R2 | `awaiting_release_approval:R2` | 未批准 | 保持 R1 `assist / [] / 100 / true` | 全新单槽候选 `codex/integrate-r2-m13-20260729-050341-ecd2fc89` 已通过并进入 Agent；等待独立生产发布批准 |
| R3 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R4 | `not_eligible` | 未批准 | 保持发布前原值 | — |

## 恢复提示

Shadow 不能调用付费 API，也不能写 PowerMem 经验。回滚只影响新对话；运行中的 Supervisor 对话继续排空或人工处理，不能强切 owner。

## M13.2 / R2 单槽阶段集成（2026-07-29）

- 冻结引用：Agent `2b7bd44813dbbe63836e8fd2434c0b9be08af404`、dev `fb7450775a227d891372c19eae1b308045c51e68`、M13 状态提交 `95ef865f2a084ce57b91be5eb326e1045247d4a0`；实现检查点固定为 `d2a5970fa2c61ab7974451b38cc3bd8fbefa6b56`。
- 前置依赖：M13.1、M02、M05、M06、M11、M12.4–M12.5 均已是冻结 Agent 祖先，冻结 dev 也已进入 Agent；集成前没有其他任务占用单槽锁。
- 唯一候选：`codex/integrate-r2-m13-20260729-050341-ecd2fc89`。本次调用 `Integrate-AgentModule.ps1` 的参数固定为 `M13 / Phase / R2 / M13.2 / codex/agent-0.8.4-m13-integration`，没有复用历史阻塞候选。
- 权威门禁：12 项非付费命令全部绿色，覆盖中文工程规范、Python 3.12、R2 Graph 定向、后端可运行全量、Ruff、Web Agent 合同/全量/lint/build、生产配置隔离、既有 Docker 退役基线隔离和上下文常量审计。后端全量仅排除冻结 Agent 中已存在、依赖已退役 `scripts/docker.sh` 的 6 个基线用例，并额外证明 R2 没有修改该测试或恢复脚本。
- 上下文合同：候选继续统一使用 `effective_context_k=896`、`output_reserve_k=32`、`safety_reserve_k=32`、DeepSeek V4 Pro `max_context_tokens=1000000`、`require_verified_model_profile=true` 和 `compaction_retry_backoff_seconds=30`；未增加视频节点窗口常量或 128K 业务兜底。
- 原子结果：脚本先把远端 Agent 从 `2b7bd44813dbbe63836e8fd2434c0b9be08af404` 推进到 `4baa22193e661a570fecbecf21a5e9b3750c5162`，并把模块状态推进到 `994efd77bc2f03285d710ce7ab7ee6939ec05a96`；完整中文交接记录将以相同候选再次防漂移快进，最终值以远端复读为准。
- 安全边界：未修改 `backend/config.prod.yml`，未调用真实图片、视频、PPT、剪映、LLM、content-app 或 PowerMem 付费接口，未执行 M13.3，未发布 `primary(video)`，自动化状态保持 `automation_local_ready`。
- 停止点：R2 代码阶段状态为 `phase_integrated:R2`，发布状态为 `awaiting_release_approval:R2`；本任务完成远端 SHA 核对后立即停止，等待独立的 R2 生产发布批准。

## M13.2 / R2 实现检查点记录（2026-07-29）

- 依赖：M13.1 已完成集成与人工生产发布；M02、M05、M06、M11、M12.4–M12.5 均为 base Agent `2b7bd448` 的祖先，最新 dev 已进入该 Agent 基线。生产继续保持 R1 `assist / [] / 100 / true`。
- 测试候选：dev profile 精确为 `primary / [video] / 100 / true`。连续 32 个明确视频新对话全部冻结为 `supervisor_v1`；图片和无法唯一判断的首轮输入保持 `frontend_v2`，但继续挂载 R1 Turn、Snapshot/SSE、压缩和恢复。
- 回放边界：新增 `SupervisorReplayRuntime`，`off/assist` 在 Handler 前关闭，Shadow 只生成冻结决策、标准命令 DTO 和预算报告，不进入 Workflow Handler、Operation 或 PowerMem record；primary 才调用 M02/M05 图内核。
- 视频命令：`WorkflowCommand` 增加 user、Turn、当前输入、materials、reply 和 Artifact 引用；视频 Handler 必填并深拷贝附件，尚未进入 R2 的其他 Workflow 保持 M02 路由内核兼容。
- mock E2E：以 M11 `VideoPlanningWorkflowService` 作为 Handler、M06 `OperationStartCoordinator` 与固定 Provider fake 串起视频首轮；刷新/协调器重建复用同一 operation 和 provider job，供应商 start 增量为 0。
- 黄金对话：R2 视频子集 13 条覆盖全部 9 类 `AgentAction`，action、target 和追问召回均为 100%，计费误执行为 0。
- 上下文合同：回放只通过共享 `ContextBudgetPolicyProvider` 读取 896K/32K/32K，DeepSeek V4 Pro 档案固定为 1,000,000 tokens 且缺失已验证档案失败关闭；压缩期 Turn 排队、30 秒失败退避、同 Turn 与附件恢复均有定向测试。未增加视频节点窗口常量或 128K 业务兜底。
- 非付费边界：所有测试只使用 Memory Store、fixture 和 fake；未调用真实图片、视频、PPT、剪映、LLM、content-app、PowerMem 或其他付费 API。未修改 `backend/config.prod.yml`，未执行 M13.3。
- 测试与审核：完整证据见 [M13.2 / R2 测试与审核记录](../test-reports/M13.2-R2.md)。实现提交完成并复核中文规范后，只允许再修改本状态文件登记 R2 检查点。
- 自动化：保持 `automation_local_ready`。当前 macOS 没有 PowerShell；通用 M13 命令已逐项等价执行。后端全量除仓库既有的 6 个 `scripts/docker.sh` 缺失用例外为 `5057 passed, 19 skipped`，该同一基线问题已记录在 M13.1；R2 定向、Ruff、Web 合同/全量/lint/build 均绿色。

## M13.1 / R1 生产发布（2026-07-27）

- 发布状态：`released:R1`；生产目标值为 `assist / [] / 100 / true`，预算、严格模型档案和 30 秒失败退避保持不变。
- 人工步骤：唯一发布负责人确认已上传 `38a782b` 对应发布包并重启，启动日志正常且未报告红线异常；未提供截图。
- Codex 可复核边界：未认证访问生产 `/agent/health` 到达后端认证边界并返回 JSON `401`，证明路由可达和认证仍生效；由于没有生产 Authorization，本记录不把该探测写成已认证的新对话功能 smoke。
- 回滚：保留 `off / [] / 0 / false` 回滚包及 SHA-256；异常时只停止新对话进入 Runtime，不迁移历史对话或强切运行中任务。
- 后续边界：M02.1 的依赖已满足，可由独立任务启动；本次发布不授权 M13.2/R2、`primary`、真实付费供应商测试或 Agent→dev 合并。
- 详细证据：[M13.1 / R1 生产发布记录](../test-reports/M13.1-R1-production-release.md)。

## M13.1 实现检查点记录

- 依赖：M00-I.1、M01、M03、M04、M07 和 M12.3/R1 均已进入 base Agent `f03f733`；最新 dev `fb745077` 是该 Agent 的祖先，dev→Agent 预检为 `up_to_date`。
- 测试候选：`backend/config.dev.yml` 为 `assist / [] / 100 / true`，进程内 Gateway 连续 32 个新对话全部冻结为 assist；生产配置未修改，默认仍为 `off / [] / 0 / false`。
- 实现：完成 R1 Turn 原子登记、migration/OpenAPI、60% 外置、75%/85% 摘要、持久化队列/租约/Snapshot、旧流程接力、queued 可见性和同会话 pending 写入串行化。
- 回归：真实 message job 不能越序接力 queued Turn；历史非法 marker 由 Snapshot 安全清理且不改变队列顺序。
- 审核：独立 Reviewer 最终 Critical/Important/Minor 均为 0，`Ready to merge: Yes`。
- 检查点：原业务实现与中文规范修复固定在 `e4eb45838d20bf110841aa360f24d699b32ead3d`；初版门禁修复 `93169c7fd1e2b4a771830fdd71b393519f5101b8` 已被独立审查修正，当前权威可重试检查点为 `c86d181787dfca875cd8f267b709859fc82efb28`。
- 最终阶段门禁：全新候选 `codex/integrate-r1-m13-20260725-113904-ddf38e34` 通过 `scripts/agentization/Invoke-M13R1PhaseGate.ps1` 完整执行八项 `M13 / Phase / R1 / M13.1` 非付费权威门禁；远端 Agent、dev 与模块基线复核无漂移后已原子更新。
- 历史停止点：M13.1 单槽集成完成时为 `phase_integrated:R1`、`awaiting_release_approval:R1`；该状态已由 2026-07-27 的独立 R1 生产批准和人工发布解除。
- 详细证据：[M13.1-R1 测试与审核记录](../test-reports/M13.1-R1.md)。
- 门禁入口修复证据：[M13.1-R1 门禁入口修复记录](../test-reports/M13-R1-gate-repair.md)。
- locked files：`无`
- integration failure evidence：`无`

## M13.1 / R1 统一预算与压缩恢复修复（2026-07-26）

- 修复验收时状态：`implementation_local_verified_chrome_passed`；代码、四类 intent 自动化、本地真实 Runtime、后端/前端可运行门禁、原设计文档和 Mac Chrome 可见验收均已完成。真实图片视频流程在首次 plan.md 轮询超时后通过同一入口受控重试成功，停在人工审核且未进入付费生成。本节记录发布前的历史检查点；生产值已在 2026-07-27 的独立 R1 发布中切换为 `assist / [] / 100 / true`。
- 已确认合同：DeepSeek V4 Pro 物理窗口 `1,000,000 tokens`；所有当前和未来 Agent/节点统一从 profile 读取 `896K` 有效窗口、`32K` 输出预留和 `32K` 安全预留，`K=1024 tokens`，可用输入 `851,968 tokens`。
- 严格边界：dev/prod 都保存相同预算结构和模型档案；实际 Runtime 使用 `require_verified_model_profile=true`，缺失、未验证或过期档案不得走 128K。发布前生产开关关闭，R1 获批后只切换 assist、比例和压缩开关。
- 根因修复：Plan 修订恢复请求只留在权威 Store，不重复进入 Prompt；压缩失败持久化 `retry_not_before=失败时间+30秒`，Snapshot/SSE/Run 到期前不调度，到期后单次恢复。
- R2–R4 继承要求：新增或修改 Agent、节点、Skill 或流程必须复用共享 `ContextBudgetPolicyProvider`，并验证附件完整、自动压缩、压缩期输入排队继续和失败受控重试；不得增加节点级窗口常量。
- 验证结果：Runtime 重点回归最终复跑 `232 passed`；后端可运行全集 `4583 passed, 19 skipped`，另有 6 个与本次无关的 Docker 脚本缺失基线失败；前端 `303 passed`、类型检查和测试构建通过；本地真实 SQLite Runtime 已完成 `560,117 bytes` Artifact 外置、压缩完成事件、当前参考图保留和 Turn 接续。Mac Chrome 进一步完成真实 PNG 上传、视频表单、3 个方向、`600,114 bytes` Artifact 自动压缩、页面排队提示、过期租约接管及 `queued -> processing -> completed`。
- 可见流程停止点：plan.md v1 已生成并停在人工审核；未点击“同意方案”，未进入付费素材/视频生成。macOS 已使用 `git`、`rg`、`python3` 检查本次中文提交、注释和新增配置说明；Windows PowerShell 5.1/Pester 权威总门禁仍只在其兼容环境执行，不能在 Mac 上伪造为绿色。
- 详细证据：[M13.1 / R1 统一上下文预算与压缩恢复修复记录](../test-reports/M13.1-R1-context-budget-repair.md)。
