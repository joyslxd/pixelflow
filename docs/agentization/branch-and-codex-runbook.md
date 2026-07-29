# PixelFlow Agent 化分支、自动同步与 Codex 执行手册

> 生效日期：2026-07-22。
>
> 核心规则：模块之间可以并行，模块内部所有切片必须串行；每个切片由开发者手动启动一个 1–3 小时 Codex 任务。切片内部的分支恢复、TDD、测试、审核、状态、commit 和 push 自动完成，但 Codex 完成当前切片后必须停止。

## 1. 两个长期分支的权威边界

| 分支 | 权威内容 | 允许方向 |
| --- | --- | --- |
| `feature/dev_0.8.4_boguan` | 日常 Bug、紧急需求、当前 v2 业务行为 | 只向 Agent 同步 |
| `feature/agent_0.8.4_boguan` | 已通过门禁的 Agent 模块，同时包含最新可集成 dev | M13 完成并人工批准后才整体回到 dev |

开发期间只允许 `dev → agent`。禁止自动执行 `agent → dev`，避免循环历史和权威来源不清。

最终收口顺序固定为：

```text
最新 dev → 最新 Agent → M13 全量/Shadow/发布/回滚
→ 人工批准 Agent 整体合回 dev
→ 正常测试/发布分支
```

## 2. dev 自动同步什么时候发生

采用三重触发，方向始终只有 `dev → agent`。

### 2.1 新模块第一个切片开始前

Codex 创建模块分支前执行 `dev-sync preflight`：

1. fetch 远端 dev 和 agent。
2. 判断远端最新 dev 是否已是 Agent 的祖先。
3. 如果不是，创建临时同步候选，将 dev 纳入候选并运行非付费回归。
4. 候选绿色且远端基线未变化后才更新 Agent。
5. 从新的 Agent SHA 创建该模块唯一开发分支和 worktree。
6. 冲突或测试失败时不创建模块分支、不修改 Agent，记录阻塞证据。

同一模块的中间切片只恢复模块分支，不重复同步 dev，避免开发基线持续漂移。

### 2.2 普通模块阶段检查点或最后一个切片完成后

只有[四阶段上线计划](phased-rollout-plan.md)明确列出的中间切片可以成为 `release checkpoint`。该切片通过阶段门禁后写 `ready_for_phase_integration`；模块最后一片通过完整门禁后写 `ready_for_integration`。单槽集成随后构建：

```text
最新 origin/feature/agent_0.8.4_boguan
  + 最新 origin/feature/dev_0.8.4_boguan
  + 模块 checkpoint commit
  = codex/integrate-rX-mXX-YYYYMMDD-HHMM
```

候选必须满足：

- dev-sync guard、模块定向/边界测试和 flag-off 回归全部绿色；
- 候选构建后远端 agent/dev 没有再次前进；
- 只有单槽集成任务可以更新 Agent；未来启用保护分支后也只授予该任务更新权限；
- 绿色时更新 Agent、`status/BOARD.md` 和 `integration/MERGE_LOG.md`；
- 阶段检查点绿色后写 `phase_integrated`，但模块仍按同一分支串行开发后续切片；最终模块绿色后写 `merged`；
- 冲突或测试失败时 Agent 保持不变，中间检查点写 `phase_integration_blocked`，最终模块写 `integration_blocked` 并保存安全证据；
- 后续检查点只集成 `last_integrated_commit..checkpoint_commit` 的新增历史，禁止 force-push/rebase 已共享模块分支；
- dev 在测试期间前进时当前候选自动失效并从最新远端重建。

`automation_local_ready` 是当前实际模式：模块状态和 checkpoint commit 推送后停止，由开发者复制 9.10A 话术手动启动单槽集成。只有未来状态提升为 `automation_active` 后，才由远端 CI 自动触发；两种模式执行同一候选构建和 fail-closed 门禁。

### 2.3 dev→agent 漂移检查

`dev-sync reconciliation` 执行以下检查：

- dev 没有领先：成功结束，不做变更；
- dev 领先且可无冲突合并：创建同步候选，运行非付费回归，绿色后进入 Agent 单槽队列；
- 冲突或测试失败：不修改 Agent，记录安全证据并通知；
- 凭据、用户内容、完整 URL 或异常堆栈不得写入通知和日志。

当前没有 Jenkins 或其他远端 CI，状态固定为 `automation_local_ready`：开发者必须在每个模块首次开工、阶段检查点集成和最终模块集成前人工触发该脚本。未来实际配置远端 CI 后，才可增加每天北京时间 02:00 调度并把状态提升为 `automation_active`。Codex 对话结束后不会自行定时唤醒，也不能把人工触发描述成无人值守调度。

### 2.4 M00 首次引导例外

M00 自身负责创建上述本地安全脚本，因此不能依赖当前不存在的远端 CI 完成第一次集成：

1. 开发者分别启动 M00-A、M00-B 的每个短切片；两条线并行，各线内部串行。
2. A/B 全部完成后，开发者手动启动一次 `M00-I.1`。
3. `M00-I.1` 创建临时 `codex/integrate-m00-YYYYMMDD-HHMM`，纳入最新 agent/dev、M00-A、M00-B 并运行 M00 跨端合同、flag-off、Pester、Web 聚合、构建、中文工程规范和本地分支自动化门禁。
4. 本地候选全部绿色且远端基线未变化后即可更新 Agent，把状态写为 `automation_local_ready` 并完成 M00；缺少 Jenkins/Gitee 流水线不是阻塞项。
5. M00-I.1 不运行 M01–M13 模块门禁；M02 定向集合由 M02 执行，后端仓库全量只由 M13 执行。

## 3. 本地自动化脚本与可选远端配置

M00 计划交付：

| 计划文件 | 职责 |
| --- | --- |
| `scripts/agentization/Test-AgentBranchPolicy.ps1` | 检查分支、祖先关系、模块写入者、工作区和状态文档 |
| `scripts/agentization/Sync-DevToAgent.ps1` | 创建安全的 dev→agent 同步候选并执行同步测试 |
| `scripts/agentization/Start-AgentModule.ps1` | 同步预检后创建模块分支、worktree、状态记录和远端 tracking；M00 支持 `a/b` lane |
| `scripts/agentization/Integrate-AgentModule.ps1` | 获取单槽锁，校验 module/release/checkpoint 后按最新 Agent + dev + 模块增量构建候选并执行门禁 |
| `scripts/agentization/Invoke-AgentModuleGate.ps1` | 根据模块 ID 和可选 release ID 运行阶段或最终定向、边界、flag-off 和构建测试 |
| `scripts/agentization/Reconcile-DevToAgent.ps1` | dev→agent 漂移检查入口；当前人工触发，未来 `automation_active` 才接入 02:00 调度 |

不再设计 `Start-AgentSlice.ps1`。所有切片顺序复用模块分支/worktree。

未来如果团队部署 Jenkins、Gitee 流水线或其他 CI，可再执行一次性管理员准备：

- 为 Codex/CI 配置最小权限 Gitee 凭据，凭据只放系统凭据库或 CI secret；
- 将 Agent 设置为保护分支/评审模式，禁止普通开发直接 push；
- 配置 Gitee WebHook + Jenkins 或团队可用的 Gitee 流水线；
- 配置普通模块 `ready_for_phase_integration | ready_for_integration` 触发器、阶段检查点白名单、单槽锁和绿色自动合并；
- 配置每天北京时间 02:00 的调度；
- 验证失败时不更新 Agent，日志不泄露 token、Authorization 或业务内容。

当前没有远端流水线，不创建无法运行的 `Jenkinsfile`。逐切片开发、单槽集成和漂移检查全部使用本地脚本并由开发者人工触发；状态保持 `automation_local_ready`，不得描述成“已经无人值守自动化”。未来完成上述管理员配置并实际验收后，才能提升为 `automation_active`。

## 4. 完整分支清单

### 4.1 长期分支

```text
feature/dev_0.8.4_boguan
feature/agent_0.8.4_boguan
```

### 4.2 M00 两条开发分支

| 分支 | 串行任务 | 写入者 |
| --- | --- | --- |
| `codex/agent-0.8.4-m00-a` | `M00-A.1 → M00-A.2 → M00-A.3` | A |
| `codex/agent-0.8.4-m00-b` | `M00-B.1` | B |

两条分支从同一个同步后的 Agent SHA 创建，以已评审 `contracts-v1.md` 为共同设计基线。A/B 不得修改对方锁定路径。M00 首次集成只使用一次性 `codex/integrate-m00-YYYYMMDD-HHMM`，不保留第三条长期开发分支。

### 4.3 M01–M13 模块分支

模块分支名称提前冻结，但轮到模块开工时才从同步后的最新 Agent 创建。

| 模块 | 分支名称 |
| --- | --- |
| M01 | `codex/agent-0.8.4-m01-runtime-store` |
| M02 | `codex/agent-0.8.4-m02-graph-kernel` |
| M03 | `codex/agent-0.8.4-m03-context-runtime` |
| M04 | `codex/agent-0.8.4-m04-context-compaction` |
| M05 | `codex/agent-0.8.4-m05-supervisor` |
| M06 | `codex/agent-0.8.4-m06-external-jobs` |
| M07 | `codex/agent-0.8.4-m07-web-runtime` |
| M08 | `codex/agent-0.8.4-m08-image-workflow` |
| M09 | `codex/agent-0.8.4-m09-ppt-workflow` |
| M10 | `codex/agent-0.8.4-m10-video-analysis` |
| M11 | `codex/agent-0.8.4-m11-video-workflow` |
| M12 | `codex/agent-0.8.4-m12-workspace-ui` |
| M13 | `codex/agent-0.8.4-m13-integration` |

禁止创建 `mXX-sYY-*` 切片分支。每个模块只有一个开发分支和一个 worktree。

### 4.4 临时同步/集成候选

```text
codex/sync-dev-to-agent-YYYYMMDD-HHMM
codex/integrate-mXX-YYYYMMDD-HHMM
codex/integrate-rX-mXX-YYYYMMDD-HHMM
```

候选完成或关闭后删除，不得在临时候选上继续堆业务代码。

## 5. 模块并行和依赖波次

模块之间允许并行，前提是依赖已满足且锁定文件不重叠；模块内部禁止并行。

| 批次 | A 线可并行模块 | B 线可并行模块 | 条件 |
| --- | --- | --- | --- |
| R1 / D1–D4 | M00-A、M01、M03、M04 | M00-B、M07、M12.1–M12.3 | M00-I.1 后交付 assist/自动压缩可感知版 |
| R2 / D5–D9 | M02、M05、M06 | M11、M12.4–M12.5 | 视频 Supervisor/Workflow Agent MVP |
| R3 / D10–D13 | 平台稳定化、跨 workflow 缺陷 | M08、M09、M10 | 图片/编辑、PPT、视频分析接入 |
| R4 / D14–D18 | M13 全量、并发、回滚 | M13 前端恢复、真实流程 | 保持100%新对话，生产模式/intent变化人工批准 |

同一个人建议同时保持 2–3 个写入型模块任务，避免本地资源和审查负担过高。例如 M00 后：

```text
A：M01 与 M03 两个 Codex 任务并行；每个模块内逐切片串行
B：M07、M08、M09 三个 Codex 任务并行；每个模块内逐切片串行
```

## 6. 模块内部串行规则

```text
M01.1 → 测试/审核/状态/commit/push → Codex 停止
开发者手动说“继续 M01”
M01.2 → 测试/审核/状态/commit/push → Codex 停止
……
明确阶段检查点 → 阶段门禁 → ready_for_phase_integration → Codex 停止
开发者复制 9.10A 话术手动触发单槽增量集成；后续切片仍由开发者手动启动
最后一片 → 模块门禁 → ready_for_integration → Codex 停止
开发者复制 9.10A 话术手动触发单槽最终集成
```

强制约束：

- 每个 Codex 任务只处理一个 1–3 小时切片；
- 当前切片完成后不得自行进入下一片，不得采用模块级托管长任务；
- 后续切片恢复同一个模块分支/worktree，以上一切片远端 commit 为基线；
- 同一模块同一时刻只有一个写入者；另一个对话只能只读审核；
- 不创建切片分支、切片 worktree或切片状态合并流程；
- 开发者可以同时启动不同模块，但运行手册/状态文件必须证明依赖和文件所有权不冲突。

## 7. Codex 自动执行一个切片的标准流程

1. 读取根目录 `AGENTS.md`、设计、合同、运行手册、测试矩阵、总看板和目标模块状态。
2. 检测是否已在正确的模块 worktree；检查当前分支、远端 tracking 和工作区。
3. 保留并避让用户未提交修改，不得删除、reset 或覆盖。
4. 如果是模块第一片，执行 dev→agent 预检并创建模块分支/worktree；否则恢复现有模块分支/worktree。
5. 验证上一切片已 commit/push，且没有其他 Codex 正在写本模块。
6. 更新模块状态：当前切片、base SHA、branch、writer、locked files 和开始时间。
7. 使用 TDD：失败测试 → 验证失败 → 最小实现 → 验证通过。
8. 运行定向、边界、lint/构建、`git diff --check` 和必要的 flag-off 回归。
9. 启动独立只读审核；处理有效意见后重新验证。
10. 执行中文工程规范检查：新增/修改解释性注释必须使用中文；每个新增/修改配置键必须有逐项中文说明；机器指令例外必须命中最小白名单并由 reviewer确认。
11. 使用中文更新状态/测试报告/交接信息，只暂存当前切片文件；commit 标题和正文必须以中文表达本切片目的与验证结果。
12. 中文规范和全部切片门禁通过后，创建当前切片独立 commit 并 push 模块分支；不合规不得 push。
13. 如果当前切片不是检查点也不是最后一片，记录下一片第一动作并停止；等待开发者手动继续。
14. 如果当前切片是阶段计划明确列出的检查点，运行阶段门禁，绿色时写 `ready_for_phase_integration` 并 push；如果是最后一片，运行完整模块门禁，绿色时写 `ready_for_integration` 并 push。随后停止；当前 `automation_local_ready` 时提示开发者复制 9.10A 话术人工启动单槽集成，未来 `automation_active` 时才由远端 CI 接管。不得自动进入下一片。
15. 失败或硬阻塞不得声称完成，也不得直接修改长期 feature 分支。

Codex 只有以下情况才停下询问，而不是自行扩大范围：

- 发现冻结合同缺口或需要改变用户已批准设计；
- 同模块存在另一写入者，或工作区用户修改会被覆盖；
- 基线测试失败且原因不属于当前切片；
- 需要真实付费 API、生产凭据、Gitee 管理员权限或 Agent→dev 最终批准；
- 合并冲突不能依据现有合同安全处理；
- 推送或单槽集成任务因外部权限、远端状态失败。

## 8. 自动审核的定义

每个切片至少经过：

1. 实现 Codex 自审 diff；
2. 独立只读 reviewer 检查合同、恢复、幂等、安全、测试和越权修改；
3. 实现 Codex处理有效意见并说明拒绝不合理意见的证据；
4. 重新运行全部切片门禁；
5. 状态文件记录 reviewer、发现、处理和最终验证。

审核不等于只看测试绿色。模块最后一片和远端集成候选还要分别运行模块级与跨模块门禁。

<a id="codex-prompts"></a>

## 9. Codex 唯一权威 A/B 启动话术

本节是整个 Agent 化改造中**唯一允许直接复制给 Codex 的话术来源**。`architecture-design.md`、`work-breakdown.md`、README 和状态文件只能链接到本节，不得复制另一套话术。以下每条都可独立用于新 Codex 对话；每次只执行一个切片。

以下全部话术自动继承根目录 `AGENTS.md` 和本手册第7、11节的中文工程硬性规范：commit/合并/状态/测试/交接使用中文，新增或修改的解释性代码注释使用中文，新增或修改的配置逐项提供详细中文说明。Codex 必须在 commit/push 前自动检查；不合规时停止，不得 push 或标记完成。用户复制话术时不需要重复追加这段要求。

### 9.1 A 首次启动 M00-A

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md、docs/agentization/README.md、docs/agentization/architecture-design.md、docs/agentization/phased-rollout-plan.md、docs/agentization/contracts-v1.md、docs/agentization/work-breakdown.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/test-matrix.md、docs/agentization/status/BOARD.md、docs/agentization/status/M00-status.md 和 docs/agentization/status/M00-A-status.md。

你是 A 开发线 Codex。请全自动执行 M00-A 的第一个未完成切片。先执行 dev→agent 安全预检，从同步后的同一 Agent 基线创建或恢复 codex/agent-0.8.4-m00-a 及独立 worktree。M00-A 内部必须严格串行，本次只完成一个切片；完成 TDD、定向/边界测试、独立审核、状态/测试文档、独立 commit 和 push 后立即停止，并报告下一切片。不得自动继续整个 M00-A，不得修改 M00-B 路径，不得直接修改两个长期 feature 分支，不得调用真实付费 API；只有运行手册定义的硬阻塞才询问。
```

### 9.2 A 新对话继续 M00-A

```text
不要依赖任何旧对话内容。先阅读仓库根目录 AGENTS.md、全部 docs/agentization 开发入口文档（包括 phased-rollout-plan.md）、docs/agentization/status/M00-A-status.md 和最近测试记录。你是 A 开发线 Codex。请检查远端 codex/agent-0.8.4-m00-a、最近 commit、worktree、用户改动、上一切片证据和当前唯一写入者；确认安全后只执行 M00-A 的下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得重复已完成工作，不得自动进入再下一片，不得修改 M00-B 或两个长期 feature 分支。完成后报告下一切片；只有硬阻塞才询问。
```

### 9.3 B 首次启动 M00-B

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md、docs/agentization/README.md、docs/agentization/architecture-design.md、docs/agentization/phased-rollout-plan.md、docs/agentization/contracts-v1.md、docs/agentization/work-breakdown.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/test-matrix.md、docs/agentization/status/BOARD.md、docs/agentization/status/M00-status.md 和 docs/agentization/status/M00-B-status.md。

你是 B 开发线 Codex。请全自动执行 M00-B 的第一个未完成切片。先执行 dev→agent 安全预检，从与 M00-A 相同的同步后 Agent 基线创建或恢复 codex/agent-0.8.4-m00-b 及独立 worktree。本次只完成一个切片；严格按照 contracts-v1.md 实现 TypeScript 镜像合同和前端测试入口，不得修改 Python 权威 DTO/fixture。完成 TDD、测试、独立审核、状态/测试文档、独立 commit 和 push 后停止。不得修改 M00-A 或两个长期 feature 分支，不得调用真实付费 API；只有硬阻塞才询问。
```

### 9.4 B 新对话继续 M00-B

```text
不要依赖任何旧对话内容。先阅读仓库根目录 AGENTS.md、全部 docs/agentization 开发入口文档（包括 phased-rollout-plan.md）、docs/agentization/status/M00-B-status.md 和最近测试记录。你是 B 开发线 Codex。请检查远端 codex/agent-0.8.4-m00-b、最近 commit、worktree、用户改动、上一切片证据和当前唯一写入者；确认安全后只执行 M00-B 的下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得修改 M00-A/Python 权威 fixture 或两个长期 feature 分支。完成后报告结果；只有硬阻塞才询问。
```

### 9.5 手动启动首次 M00 集成

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md、docs/agentization/README.md、docs/agentization/architecture-design.md、docs/agentization/phased-rollout-plan.md、docs/agentization/contracts-v1.md、docs/agentization/work-breakdown.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/test-matrix.md、docs/agentization/status/BOARD.md、docs/agentization/status/M00-status.md、docs/agentization/status/M00-A-status.md、docs/agentization/status/M00-B-status.md、docs/agentization/integration/DECISIONS.md 和 docs/agentization/integration/MERGE_LOG.md；如本次任务附带上一轮 M00-I.1 阻塞报告，可将其作为历史证据读取，但不得把报告中的旧验收边界覆盖 D-008。请全自动重新执行 M00-I.1。

先 fetch 并固定最新 origin/feature/agent_0.8.4_boguan、origin/feature/dev_0.8.4_boguan、origin/codex/agent-0.8.4-m00-a、origin/codex/agent-0.8.4-m00-b 四条远端引用；只有 A/B 两线全部完成并已 push 时，才从这四条最新引用创建全新的 codex/integrate-m00-YYYYMMDD-HHMM。不得直接续用或合入上一条 blocked 候选。

固定顺序为“最新 Agent + 最新 dev → M00-A → M00-A定向测试 → M00-B → M00跨端合同/M00范围全量/flag-off/本地自动化门禁”。M00范围只包括 Python/TypeScript唯一fixture、Agent Runtime默认关闭合同、OpenAPI兼容、Windows PowerShell 5.1 + Pester 3.4、Web测试聚合、lint、build-prod、中文提交/注释/配置说明和分支脚本临时仓库验证。禁止执行 M01–M13 模块门禁；禁止用 M02 的 gateway runtime cleanup 等历史定向测试阻塞 M00；禁止运行只属于 M13 的后端仓库全量 pytest。

候选 Pester 必须使用兼容 Pester 3.4 的数组断言：用“($items -contains $expected) | Should Be $true”，不得把文件内容断言“Should Contain”用于数组。当前没有 Jenkins 或其他远端 CI：不要新增 Jenkinsfile，不要求 Gitee/Jenkins 管理员配置，不把缺少远端调度、保护分支或Webhook视为 M00 阻塞；本地候选全部绿色后自动化状态写 automation_local_ready，绝不能写 automation_active。

完成独立审核；只有上述 M00 本地门禁全部绿色且四条远端基线未变化时，才更新 feature/agent_0.8.4_boguan、M00/BOARD 状态和 MERGE_LOG。失败时保持长期分支不变并报告安全证据。不得调用真实付费 API。
```

### 9.6 A 首次启动普通模块（以 M01 为例）

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md，以及 docs/agentization 下的 README.md、architecture-design.md、phased-rollout-plan.md、contracts-v1.md、work-breakdown.md、branch-and-codex-runbook.md、test-matrix.md、status/BOARD.md 和 status/M01-status.md。

你是 A 开发线 Codex。请全自动执行 M01 的第一个未完成切片。先验证依赖已进入 feature/agent_0.8.4_boguan，执行 dev→agent 安全预检，然后创建或恢复 codex/agent-0.8.4-m01-runtime-store 和独立模块 worktree。M01 内部所有切片严格串行，本次只完成一个切片；完成 TDD、测试、独立审核、状态记录、独立 commit 和 push 后停止，并报告下一切片。不得建立切片子分支/worktree，不得自动继续整个模块，不得直接修改两个长期 feature 分支；只有硬阻塞才询问。
```

启动 M03 等其他 A 模块时，只替换开发线模块编号、状态文件和模块分支名。

### 9.7 A 新对话继续普通模块

```text
不要依赖任何旧对话内容。先阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档（包括 phased-rollout-plan.md）和目标 A 线模块状态文件。检查目标模块远端分支、最近 commit、worktree、用户改动、上一切片证据和唯一写入者；安全后只执行该模块下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得创建切片子分支，不得直接修改两个长期 feature 分支。如果本切片是 phased-rollout-plan.md 明确列出的检查点，运行阶段门禁，绿色后写 ready_for_phase_integration 并 push；如果是最后一片，运行完整模块门禁，绿色后写 ready_for_integration 并 push。随后停止；当前为 automation_local_ready 时，明确提示开发者复制 9.10A 话术手动启动单槽集成。只有硬阻塞才询问。
```

### 9.8 B 首次启动普通模块（以 M07 为例）

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md，以及 docs/agentization 下的 README.md、architecture-design.md、phased-rollout-plan.md、contracts-v1.md、work-breakdown.md、branch-and-codex-runbook.md、test-matrix.md、status/BOARD.md 和 status/M07-status.md。

你是 B 开发线 Codex。请全自动执行 M07 的第一个未完成切片。先验证依赖已进入 feature/agent_0.8.4_boguan，执行 dev→agent 安全预检，然后创建或恢复 codex/agent-0.8.4-m07-web-runtime 和独立模块 worktree。M07 内部所有切片严格串行，本次只完成一个切片；完成 TDD、测试、独立审核、状态记录、独立 commit 和 push 后停止，并报告下一切片。不得建立切片子分支/worktree，不得自动继续整个模块，不得直接修改两个长期 feature 分支；只有硬阻塞才询问。
```

启动 M08/M09 等其他 B 模块时，只替换模块编号、状态文件和模块分支名。

### 9.9 B 新对话继续普通模块

```text
不要依赖任何旧对话内容。先阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档（包括 phased-rollout-plan.md）和目标 B 线模块状态文件。检查目标模块远端分支、最近 commit、worktree、用户改动、上一切片证据和唯一写入者；安全后只执行该模块下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得创建切片子分支，不得直接修改两个长期 feature 分支。如果本切片是 phased-rollout-plan.md 明确列出的检查点，运行阶段门禁，绿色后写 ready_for_phase_integration 并 push；如果是最后一片，运行完整模块门禁，绿色后写 ready_for_integration 并 push。随后停止；当前为 automation_local_ready 时，明确提示开发者复制 9.10A 话术手动启动单槽集成。只有硬阻塞才询问。
```

### 9.10 同时启动不同模块的示例

A 在 M00 完成后可打开两个独立 Codex 任务：

```text
不要依赖旧对话，先读根目录 AGENTS.md 和 agentization 开发入口。作为 A 线按运行手册全自动执行 M01 的第一个未完成切片；模块内严格串行，本次只做一个切片。
```

```text
不要依赖旧对话，先读根目录 AGENTS.md 和 agentization 开发入口。作为 A 线按运行手册全自动执行 M03 的第一个未完成切片；模块内严格串行，本次只做一个切片。
```

B 可同时打开三个独立 Codex 任务：

```text
作为 B 线按运行手册全自动执行 M07 的第一个未完成切片；模块内严格串行，本次只做一个切片。
```

```text
作为 B 线按运行手册全自动执行 M08 的第一个未完成切片；模块内严格串行，本次只做一个切片。
```

```text
作为 B 线按运行手册全自动执行 M09 的第一个未完成切片；模块内严格串行，本次只做一个切片。
```

短话术只有在 `AGENTS.md` 和本手册已经包含完整自动化约束时使用；发现文档不一致必须停止开工并先修正文档。

### 9.10A 无远端 CI 时手动启动单槽集成

当目标模块状态已经是 `ready_for_phase_integration` 或 `ready_for_integration`，开发者新开一个 Codex 任务，复制下面的话术，并在同一条消息中明确提供模块号；阶段检查点还要提供 release ID 和切片号：

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、docs/agentization 全部开发入口文档、目标模块状态文件、status/BOARD.md、integration/DECISIONS.md 和 integration/MERGE_LOG.md。当前自动化状态是 automation_local_ready，没有 Jenkins 或其他远端 CI。请全自动且只执行我在本消息指定模块的单槽集成：先 fetch 并固定最新 Agent、dev 和目标模块远端 checkpoint，验证状态只能是 ready_for_phase_integration 或 ready_for_integration、提交已 push、阶段检查点在白名单中且没有其他集成人；然后由本次任务调用 scripts/agentization/Integrate-AgentModule.ps1，按“最新 Agent + 最新 dev + 目标模块增量”创建全新候选并运行该模块权威门禁。绿色且远端基线未变化时才更新 Agent、目标模块状态、BOARD 和 MERGE_LOG；失败时分别写 phase_integration_blocked 或 integration_blocked，并保持 Agent 不变。不得自动执行目标模块下一切片，不得运行其他模块门禁，不得调用真实付费 API，不得把状态写成 automation_active。
```

每次人工集成只允许一个模块或一个阶段检查点；禁止在同一任务中批量扫描并集成所有 ready 模块。模块号、release ID 和切片号缺失或与状态文件不一致时必须 fail-closed。

### 9.11 M13 与生产发布的两道门

M13 仍然只有一个模块分支 `codex/agent-0.8.4-m13-integration` 和一个 worktree，M13.1→M13.2→M13.3→M13.4→M13.5 严格串行。每个 M13 切片都必须由开发者手动启动一次；切片内部的代码、测试、审核、状态记录、commit 和 push 自动完成。

M13.x 通过只表示“对应发布候选已经具备申请上线的资格”，**不表示已经发布生产**。M13.1–M13.4 默认只允许非付费门禁、测试环境全量验证或 dry-run；切片先写 `ready_for_phase_integration:R*` 并停止，开发者使用 9.10A 人工触发单槽候选，绿色进入 Agent 后再写 `phase_integrated:R*` 和 `awaiting_release_approval:R*`。生产运行模式、`enabled_intents` 范围、Feature Flag 和真实付费供应商冒烟是另一道外部状态门禁，必须由唯一发布负责人再明确批准一次，且一次批准只允许一个批次的精确变化。当前无真实外部用户，不设计随机百分比灰度或用户白名单，各阶段获批后均覆盖全部新对话100%。

| 切片 | 允许启动的最早条件 | 切片通过后的停止点 |
| --- | --- | --- |
| M13.1 / R1 | M00-I.1 已完成；M01、M03、M04、M07 和 M12.3 的 R1 增量已进入 Agent；最新 dev→agent 门禁绿色 | 先 `ready_for_phase_integration:R1`；人工触发的单槽候选绿色后才到 `awaiting_release_approval:R1`，不得自动改生产 `assist/100%` |
| M13.2 / R2 | M02、M05、M06、M11、M12.4–M12.5 的 R2 增量已进入 Agent；最新 dev→agent 门禁绿色 | 先 `ready_for_phase_integration:R2`；人工触发的单槽候选绿色后才到 `awaiting_release_approval:R2`，不得自动把 `video` 切到 `primary`；比例保持100% |
| M13.3 / R3 | M08、M09、M10 的 R3 增量已进入 Agent；最新 dev→agent 门禁绿色 | 先 `ready_for_phase_integration:R3`；人工触发的单槽候选绿色后才到 `awaiting_release_approval:R3`，不得自动开放四类 intent |
| M13.4 / R4 | M01–M12 最终门禁全部绿色并完成最后一次 dev→agent 同步 | 先 `ready_for_phase_integration:R4`；人工触发的单槽候选绿色后才到 `awaiting_release_approval:R4`；保持 `primary + 四类intent + 100%`，只做稳定化/回滚门禁 |
| M13.5 / R4 | M13.4 通过，且发布负责人明确批准真实付费供应商冒烟并提供临时凭据 | 真实报告、运行手册和发布签字完成后停止 |

### 9.12 手动启动 M13.1 / R1

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md，以及 docs/agentization 下的 README.md、architecture-design.md、phased-rollout-plan.md、contracts-v1.md、work-breakdown.md、branch-and-codex-runbook.md、test-matrix.md、status/BOARD.md、status/M13-status.md、integration/DECISIONS.md 和 integration/MERGE_LOG.md。

你是本周唯一 M13 集成人。请全自动执行且只执行 M13.1 / R1。先确认 M00-I.1 已完成，M01、M03、M04、M07、M12.3 的 R1 增量已经进入 feature/agent_0.8.4_boguan，最新 dev→agent 门禁绿色；然后创建或恢复 codex/agent-0.8.4-m13-integration 及独立 worktree。完成 assist 配置候选、migration/OpenAPI、压缩 Notice/排队/恢复、旧流程等价、flag-off 和全部 R1 非付费门禁，并在测试环境以 assist+100% 覆盖全部新对话验证；完成独立审核、状态/测试记录、独立 commit 和 push 后立即停止，并把状态写为 ready_for_phase_integration:R1，提示开发者复制 9.10A 话术人工触发单槽集成；只有候选绿色进入 Agent 后才可写 phase_integrated:R1 和 awaiting_release_approval:R1。不得修改生产 Feature Flag、不得把生产从 off+0% 改为 assist+100%、不得调用真实付费 API、不得自动执行 M13.2；依赖不满足时保持分支不变并报告证据。
```

### 9.13 手动启动 M13.2 / R2

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档、status/M13-status.md、status/BOARD.md 和最近的 R1/R2 门禁记录。你是本周唯一 M13 集成人。请恢复 codex/agent-0.8.4-m13-integration，并且只执行 M13.2 / R2。先确认 M13.1 已完成，M02、M05、M06、M11、M12.4–M12.5 的 R2 增量已进入 Agent，最新 dev→agent 门禁绿色。必须继承 R1 的统一上下文合同：所有节点从 profile 读取 896K 有效窗口、32K 输出预留、32K 安全预留，DeepSeek V4 Pro 档案为 1000000 tokens 且 require_verified_model_profile=true，压缩失败退避 30 秒；不得新增视频节点常量或恢复 128K 业务兜底。完成视频 replay/shadow、黄金对话、mock E2E、重复 start=0、kill switch、附件上下文、压缩期排队/恢复和禁止 shadow 计费/PowerMem record 的非付费门禁，并在测试环境以 primary(video)+100% 验证；完成审核、状态/测试记录、独立 commit 和 push 后立即停止，并写 ready_for_phase_integration:R2，提示开发者复制 9.10A 话术人工触发单槽集成；只有候选绿色进入 Agent 后才可写 awaiting_release_approval:R2。不得修改生产配置、不得在未批准时把 video 切到 primary、不得调用真实付费 API、不得自动执行 M13.3。
```

### 9.14 手动启动 M13.3 / R3

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档、status/M13-status.md、status/BOARD.md 和最近的 R2/R3 门禁记录。你是本周唯一 M13 集成人。请恢复 codex/agent-0.8.4-m13-integration，并且只执行 M13.3 / R3。先确认 M13.2 已完成，M08、M09、M10 的 R3 增量已进入 Agent，最新 dev→agent 门禁绿色。必须继承 R1 的统一上下文合同：所有新旧节点从 profile 读取 896K/32K/32K，模型档案严格验证且失败退避 30 秒；不得按图片、PPT、视频分析分别写窗口常量。完成图片/编辑、PPT、视频分析 mock E2E，以及四类 intent 的附件保留、自动压缩、压缩期输入排队继续、受控重试、重启、断线、并发、402、旧 API 和 flag-off 回归；完成审核、状态/测试记录、独立 commit 和 push 后立即停止，并写 ready_for_phase_integration:R3，提示开发者复制 9.10A 话术人工触发单槽集成；只有候选绿色进入 Agent 后才可写 awaiting_release_approval:R3。不得修改生产配置、不得开放生产四类 intent、不得调用真实付费 API、不得自动执行 M13.4。
```

### 9.15 手动启动 M13.4 / R4

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档、status/M13-status.md、status/BOARD.md 和最近的全量门禁/回滚记录。你是本周唯一 M13 集成人。请恢复 codex/agent-0.8.4-m13-integration，并且只执行 M13.4 / R4。先确认 M01–M12 全部模块已完成最终集成，最后一次 dev→agent 同步绿色。必须审计全部 Agent/节点都由共享 ContextBudgetPolicyProvider 读取 896K/32K/32K，严格使用 1000000 tokens 模型档案和 30 秒失败退避，任何 128K 业务兜底或节点级窗口常量都视为阻断。完成五条主流程和直接图片编辑的附件、自动压缩、排队继续、失败恢复全量非付费矩阵，以及 Shadow、并发、断线恢复、kill switch、排空和回滚演练；保持 primary+四类intent+100% 的既定范围，不设计逐级百分比灰度。完成审核、状态/测试记录、独立 commit 和 push 后立即停止，并写 ready_for_phase_integration:R4，提示开发者复制 9.10A 话术人工触发单槽集成；只有候选绿色进入 Agent 后才可写 awaiting_release_approval:R4。不得自动修改生产模式或intent范围，不得调用真实付费 API，不得自动执行 M13.5。
```

### 9.16 经批准启动 M13.5 / R4 真实冒烟

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档、status/M13-status.md、status/BOARD.md、最近的 M13.4 门禁/回滚证据和发布负责人的本次书面批准。你是本周唯一 M13 集成人。我明确批准本次 M13.5 真实付费供应商冒烟；授权范围只限批准记录列出的环境、流程、账号、次数和费用上限。请恢复 codex/agent-0.8.4-m13-integration，只执行 M13.5：从进程环境读取临时 Authorization，执行批准范围内的真实冒烟，完成安全脱敏报告、运行手册、AGENTS/README/最新设计同步、发布签字、独立 commit 和 push 后立即停止。不得扩大测试范围、不得把凭据写入代码/文档/日志、不得自动改变生产运行模式、intent范围或 Feature Flag；批准、凭据或费用上限不完整时不得调用真实接口。
```

<a id="r1-release-approval"></a>

### 9.17 R1 生产发布的明确批准话术（assist + 100%）

M13.1 通过并进入 Agent 后，如果阶段报告、回滚方案和生产访问条件都齐全，发布负责人另开一个 Codex 任务并复制下面这段。**发送这段话就是人工批准动作；不要求发布负责人亲自编辑 YAML。** Codex/流水线只在获得生产权限、审批链和可回滚部署入口后执行，遇到二次认证或平台强制人工按钮时再由发布负责人完成该不可委托步骤。

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/phased-rollout-plan.md、docs/agentization/status/BOARD.md、docs/agentization/status/M13-status.md，以及 M13.1/R1 阶段报告和回滚证据。

我以本次唯一发布负责人身份，明确批准执行 R1 生产发布：对全部新建对话启用 agent_runtime.mode=assist 和 context_compaction_enabled=true，enabled_intents 保持空列表，new_conversation_rollout_percent=100；保持 context_budget=896K/32K/32K、require_verified_model_profile=true、compaction_retry_backoff_seconds=30，并复核 deepseek-v4-pro 的 max_context_tokens=1000000 档案仍有效。现有图片、视频、PPT、视频分析阶段工作流仍拥有推进权，历史对话和运行中任务不得迁移。当前不使用随机百分比灰度或用户白名单。请先复核 M13.1 已进入 feature/agent_0.8.4_boguan、最新 dev→agent 门禁绿色、生产备份/kill switch/回滚路径可用，再通过受控配置和发布流水线完成本次变更、部署后 smoke/指标观察、BOARD/M13/MERGE_LOG/发布记录更新。任何门禁失败、模型档案失效或红线指标异常立即停止并回滚到 off+0%；本次授权不包含 R2、primary、真实付费供应商测试或 Agent→dev 合并。完成或回滚后立即停止并报告证据。
```

后续 R2/R3/R4 使用同一原则：比例始终保持全部新对话100%，每次批准只写明一个精确能力范围变化。R2 只批准 `mode=primary + enabled_intents=[video]`；R3 只批准 `mode=primary + enabled_intents=[video,image,ppt,video_analysis]`；R4 不扩大模式、intent或比例，只批准在既有全量范围内完成稳定化、回滚验收和经单独授权的真实冒烟。不得使用“按计划继续后续全部阶段”这种无限授权；每个阶段完成观察和记录后都必须停止，等待下一次人工批准。

## 10. 明确不采用的方案

### 模块级托管长任务

已否决。Codex 不得一次连续开发完整模块。每个切片结束后必须停止，开发者手动启动下一片。

### 同一模块内切片并行

已否决。不设计 `parallel_safe`、切片子分支、切片 worktree 或切片合并流程；模块内并行会增加状态、文件锁和二次合并复杂度。

### 只靠人记得定期同步 dev

已否决。当前虽然由开发者人工触发，但模块开工、阶段/最终集成和漂移检查都必须使用状态文件、固定话术和仓库脚本，禁止临场手写合并命令或跳过门禁；未来远端 CI 也只能调用同一脚本。

### 每次 dev push 未经测试直接进入 Agent

已否决。所有同步先进入临时候选并通过非付费回归。

### dev 与 agent 双向自动同步

已否决。M13 人工批准前只允许 dev→agent。

## 11. 提交、推送和完成定义

- 每个切片至少一个独立 commit，完成即 push 当前模块分支；
- commit 标题和正文、PR/合并说明、状态/交接/测试/发布记录必须使用中文；允许保留类名、配置键、模块号、命令和 `Agent/API/DTO` 等技术标识，但主体语义不能是纯英文。自动集成 commit 也必须使用中文模板；
- 新增或修改的人工代码注释、docstring、JSDoc 和脚本说明必须使用中文；机器指令、许可证、外部协议原文等最小例外按 `AGENTS.md` 执行；
- 每一个新增或修改的叶子配置项必须有紧邻的详细中文注释；不支持注释的格式必须逐键提供 schema `description` 或同目录中文说明，且禁止在注释中写真实凭据；
- commit/push 前必须执行中文规范检查并记录结果；任一不合规不得 push、不得进入阶段/模块集成状态；
- 禁止普通切片直接 push `feature/dev_*` 或 `feature/agent_*`；
- 已共享模块分支不得 force-push/rebase 改写历史；
- 切片完成 = 代码、定向测试、独立审核、状态记录、commit、push 全部齐全；
- 阶段检查点完成 = 指定增量已通过阶段门禁并进入 Agent；`phase_integrated` 不等于模块完成，下一切片仍由开发者手动启动；
- 模块开发完成 = 所有切片完成且最后一片已写 `ready_for_integration`；
- 模块真正完成 = 单槽候选绿色并已进入 Agent，总看板和合并日志已更新；
- `phase_integration_blocked` 或 `integration_blocked` 不得报告为完成；
- M00 自动化未通过远端验收前，不得把普通模块集成或每日同步描述为无人值守。
