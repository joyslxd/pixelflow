# PixelFlow Agent 化四阶段上线计划

> 状态：已确认。目标是在不等待全部 M00–M13 完成的前提下，先让业务在第 4 个工作日看到自动上下文压缩，在第 9 个工作日使用视频会话 Agent；全部新对话预计在第 16–18 个工作日完成接管。

## 1. 最终节奏

| 上线批次 | 工作日 | 累计时间 | 对业务可见的能力 | 运行模式 |
| --- | ---: | ---: | --- | --- |
| R1 自动上下文压缩可感知版 | 4 天 | 第 4 天 | 压缩开始/完成提示、输入排队、刷新恢复、原任务继续 | `assist`，全部新对话 100% |
| R2 视频会话 Agent MVP | 5 天 | 第 9 天 | 视频流程可自然语言继续、修改、重生成、重试、新建、切换、取消或追问 | `primary`，仅 `video`，全部新对话 100% |
| R3 其余业务接入 Agent | 4 天 | 第 13 天 | 图片/编辑、PPT、视频分析使用同一 Supervisor 和 Context Runtime | `primary`，四类 intent，全部新对话 100% |
| R4 全量稳定化和全面接管 | 3–5 天 | 第 16–18 天 | Shadow、全流程 E2E、回滚和新对话全面接管 | 保持 `primary`、四类 intent、100% |

总工作量没有因为分阶段而消失；变化是第 4 天和第 9 天就能分别交付可演示、可上线的业务成果，不再等到所有流程完成后一次性上线。当前测试和生产均无真实外部用户，因此不使用随机 10%/30%/50% 灰度或内部用户白名单；每个获批阶段直接覆盖全部新对话，阶段之间仍通过独立人工批准控制风险。

## 2. 阶段性集成检查点

原方案只有“模块全部切片完成后集成”。为了支持阶段上线，增加显式 `release checkpoint`，但不改变“每次只执行一个切片、模块内严格串行”的规则。

### 2.1 状态

- `ready_for_phase_integration`：当前模块到达本文件列出的阶段检查点，检查点测试通过，可以把本模块截至当前 commit 的增量纳入 Agent 集成候选。
- `phase_integrated`：该检查点已经通过单槽候选进入 `feature/agent_0.8.4_boguan`，但模块仍可能有后续切片，不能标记为 `done`。
- `phase_integration_blocked`：检查点冲突、测试失败或远端基线变化；Agent 主干保持不变。
- `ready_for_integration`：模块全部切片完成后的最终集成状态，含义保持不变。

模块状态必须记录：`release_id`、`checkpoint_slice`、`checkpoint_commit`、`last_integrated_commit`、`checkpoint_status` 和门禁证据。同一模块后续切片继续复用原模块分支/worktree，禁止 force-push；下一检查点只集成 `last_integrated_commit..checkpoint_commit` 的增量。

### 2.2 单槽集成

普通模块最终完成或达到本文件明确定义的阶段检查点时，单槽集成任务都按以下方式构建候选：

```text
最新 Agent + 最新 dev + 模块 checkpoint commit
  → checkpoint 专属门禁
  → 绿色后更新 Agent 和检查点记录
  → 失败时保持 Agent 不变
```

代码进入 Agent 分支不等于自动发布生产。每个 R1–R4 上线批次仍需唯一发布负责人审核阶段报告并明确批准运行模式或 `enabled_intents` 范围变化。这里有两道独立门：M13.x 切片通过是“具备申请上线资格”，生产批准才是“允许生产新对话使用本阶段能力”。前者不会隐式包含后者。

## 3. R1：自动上下文压缩可感知版

### 3.1 时间和目标

- 开发、联调和上线：4 个工作日。
- 业务第一次可见成果：第 4 个工作日。
- 目标：新对话在不改变现有图片、视频、PPT阶段编排的情况下，先使用统一 Turn、SSE、Context Budget Guard 和压缩状态。

### 3.2 范围

| 开发线 | 模块/切片 | 交付 |
| --- | --- | --- |
| A | M00-A、M01、M03、M04 | 合同、Turn/Summary/Event、上下文档案、60/72/85/92压缩、压缩锁和排队 |
| B | M00-B、M07、M12.1–M12.3 | TypeScript合同、Snapshot/SSE/Reducer、双运行时挂载、压缩和排队UI、历史恢复 |
| A+B | M00-I.1、M13-R1 | M00 跨端 fixture 与本地自动化门禁、assist 门禁、旧流程等价和全部新对话100%发布门禁 |

M12 在 R1 前三个切片后建立 `R1-assist-ui` 检查点；M12 后续表单/Plan/Artifact交互切片继续在同一模块分支串行开发。

### 3.3 用户可见行为

- 开始：`对话内容较长，正在整理上下文，当前任务和已生成内容不会丢失。`
- 完成：`上下文整理完成，正在继续处理刚才的请求。`
- 压缩期间输入框可继续发送，新输入显示“已排队”。
- 刷新或切换对话后，从 Snapshot 恢复压缩和排队状态，前端不自动重发。
- 原始消息、Plan、创作合同和 Artifact 永远保留；摘要只影响下一次模型输入。

### 3.4 配置

```yaml
pixelflow:
  agent_runtime:
    mode: assist
    enabled_intents: []
    new_conversation_rollout_percent: 100
    context_compaction_enabled: true
    context_budget:
      effective_context_k: 896
      output_reserve_k: 32
      safety_reserve_k: 32
      require_verified_model_profile: true
    compaction_retry_backoff_seconds: 30
```

以上是 R1 获批后的**目标生产值**，不是 M13.1 测试通过后自动写入的值：

- `assist`：全部新对话先使用统一 Turn、SSE、Context Budget Guard、压缩与恢复；业务执行权仍归现有阶段工作流，不启用 Supervisor 自主接管。
- `enabled_intents: []`：R1 不让任何图片/视频/PPT/视频分析 intent 进入 `primary`。
- `new_conversation_rollout_percent: 100`：当前无真实外部用户，测试和生产获批后均让全部**新建对话**进入本阶段；历史对话和运行中任务不迁移。
- `context_compaction_enabled: true`：允许 R1 新对话在达到预算阈值时压缩，并向前端发出“开始整理/整理完成”事件。
- `context_budget`：`K=1024 tokens`；全部当前和未来 Agent 节点统一使用 896K 有效窗口、32K 输出预留和 32K 安全预留，可用输入为 832K（851,968 tokens）。R2–R4 新增 Agent、节点或流程不得再增加节点级窗口常量。
- `require_verified_model_profile: true`：当前 `deepseek-v4-pro` 使用 `1,000,000 tokens` 已验证档案；实际流程缺档、未验证或过期时 fail-closed，不走 128K。
- `compaction_retry_backoff_seconds: 30`：压缩失败事务持久化下一次重试时间；Snapshot/SSE/Run 在 30 秒内不重复唤醒，到期后恢复一次，排队输入继续执行。

当前版本不设计也不实现用户白名单字段。以后真正存在外部用户并需要分群时，再单独设计基于后端认证 `user_id`/租户的发布策略；不得把尚未实现的白名单写成现有可配置能力。

M13.1 通过后先写 `ready_for_phase_integration:R1`；远端候选绿色并进入 Agent 后，自动化再写 `phase_integrated:R1` 与 `awaiting_release_approval:R1`。唯一发布负责人必须再使用[执行手册 9.17 的 R1 生产批准话术](branch-and-codex-runbook.md#r1-release-approval)启动一次受控发布任务；该授权只允许生产从 `off+0%` 变为 `assist+100%`，不包含 R2、`primary`、真实付费 API 或 Agent→dev 合并。

### 3.5 上线门禁

- 压缩关键事实、否定约束、合同、ID保留率 100%。
- 压缩期间并发输入不丢失、不重复、不乱序。
- SSE断线后按Cursor续传；gap时重新加载Snapshot。
- Feature Flag关闭时旧流程行为和现有API合同不变。
- 演示使用测试长对话按统一 832K 可用输入触发压缩，禁止为演示降低阈值、关闭严格模型档案或启用 128K 兼容兜底。

## 4. R2：视频会话 Agent MVP

### 4.1 时间和目标

- R1后继续开发 5 个工作日。
- 累计第 9 个工作日上线。
- 目标：只让新视频对话进入 `supervisor_v1`；其他intent仍保留R1 assist/旧阶段编排。

### 4.2 范围

| 开发线 | 模块/切片 | 交付 |
| --- | --- | --- |
| A | M02、M05、M06 | Supervisor/Workflow thread、interrupt/resume、ActionDecision、Validator、持久化Operation/Lease |
| B | M11、M12.4–M12.5 | 视频Workflow Graph、消息/Artifact/@scene目标引用、视频交互卡和任务看板 |
| A+B | M13-R2 | 视频黄金对话、Shadow、Mock E2E、真实长任务全量验证和Kill Switch |

### 4.3 第一版动作

- `answer_only`
- `continue_workflow`
- `modify_workflow`
- `regenerate_stage`
- `retry_failed`
- `start_workflow`
- `switch_workflow`
- `cancel_workflow`
- `clarify`

Plan、创作合同、场景包、最终视频确认、单分镜4–15秒、额度暂停和剪映草稿输入约束全部沿用现有权威规则。任何计费动作目标不唯一时必须追问。

### 4.4 配置

```yaml
pixelflow:
  agent_runtime:
    mode: primary
    enabled_intents:
      - video
    new_conversation_rollout_percent: 100
    context_compaction_enabled: true
```

R2 获批后，全部新对话仍使用统一 Context Runtime；其中 `video` 由 `primary` Supervisor/Workflow Graph 接管，其他 intent 保持 R1 `assist` 和旧阶段工作流。比例不再变化，风险边界由 `enabled_intents` 控制。

### 4.5 上线门禁

- Supervisor动作黄金集准确率≥92%，目标Workflow/Artifact准确率≥95%。
- 计费动作误执行和重复供应商start均为0。
- 视频刷新、断线、进程重启只恢复原Job，不重新计费。
- 视频结果必须人工结束；场景包无自动确认；Plan/合同/资产清单不被摘要改写。
- 402、超时、部分分镜失败和修改循环可恢复。

## 5. R3：图片、PPT和视频分析接入

### 5.1 时间和目标

- R2后继续开发4个工作日。
- 累计第13个工作日上线。
- 目标：复用已经上线的Supervisor、Context Runtime和Job Coordinator，不再新建另一套会话逻辑。

### 5.2 范围

| 开发线 | 模块 | 交付 |
| --- | --- | --- |
| B | M08 | 图片/图片编辑Graph，多图、直接编辑、60秒语义和参数确认 |
| B | M09 | PPT大纲、页面、单页重生成、文件和下载 |
| B | M10 | 单/多视频分析、结果外置、继续/换视频/另开流程 |
| A | 平台稳定化 | 通用Operation、Context、Supervisor和跨Workflow目标定位缺陷修复 |
| A+B | M13-R3 | 三类intent Mock E2E、跨流程切换、Artifact引用和四类intent全量发布门禁 |

### 5.3 配置

```yaml
pixelflow:
  agent_runtime:
    mode: primary
    enabled_intents:
      - video
      - image
      - ppt
      - video_analysis
    new_conversation_rollout_percent: 100
    context_compaction_enabled: true
```

### 5.4 上线门禁

- 图片、编辑、PPT、视频分析旧API和Feature Flag关闭回归全绿。
- 直接图片编辑缺原图时暂停等待上传；失败后重新确认参数。
- PPT只修改目标页时不得重启整套PPT。
- 视频分析完整结果外置，Supervisor只读取摘要和证据引用。
- 同一对话多个Workflow并存时不得串任务、串Artifact或串用户。

## 6. R4：全量稳定化和全面接管

### 6.1 时间和目标

- R3后继续开发3–5个工作日。
- 累计第16–18个工作日完成。
- 目标：在 R3 已经 `primary + 四类 intent + 100%` 的基础上，补齐全流程门禁、真实供应商冒烟、运行监控和回滚演练。

### 6.2 阶段接管顺序

```text
R1：assist + 100%新对话
→ R2：primary(video) + 100%新对话
→ R3：primary(四类intent) + 100%新对话
→ R4：保持R3生产范围，完成稳定化、真实冒烟和发布签字
```

历史 `frontend_v2` 对话继续按原Job和原Owner安全排空；有pending job的旧对话禁止在线切换。回滚只停止新对话进入Supervisor，不强切正在运行的对话。

### 6.3 上线门禁

- M00–M12最终模块门禁和M13完整门禁通过。
- 五条主流程和直接图片编辑Mock E2E、重启、断线、并发、402全绿。
- Shadow不调用付费供应商、不写PowerMem经验。
- Kill Switch、排空、回滚和最后一次dev→agent同步演练通过。
- 经人工批准后才执行真实供应商冒烟；R4 不再改变100%比例，只验证现有全量接管的稳定性和回滚能力。

## 7. 两人按天并行顺序

| 工作日 | A线 | B线 | 当日检查点 |
| --- | --- | --- | --- |
| D1 | M00-A.1起，串行推进A线切片 | M00-B.1 | 合同/fixture一致；准备M00-I.1 |
| D2 | M01与M03不同模块并行，各模块内串行 | M07 | Turn/Context与前端事件fake对齐 |
| D3 | M04；补齐M01/M03门禁 | M12.1–M12.3 | R1候选、压缩和排队恢复 |
| D4 | R1后端/安全门禁 | R1前端/旧流程回归 | `assist` + 全部新对话100%上线 |
| D5–D6 | M02后进入M05 | M11使用fake并行开发 | Supervisor/视频合同对齐 |
| D7–D8 | M06与M05收口 | M11、M12.4–M12.5 | 视频Mock E2E、恢复、402 |
| D9 | R2 Shadow/全量视频门禁 | R2视频UI/任务看板回归 | `primary(video)` + 全部新对话100% |
| D10–D12 | 平台稳定化和跨Workflow缺陷修复 | M08/M09/M10不同模块并行 | 三类Adapter逐个进入Agent |
| D13 | R3跨流程门禁 | R3前端/旧API回归 | `primary(四类intent)` + 全部新对话100% |
| D14–D16 | M13全量、并发、回滚 | M13全量、前端恢复、运行手册 | 保持100%，完成稳定化和回滚验收 |
| D17–D18 | 真实供应商与线上问题缓冲 | 真实流程与交互缺陷缓冲 | 仅按实际问题使用 |

每一行仍遵守：一个Codex任务只执行一个1–3小时切片；开发者手动启动下一个切片；不同模块可并行，同一模块切片不能并行。

## 8. 发布职责和停止条件

### 开发者必须手动批准

- R1、R2、R3、R4每次生产运行模式、`enabled_intents` 范围或 Feature Flag 变化。
- 真实付费供应商测试。
- 生产Kill Switch或Agent→dev最终收口。
- 无法通过自动门禁的冲突和业务取舍。

这里的“手动批准”是指发布负责人必须明确发送一次范围有限的发布指令或点击公司发布平台强制要求的审批按钮，**不是要求开发者亲自登录服务器修改 YAML**。获得批准后，Codex和受控流水线负责复核证据、应用配置、部署、smoke、观察、记录和异常回滚；如果生产平台要求二次认证、人工审批按钮或暂时没有自动化发布入口，则该不可委托步骤仍由发布负责人完成。

R1 的标准批准话术位于[执行手册 9.17](branch-and-codex-runbook.md#r1-release-approval)。M13.1 通过但未收到该明确批准时，生产必须保持 `off + 0%`（或保持发布前原值），不得因为“代码已经进入 Agent”自动启用 `assist + 100%`。

### Codex 和单槽集成任务完成

- 单切片内TDD、测试、审核、状态记录、commit和push。
- 开发者人工触发后，完成明确阶段检查点的候选构建和门禁。
- 失败时写 `phase_integration_blocked` 并保持Agent主干不变。
- 当前在模块开工和集成前人工执行dev漂移检查；未来提升为 `automation_active` 后，才增加每天北京时间02:00调度。

### 必须停止上线

- 任何重复供应商start、跨用户/跨会话污染、Authorization泄漏。
- 压缩丢失权威合同、ID、否定约束或当前输入。
- Supervisor在目标不唯一时执行计费动作。
- Snapshot/SSE恢复导致前端重新启动任务。

## 9. 估算前提

- 两人每天各有6–7小时有效开发/验证时间，并可同时开启不同模块的Codex任务。
- 当前没有 Jenkins 或其他远端 CI；M00 和后续模块按 `automation_local_ready` 人工触发单槽集成与漂移检查。测试环境和模型/供应商配置仍需可用，人工触发产生的排队时间计入各阶段缓冲。
- R1–R3时间包含定向测试、阶段门禁和100%新对话发布验证，不包含无法预估的第三方接口长期故障。
- D17–D18是缓冲，不应提前承诺给新功能。
