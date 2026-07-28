# M11 视频生成 Workflow Adapter

- phase：`in_progress`
- owner：B
- base Agent SHA：`38310bb64385fe276edc0ad99c2f996db2c8c1f8`
- branch：`codex/agent-0.8.4-m11-video-workflow`
- 依赖：M00；真实联调依赖 M05/M06
- 当前切片：`M11.3`
- 当前唯一写入者：`尚未领取`
- 当前锁定文件：`无`
- M11.1 开始时间：`2026-07-28 09:51:58 +08:00`
- M11.1 完成时间：`2026-07-28 10:49:51 +08:00`
- M11.2 完成时间：`2026-07-28 12:07:54 +08:00`

## 切片

- [x] M11.1 intake/Plan/修订/恢复/权威快照（3h）
- [x] M11.2 场景包/全局资产图（3h）
- [ ] M11.3 分镜生成/部分失败/单镜修改（3h）
- [ ] M11.4 merge/QC/402/修改循环/最终结束（3h）
- [ ] M11.5 剪映/版本/历史/下载（2h）

## 恢复提示

这是风险最高、最后联调的业务模块。Plan、scene blueprints、asset manifest 是不可被 Supervisor 或摘要重写的权威数据。

## M11.1 完成记录

- 依赖与预检：远端 Agent 基线为 `38310bb64385fe276edc0ad99c2f996db2c8c1f8`，远端 dev `fb7450775a227d891372c19eae1b308045c51e68` 是其祖先，dev→agent 结果为 `up_to_date`；未修改两个长期 feature 分支，未创建切片子分支/worktree。
- 实现：新增视频领域 `VideoPlanningWorkflowService` 和 `VideoPlanAuthoritySnapshot`，固化 intake 取消、三个方向及重生成、显式选择、初始 Plan、修订、历史恢复和 Runtime 投影。
- 权威边界：规范 JSON + SHA-256 隔离所有可变输入；当前/历史合同统一校验用户确认、场景图能力、精确时长、4–15 秒连续时间线、资产清单和历史一致性；修订冻结版本、意图、模型模式、模型及能力快照。
- TDD：经历模块缺失、Markdown 保真、恢复保真、历史篡改、首轮审核 3 个 RED、修订绕过 6 个 RED、相邻漂移 2 个 RED，最终聚焦测试 `21 passed`。
- 验证：M11 后端权威范围 `477 passed`，Web `305 passed`，Ruff、lint、build-prod、`git diff --check` 和中文工程规范通过；M11 Slice Gate `Passed=True / CommandCount=7`，BranchAutomation `43 passed`。
- 审核：独立只读 reviewer `/root/m11_1_independent_review` 第三轮结论 `Critical=0 / Important=0 / Minor=0 / Ready=Yes`；全部意见均经 RED/GREEN 关闭。
- 外部调用：未调用 LLM、content-app、图片、视频、PPT、剪映或其他真实付费 API；未新增配置、依赖或锁文件变更。
- 阶段：M11.1 不是阶段检查点且不是模块最后一片，`phase` 保持 `in_progress`，不写任何集成就绪状态。
- 报告：完整证据见 `docs/agentization/test-reports/M11.1.md`。
- 提交与推送：本状态随 M11.1 独立中文提交推送到 `origin/codex/agent-0.8.4-m11-video-workflow`。
- 下一步：后续开发者重新执行安全预检并取得唯一写入权后，串行开始 `M11.2 场景包和全局资产图`；本次不进入 M11.2。

## M11.2 完成记录

- 依赖与预检：本地 `HEAD`、远端 tracking 和远端模块分支均为 M11.1 提交 `234ee6074e45ccf50170206a97928cb046d8bf90`；复用既有模块分支和 worktree，未修改两个长期 feature 分支，未创建切片分支或切片 worktree。
- 实现：新增 `VideoScenePackageWorkflowService` 和 `VideoScenePackageAuthoritySnapshot`，只消费用户显式同意的 Plan，冻结场景包、全局资产、素材 URL、资产图生成状态和稳定 Artifact 引用。
- 权威边界：Plan 当前值及完整历史、合同、蓝图和资产清单在准备、恢复与投影边界重复校验；场景身份、顺序、整数时长、正文、Prompt、引用集合及资产图严格继承，篡改和非规范 URL 均失败关闭。
- 兼容隔离：共享旧 v2 helper 的权威行为由默认关闭的 `authority_mode` 控制，旧 Router 保持原 Prompt 与顺序 ID；仅新 Workflow Service 显式开启。
- TDD：经历模块缺失、隐藏历史篡改、正文后缀、Prompt/额外字段、未显式审核、附件替换、伪造投影、模型继承、整数类型、URL 规范和旧 v2 行为回归等 RED，最终 M11.2 专项 `22 passed`、相邻回归 `80 passed`。
- 验证：M11 后端权威范围 `493 passed`，Web `305 passed`，Ruff、lint、build-prod、`git diff --check` 和中文工程规范通过；M11 Slice Gate `Passed=True / CommandCount=7`，BranchAutomation `43 passed`。
- 审核：独立只读 reviewer `/root/m11_2_independent_review` 最终结论 `Critical=0 / Important=0 / Minor=0 / Ready=Yes`，并独立复跑聚焦测试 `38 passed`。
- 外部调用：未调用 LLM、content-app、图片、视频、PPT、剪映、PowerMem 或其他真实付费 API；未新增配置、依赖或锁文件变更。
- 阶段：M11.2 不是阶段检查点且不是模块最后一片，`phase` 保持 `in_progress`，不写任何集成就绪状态，不触发单槽集成。
- 报告：完整证据见 `docs/agentization/test-reports/M11.2.md`。
- 提交与推送：本状态随 M11.2 独立中文提交推送到 `origin/codex/agent-0.8.4-m11-video-workflow`。
- 下一步：后续开发者重新执行安全预检并取得唯一写入权后，串行开始 `M11.3 分镜生成/部分失败/单镜修改`；本次不进入 M11.3。
