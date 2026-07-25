# M13 集成、Shadow、全量发布、回滚与交付

- phase：`phase_integrated`
- owner：A+B；当周单一集成人
- branch：`codex/agent-0.8.4-m13-integration`
- 依赖：按 R1–R4 增量满足；最终收口依赖 M01–M12
- 当前切片：`M13.2`
- base Agent SHA：`f03f733115fb0ddd554dcb434f368cef5f09b39e`
- 当前唯一写入者：`尚未领取`
- 开始时间：`2026-07-25 13:38:00 +08:00`
- M13.1 已释放文件：本切片实现、migration、测试、配置、AGENTS/README/最新设计、状态和测试报告全部解除写锁
- release_id：`R1`
- checkpoint_slice：`M13.1`
- checkpoint_commit：`c86d181787dfca875cd8f267b709859fc82efb28`
- last_integrated_commit：`328fb535bb2c03790bd1bb189781b9cd64aa1567`
- checkpoint_status：`phase_integrated:R1`
- 当前发布门禁：`awaiting_release_approval:R1`；M13.1 单槽候选已绿色进入 Agent，生产发布仍须唯一发布负责人另行批准
- 生产配置：未变更；切片通过不等于生产上线

## 切片

- [x] M13.1 / R1 assist、压缩 UI/恢复、旧流程等价、全部新对话100%（2.5h）
- [ ] M13.2 / R2 视频 replay/shadow/黄金对话/mock E2E、`primary(video)+100%`（3h）
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
| R1 | `awaiting_release_approval:R1` | 未批准 | 保持发布前原值 | 单槽候选 `codex/integrate-r1-m13-20260725-113904-ddf38e34` 完整执行八项非付费阶段门禁并进入 Agent；模块增量 `328fb53` |
| R2 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R3 | `not_eligible` | 未批准 | 保持发布前原值 | — |
| R4 | `not_eligible` | 未批准 | 保持发布前原值 | — |

## 恢复提示

Shadow 不能调用付费 API，也不能写 PowerMem 经验。回滚只影响新对话；运行中的 Supervisor 对话继续排空或人工处理，不能强切 owner。

## M13.1 实现检查点记录

- 依赖：M00-I.1、M01、M03、M04、M07 和 M12.3/R1 均已进入 base Agent `f03f733`；最新 dev `fb745077` 是该 Agent 的祖先，dev→Agent 预检为 `up_to_date`。
- 测试候选：`backend/config.dev.yml` 为 `assist / [] / 100 / true`，进程内 Gateway 连续 32 个新对话全部冻结为 assist；生产配置未修改，默认仍为 `off / [] / 0 / false`。
- 实现：完成 R1 Turn 原子登记、migration/OpenAPI、60% 外置、75%/85% 摘要、持久化队列/租约/Snapshot、旧流程接力、queued 可见性和同会话 pending 写入串行化。
- 回归：真实 message job 不能越序接力 queued Turn；历史非法 marker 由 Snapshot 安全清理且不改变队列顺序。
- 审核：独立 Reviewer 最终 Critical/Important/Minor 均为 0，`Ready to merge: Yes`。
- 检查点：原业务实现与中文规范修复固定在 `e4eb45838d20bf110841aa360f24d699b32ead3d`；初版门禁修复 `93169c7fd1e2b4a771830fdd71b393519f5101b8` 已被独立审查修正，当前权威可重试检查点为 `c86d181787dfca875cd8f267b709859fc82efb28`。
- 最终阶段门禁：全新候选 `codex/integrate-r1-m13-20260725-113904-ddf38e34` 通过 `scripts/agentization/Invoke-M13R1PhaseGate.ps1` 完整执行八项 `M13 / Phase / R1 / M13.1` 非付费权威门禁；远端 Agent、dev 与模块基线复核无漂移后已原子更新。
- 当前停止点：`phase_integrated:R1`、`awaiting_release_approval:R1`；等待唯一发布负责人另行复制执行手册 9.17 的 R1 生产发布批准话术。不得自动修改生产配置、调用真实付费 API 或执行 M13.2。
- 详细证据：[M13.1-R1 测试与审核记录](../test-reports/M13.1-R1.md)。
- 门禁入口修复证据：[M13.1-R1 门禁入口修复记录](../test-reports/M13-R1-gate-repair.md)。
- locked files：`无`
- integration failure evidence：`无`
