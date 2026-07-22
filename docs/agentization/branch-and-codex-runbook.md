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
最新 dev → 最新 Agent → M13 全量/Shadow/灰度/回滚
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

### 2.2 普通模块最后一个切片完成后

最后一个切片完成全部门禁后，把模块状态写成 `ready_for_integration` 并 push。远端单槽流水线随后自动构建：

```text
最新 origin/feature/agent_0.8.4_boguan
  + 最新 origin/feature/dev_0.8.4_boguan
  + 已完成模块分支
  = codex/integrate-mXX-YYYYMMDD-HHMM
```

候选必须满足：

- dev-sync guard、模块定向/边界测试和 flag-off 回归全部绿色；
- 候选构建后远端 agent/dev 没有再次前进；
- 只有单槽流水线可以更新 Agent 保护分支；
- 绿色时更新 Agent、`status/BOARD.md` 和 `integration/MERGE_LOG.md`；
- 冲突或测试失败时 Agent 保持不变，模块状态写 `integration_blocked` 并保存安全证据；
- dev 在测试期间前进时当前候选自动失效并从最新远端重建。

普通模块不要求开发者再手动启动集成任务。M00 是引导自动化的例外，见 2.4。

### 2.3 每天北京时间 02:00

Gitee/Jenkins 定时流水线执行 `dev-sync reconciliation`：

- dev 没有领先：成功结束，不做变更；
- dev 领先且可无冲突合并：创建同步候选，运行非付费回归，绿色后进入 Agent 单槽队列；
- 冲突或测试失败：不修改 Agent，记录安全证据并通知；
- 凭据、用户内容、完整 URL 或异常堆栈不得写入通知和日志。

Codex 对话结束后不会在每天 02:00 自行唤醒。定时能力必须由 M00 生成的仓库脚本和一次性配置的 Gitee/Jenkins 调度执行。M00 未完成前只能标记 `design_only`；本地脚本完成但远端未配置时只能标记 `automation_local_ready`；实际调度、保护分支和绿色自动合并验证后才能标记 `automation_active`。

### 2.4 M00 首次引导例外

M00 自身负责创建上述自动化，因此不能依赖尚不存在的流水线完成第一次集成：

1. 开发者分别启动 M00-A、M00-B 的每个短切片；两条线并行，各线内部串行。
2. A/B 全部完成后，开发者手动启动一次 `M00-I.1`。
3. `M00-I.1` 创建临时 `codex/integrate-m00-YYYYMMDD-HHMM`，纳入最新 agent/dev、M00-A、M00-B 并运行跨端/自动化门禁。
4. 完成一次性 Gitee/Jenkins 管理员配置并实际验收后，M01–M12 才能依赖无人值守自动集成和每日调度。

## 3. 自动化脚本与一次性远端配置

M00 计划交付：

| 计划文件 | 职责 |
| --- | --- |
| `scripts/agentization/Test-AgentBranchPolicy.ps1` | 检查分支、祖先关系、模块写入者、工作区和状态文档 |
| `scripts/agentization/Sync-DevToAgent.ps1` | 创建安全的 dev→agent 同步候选并执行同步测试 |
| `scripts/agentization/Start-AgentModule.ps1` | 同步预检后创建模块分支、worktree、状态记录和远端 tracking；M00 支持 `a/b` lane |
| `scripts/agentization/Integrate-AgentModule.ps1` | 获取单槽锁，按最新 Agent + dev + 模块构建候选并执行门禁 |
| `scripts/agentization/Invoke-AgentModuleGate.ps1` | 根据模块 ID 运行定向、边界、flag-off 和构建测试 |
| `scripts/agentization/Reconcile-DevToAgent.ps1` | 每日 02:00 漂移检查入口 |

不再设计 `Start-AgentSlice.ps1`。所有切片顺序复用模块分支/worktree。

一次性管理员准备：

- 为 Codex/CI 配置最小权限 Gitee 凭据，凭据只放系统凭据库或 CI secret；
- 将 Agent 设置为保护分支/评审模式，禁止普通开发直接 push；
- 配置 Gitee WebHook + Jenkins 或团队可用的 Gitee 流水线；
- 配置普通模块 `ready_for_integration` 触发器、单槽锁和绿色自动合并；
- 配置每天北京时间 02:00 的调度；
- 验证失败时不更新 Agent，日志不泄露 token、Authorization 或业务内容。

如果暂时没有远端流水线，逐切片开发仍可使用本地脚本，但普通模块不会自行集成、每天 02:00 也不会自动执行；不得把这种状态描述成“已经自动化”。

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
```

候选完成或关闭后删除，不得在临时候选上继续堆业务代码。

## 5. 模块并行和依赖波次

模块之间允许并行，前提是依赖已满足且锁定文件不重叠；模块内部禁止并行。

| 波次 | A 线可并行模块 | B 线可并行模块 | 条件 |
| --- | --- | --- | --- |
| W0 | M00-A | M00-B | 同一个设计/Agent 基线；各线内部串行 |
| W1 | M01、M03 | M07、M08、M09 | M00 已合入；M08/M09 使用 fake 开发 |
| W2 | M02（M01 后）、M04（M01+M03 后） | M10、M11；M12 在 M07 后 | 只启动依赖已满足且路径不冲突的模块 |
| W3 | M06（M01+M02 后）、M05（M02–M04 后） | M08–M11 真实联调 | A 的真实 Port 可用 |
| W4 | M13，共同 | M13，共同 | M01–M12 全部进入 Agent |

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
最后一片 → 模块门禁 → ready_for_integration → Codex 停止
远端单槽流水线自动集成
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
10. 更新状态/测试报告/交接信息，只暂存当前切片文件。
11. 创建当前切片独立 commit 并 push 模块分支。
12. 如果不是最后一片，记录下一片第一动作并停止；等待开发者手动继续。
13. 如果是最后一片，运行模块门禁，绿色时写 `ready_for_integration` 并 push；随后停止，由远端流水线接管。
14. 失败或硬阻塞不得声称完成，也不得直接修改长期 feature 分支。

Codex 只有以下情况才停下询问，而不是自行扩大范围：

- 发现冻结合同缺口或需要改变用户已批准设计；
- 同模块存在另一写入者，或工作区用户修改会被覆盖；
- 基线测试失败且原因不属于当前切片；
- 需要真实付费 API、生产凭据、Gitee 管理员权限或 Agent→dev 最终批准；
- 合并冲突不能依据现有合同安全处理；
- 推送/流水线因外部权限或平台状态失败。

## 8. 自动审核的定义

每个切片至少经过：

1. 实现 Codex 自审 diff；
2. 独立只读 reviewer 检查合同、恢复、幂等、安全、测试和越权修改；
3. 实现 Codex处理有效意见并说明拒绝不合理意见的证据；
4. 重新运行全部切片门禁；
5. 状态文件记录 reviewer、发现、处理和最终验证。

审核不等于只看测试绿色。模块最后一片和远端集成候选还要分别运行模块级与跨模块门禁。

## 9. 可直接复制给 Codex 的 A/B 话术

以下每条都可独立用于新 Codex 对话。每次只执行一个切片。

### 9.1 A 首次启动 M00-A

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md、docs/agentization/README.md、docs/agentization/architecture-design.md、docs/agentization/contracts-v1.md、docs/agentization/work-breakdown.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/test-matrix.md、docs/agentization/status/BOARD.md、docs/agentization/status/M00-status.md 和 docs/agentization/status/M00-A-status.md。

你是 A 开发线 Codex。请全自动执行 M00-A 的第一个未完成切片。先执行 dev→agent 安全预检，从同步后的同一 Agent 基线创建或恢复 codex/agent-0.8.4-m00-a 及独立 worktree。M00-A 内部必须严格串行，本次只完成一个切片；完成 TDD、定向/边界测试、独立审核、状态/测试文档、独立 commit 和 push 后立即停止，并报告下一切片。不得自动继续整个 M00-A，不得修改 M00-B 路径，不得直接修改两个长期 feature 分支，不得调用真实付费 API；只有运行手册定义的硬阻塞才询问。
```

### 9.2 A 新对话继续 M00-A

```text
不要依赖任何旧对话内容。先阅读仓库根目录 AGENTS.md、全部 docs/agentization 开发入口文档、docs/agentization/status/M00-A-status.md 和最近测试记录。你是 A 开发线 Codex。请检查远端 codex/agent-0.8.4-m00-a、最近 commit、worktree、用户改动、上一切片证据和当前唯一写入者；确认安全后只执行 M00-A 的下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得重复已完成工作，不得自动进入再下一片，不得修改 M00-B 或两个长期 feature 分支。完成后报告下一切片；只有硬阻塞才询问。
```

### 9.3 B 首次启动 M00-B

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md、docs/agentization/README.md、docs/agentization/architecture-design.md、docs/agentization/contracts-v1.md、docs/agentization/work-breakdown.md、docs/agentization/branch-and-codex-runbook.md、docs/agentization/test-matrix.md、docs/agentization/status/BOARD.md、docs/agentization/status/M00-status.md 和 docs/agentization/status/M00-B-status.md。

你是 B 开发线 Codex。请全自动执行 M00-B 的第一个未完成切片。先执行 dev→agent 安全预检，从与 M00-A 相同的同步后 Agent 基线创建或恢复 codex/agent-0.8.4-m00-b 及独立 worktree。本次只完成一个切片；严格按照 contracts-v1.md 实现 TypeScript 镜像合同和前端测试入口，不得修改 Python 权威 DTO/fixture。完成 TDD、测试、独立审核、状态/测试文档、独立 commit 和 push 后停止。不得修改 M00-A 或两个长期 feature 分支，不得调用真实付费 API；只有硬阻塞才询问。
```

### 9.4 B 新对话继续 M00-B

```text
不要依赖任何旧对话内容。先阅读仓库根目录 AGENTS.md、全部 docs/agentization 开发入口文档、docs/agentization/status/M00-B-status.md 和最近测试记录。你是 B 开发线 Codex。请检查远端 codex/agent-0.8.4-m00-b、最近 commit、worktree、用户改动、上一切片证据和当前唯一写入者；确认安全后只执行 M00-B 的下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得修改 M00-A/Python 权威 fixture 或两个长期 feature 分支。完成后报告结果；只有硬阻塞才询问。
```

### 9.5 手动启动首次 M00 集成

```text
不要依赖任何旧对话内容。先完整阅读根目录 AGENTS.md、docs/agentization/branch-and-codex-runbook.md、work-breakdown.md、test-matrix.md、status/M00-status.md、status/M00-A-status.md、status/M00-B-status.md 和 integration/MERGE_LOG.md。请全自动执行 M00-I.1。只有 A/B 两线全部完成并已 push 时才创建临时 codex/integrate-m00-YYYYMMDD-HHMM；按“最新 Agent + 最新 dev → M00-A → 定向测试 → M00-B → 跨端合同/全量/flag-off/自动化门禁”集成。完成独立审核和一次性 Gitee/Jenkins 配置验收；只有全部绿色且远端基线未变化时才更新 feature/agent_0.8.4_boguan、状态和 MERGE_LOG。否则保持长期分支不变并报告证据。不得调用真实付费 API，不得把未实际配置的远端能力标记为 automation_active。
```

### 9.6 A 首次启动普通模块（以 M01 为例）

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md，以及 docs/agentization 下的 README.md、architecture-design.md、contracts-v1.md、work-breakdown.md、branch-and-codex-runbook.md、test-matrix.md、status/BOARD.md 和 status/M01-status.md。

你是 A 开发线 Codex。请全自动执行 M01 的第一个未完成切片。先验证依赖已进入 feature/agent_0.8.4_boguan，执行 dev→agent 安全预检，然后创建或恢复 codex/agent-0.8.4-m01-runtime-store 和独立模块 worktree。M01 内部所有切片严格串行，本次只完成一个切片；完成 TDD、测试、独立审核、状态记录、独立 commit 和 push 后停止，并报告下一切片。不得建立切片子分支/worktree，不得自动继续整个模块，不得直接修改两个长期 feature 分支；只有硬阻塞才询问。
```

启动 M03 等其他 A 模块时，只替换开发线模块编号、状态文件和模块分支名。

### 9.7 A 新对话继续普通模块

```text
不要依赖任何旧对话内容。先阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档和目标 A 线模块状态文件。检查目标模块远端分支、最近 commit、worktree、用户改动、上一切片证据和唯一写入者；安全后只执行该模块下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得创建切片子分支，不得直接修改两个长期 feature 分支。如果这是最后一片，运行模块门禁，绿色后写 ready_for_integration 并 push，然后停止等待远端单槽流水线；只有硬阻塞才询问。
```

### 9.8 B 首次启动普通模块（以 M07 为例）

```text
不要依赖任何旧对话内容。请先完整阅读仓库根目录 AGENTS.md、docs/pixelflow-agent-skill-flow-latest-design.md，以及 docs/agentization 下的 README.md、architecture-design.md、contracts-v1.md、work-breakdown.md、branch-and-codex-runbook.md、test-matrix.md、status/BOARD.md 和 status/M07-status.md。

你是 B 开发线 Codex。请全自动执行 M07 的第一个未完成切片。先验证依赖已进入 feature/agent_0.8.4_boguan，执行 dev→agent 安全预检，然后创建或恢复 codex/agent-0.8.4-m07-web-runtime 和独立模块 worktree。M07 内部所有切片严格串行，本次只完成一个切片；完成 TDD、测试、独立审核、状态记录、独立 commit 和 push 后停止，并报告下一切片。不得建立切片子分支/worktree，不得自动继续整个模块，不得直接修改两个长期 feature 分支；只有硬阻塞才询问。
```

启动 M08/M09 等其他 B 模块时，只替换模块编号、状态文件和模块分支名。

### 9.9 B 新对话继续普通模块

```text
不要依赖任何旧对话内容。先阅读根目录 AGENTS.md、全部 docs/agentization 开发入口文档和目标 B 线模块状态文件。检查目标模块远端分支、最近 commit、worktree、用户改动、上一切片证据和唯一写入者；安全后只执行该模块下一个未完成切片。自动恢复模块分支/worktree，完成 TDD、测试、独立审核、状态更新、独立 commit 和 push 后停止。不得自动进入再下一片，不得创建切片子分支，不得直接修改两个长期 feature 分支。如果这是最后一片，运行模块门禁，绿色后写 ready_for_integration 并 push，然后停止等待远端单槽流水线；只有硬阻塞才询问。
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

## 10. 明确不采用的方案

### 模块级托管长任务

已否决。Codex 不得一次连续开发完整模块。每个切片结束后必须停止，开发者手动启动下一片。

### 同一模块内切片并行

已否决。不设计 `parallel_safe`、切片子分支、切片 worktree 或切片合并流程；模块内并行会增加状态、文件锁和二次合并复杂度。

### 只靠人记得定期同步 dev

已否决。模块开始、普通模块自动集成和每日 02:00 都必须经过仓库脚本/远端门禁。

### 每次 dev push 未经测试直接进入 Agent

已否决。所有同步先进入临时候选并通过非付费回归。

### dev 与 agent 双向自动同步

已否决。M13 人工批准前只允许 dev→agent。

## 11. 提交、推送和完成定义

- 每个切片至少一个独立 commit，完成即 push 当前模块分支；
- 禁止普通切片直接 push `feature/dev_*` 或 `feature/agent_*`；
- 已共享模块分支不得 force-push/rebase 改写历史；
- 切片完成 = 代码、定向测试、独立审核、状态记录、commit、push 全部齐全；
- 模块开发完成 = 所有切片完成且最后一片已写 `ready_for_integration`；
- 模块真正完成 = 单槽候选绿色并已进入 Agent，总看板和合并日志已更新；
- `integration_blocked` 不得报告为完成；
- M00 自动化未通过远端验收前，不得把普通模块集成或每日同步描述为无人值守。
