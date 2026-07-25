# PixelFlow 完整 Agent 化改造：开发入口

> 状态：总体设计和四阶段上线节奏已确认并进入模块实施；截至 2026-07-25，M00、M01、M03 已进入 Agent，M04 已完成模块分支最终门禁并等待人工触发单槽集成，其余模块以总看板和各模块状态文件为准。
>
> 适用长期分支：`feature/dev_0.8.4_boguan`（日常业务）与 `feature/agent_0.8.4_boguan`（Agent 集成）；两者在 2026-07-22 均基于 `02493711e8c9b74ec5f8e54cfadac3881297754c`。

这个目录是后续两名开发者和多个 Codex 对话共同执行 Agent 化改造时的唯一进度入口。汇报版 Word 文档用于沟通思路；本目录的 Markdown 文档才是开发期间持续更新的事实来源。

## 开始任何开发前必须阅读

1. 仓库根目录 `AGENTS.md`。
2. `docs/pixelflow-agent-skill-flow-latest-design.md`，了解当前五条流程和不能破坏的业务规则。
3. [总体设计](architecture-design.md)。
4. [接口与状态合同](contracts-v1.md)。
5. [四阶段上线计划](phased-rollout-plan.md)。
6. [两人并行模块与 65 个任务](work-breakdown.md)。
7. [总进度看板](status/BOARD.md)和自己领取模块的状态文件。
8. [分支、自动同步与 Codex 执行手册](branch-and-codex-runbook.md)。
9. [测试矩阵](test-matrix.md)。

面向评审和汇报的整合版为 [`docs/PixelFlow完整Agent化改造方案与并行实施计划.docx`](../PixelFlow完整Agent化改造方案与并行实施计划.docx)；开发执行仍以上述 Markdown 事实源为准。

## 文档职责

| 文档 | 谁更新 | 何时更新 |
| --- | --- | --- |
| 总体设计 | 架构负责人 | 只有评审通过的架构变更才更新 |
| `phased-rollout-plan.md` | 架构/发布负责人 | 上线批次、阶段检查点、运行模式或intent范围变化时 |
| `contracts-v1.md` | M00 合同负责人 | DTO、事件或状态机变更先走合同评审 |
| `work-breakdown.md` | 集成人 | 模块拆分或依赖确实变化时 |
| `status/Mxx-*.md` | 当前模块开发者 | 开工前、每个小任务完成后、交接前 |
| `status/BOARD.md` | 集成人 | 模块合入集成分支并通过模块闸门后 |
| `integration/MERGE_LOG.md` | 当次合并人 | 每次合并、回滚、冲突解决后 |
| `integration/DECISIONS.md` | 决策提出人 | 发生合同外判断时，先记录再实现 |
| `branch-and-codex-runbook.md` | M00/集成人 | 分支、同步触发、脚本参数或 Codex 启动口令变化时 |
| 汇报版 Word | 架构负责人 | 架构、模块、工时、分支策略或开工口令发生评审级变化时，从事实源重新生成并复核 |

开发者不要同时编辑同一个状态文件。总看板只记录已经合入的事实，个人分支上的进展记录在模块状态文件中。

## 新 Codex 对话的恢复顺序

每次新开 Codex 对话，先执行以下恢复动作，不要凭聊天记忆继续：

1. 读取上述 1–8 项文档。
2. 读取当前模块状态文件中的“当前小任务”“最后验证”“下一步第一条命令”。
3. 检查当前分支、`git status` 和模块状态里记录的基线提交。
4. 只处理当前小任务；完成后运行该任务的定向测试。
5. 更新模块状态文件，记录修改文件、测试结果、遗留问题和下一步。
6. 如果当前切片是四阶段计划明确列出的检查点，执行阶段检查点闸门；如果是模块最后一片，执行最终模块闸门。不以“代码写完”代替阶段集成或模块完成。

## 分支与合并约定

- 日常业务权威分支：`feature/dev_0.8.4_boguan`。
- Agent 集成分支：`feature/agent_0.8.4_boguan`。
- 开发期间只允许 `dev → agent`；M13 完成前不自动执行 `agent → dev`。
- 模块第一个切片开工前、明确阶段检查点和模块最后一片的集成门禁都必须纳入远端最新 dev。当前没有 Jenkins 或其他远端 CI，M00 本地脚本和候选级门禁绿色后状态为 `automation_local_ready`；模块集成与 dev→agent 漂移检查由开发者按执行手册人工触发，不能宣称已经无人值守运行。
- 模块分支按需自动创建，不提前建立全部长期分支。M00 是特殊启动模块，只保留 `codex/agent-0.8.4-m00-a`、`codex/agent-0.8.4-m00-b` 两条开发分支；首次收口使用一次性的 `codex/integrate-m00-YYYYMMDD-HHMM` 候选。其他模块各使用一个模块分支。
- 所有模块内部切片严格串行：一个切片对应一个 Codex 任务、一个独立 commit；当前切片完成后必须由开发者手动发出“继续下一个未完成切片”，Codex 不得自行连续执行整个模块。模块内不创建切片子分支，也不允许多个写入型 Codex 同时处理同一模块。
- M00-A 与 M00-B 从同一个已评审 `contracts-v1.md` 和同一个 Agent 基线并行开始，各自在自己的分支串行；M00 首次集成由开发者手动启动一次。M00 以 `automation_local_ready` 合入后，普通模块到达四阶段计划列出的检查点时置为 `ready_for_phase_integration`，模块最后一片置为 `ready_for_integration`；当前由开发者手动启动单槽集成，未来实际启用远端 CI 后才改为自动触发。失败分别置为 `phase_integration_blocked` 或 `integration_blocked`，不得污染 Agent 主干。
- `M00-I.1` 的 M00 范围全量门禁不包含 M02 定向集合，也不包含只属于 M13 的后端仓库全量测试；下游模块的既有红灯不能反向阻塞 M00。
- 新实现全部受 feature flag 保护。关闭开关时，当前 v2 五条流程行为必须不变。
- 旧对话固定 `frontend_v2`；新对话按获批阶段固定运行模式，R1 全部新对话使用 `assist`，R2 仅 `video` 进入 `primary`，R3/R4 四类intent进入 `primary`；存在 pending job 的对话绝不在线迁移编排所有权。
- 完整分支清单、创建/合并顺序、自动同步和可直接复制的 Codex 指令，**唯一以[执行手册第9节](branch-and-codex-runbook.md#codex-prompts)为准**。
- `architecture-design.md`、`work-breakdown.md`、README 和状态文件不得复制另一套话术，只能链接第9节。发现任何文档与第9节不一致时停止开工，由 M00/集成人先修正规则或引用。
- 合并顺序和验证结果写入 `integration/MERGE_LOG.md`。

## 中文工程交付规范（硬性）

- commit 标题和正文、PR/合并说明、状态/交接/测试/发布记录必须用中文；允许夹带必要的英文技术标识，但主体语义必须是中文。自动合并 commit 同样使用中文模板。
- 新增或修改的代码注释、docstring、JSDoc 和脚本说明必须用中文；代码标识符和外部协议字段保持语言/合同惯例，不要求中文化。
- 新增或修改配置时，每个叶子配置项必须有紧邻中文注释，详细说明用途、影响，并按适用情况补充类型、单位、默认值、范围、重启/生效对象、回滚和敏感值获取方式。JSON 等不支持注释的格式用 schema `description` 或同目录中文说明逐键替代。
- 每个切片在 commit/push 前运行中文规范检查并由 reviewer 复核；不合规不得 push、不得进入 `ready_for_phase_integration` 或 `ready_for_integration`。完整边界以根目录 `AGENTS.md` 和[总体设计](architecture-design.md)为准。

## 两个人与多个 Codex 对话怎么并行

- 人员和 Codex 的写入并行单位都是“模块”。同一个人可同时打开 2–3 个 Codex 任务开发依赖已满足且锁定文件不重叠的不同模块；同一模块内部所有 1–3 小时切片严格串行。
- 每个切片可以新开一个 Codex 对话；对话结束前必须把事实写回模块状态文件，不能把聊天记录当交接文档。
- 同一模块同一时刻只能有一个写代码的 Codex，后续切片复用同一个模块分支/worktree；另开对话只允许只读审核，或者在上一切片完成并释放写入权后接续开发。
- M00 的写入型并行只发生在两个独立模块线：A 写 `m00-a`，B 写 `m00-b`；两条线内部仍然串行，不能共同写对方分支或临时集成候选。
- 每个并行模块使用独立分支/worktree。Codex 对话先声明 `base SHA + branch + module + slice + locked_files`，再开始写入。
- 普通切片 Codex 只 push 当前模块分支，不直接更新两个长期 feature 分支。普通模块到达明确阶段检查点或最后一片后，开发者按执行手册人工启动单槽候选更新 `feature/agent_0.8.4_boguan`；M00 首次集成、生产运行模式/intent范围调整和最终 Agent→dev 收口同样需要人工明确启动/批准。
- 如果切片做到一半需要换对话，先填写 `templates/HANDOFF_TEMPLATE.md`；接手对话按“精确恢复动作”继续，不重新猜设计。

## “完成”的统一定义

一个小任务完成：代码、定向测试、状态记录三项齐全，并且中文提交/注释/配置说明门禁通过。

一个上线检查点完成：该批次要求的模块增量已通过阶段闸门并进入 Agent；如果模块还有后续切片，状态是 `phase_integrated`，不等于模块完成。

一个模块完成：该模块全部切片完成、模块测试和 feature flag 关闭回归通过，并已由单槽候选合入 Agent；只有切片完成但尚未集成时状态是 `ready_for_integration`，不能称为模块完成。

整个改造完成：M00–M13 全部合入，五条主流程和图片编辑流程均通过新旧双运行时回归，重复请求不产生重复计费任务，上下文压缩开始/完成对前端可感知，全量发布和回滚演练通过。
