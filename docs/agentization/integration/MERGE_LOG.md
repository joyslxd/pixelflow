# Agent 化集成合并日志

> 只有当周集成人更新。

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
- 中文 commit/合并说明、代码注释和配置逐项中文说明门禁结果
- 合并后 smoke 结果
- 回滚方式或 revert SHA
- 同步的设计/README/AGENTS/content-app 文档

## 记录

### 0001 / 2026-07-24 / M00-I.1

- module：`M00`
- source branches / SHAs：
  - `origin/codex/agent-0.8.4-m00-a`：`89cf1ff4dfcd7dd73f1c471935f00c149a7093ef`
  - `origin/codex/agent-0.8.4-m00-b`：`efadb5d48a9c81655332acb2369918c5af88db27`
- integration target before：`origin/feature/agent_0.8.4_boguan@90ace58e58a665d54219698bdf46bf4ba9543610`
- integration target after：本记录随最终候选原子更新 Agent；M00 最终实现验收 SHA 为 `9b7a292b4432db2072c594c90a03d5071cef8c95`，推送后以 `origin/feature/agent_0.8.4_boguan` 复读值为准
- latest dev SHA：`fb7450775a227d891372c19eae1b308045c51e68`
- dependency / contract-design base SHA：`8e626ae232d984f14fa9954b672b4e025894d426`
- candidate：`codex/integrate-m00-20260724-0043`；最新 Agent、dev、M00-A、M00-B 四条冻结引用均为候选祖先，候选没有续用或合入上一条 blocked 分支
- fixed sequence：
  1. 最新 Agent + 最新 dev：`55e187bb9eccf59a372f0d7887318d347d0326c8`
  2. M00-A：`ccc5881c9ab288d5b8e2cba6e407755dbd9d790e`
  3. M00-A 定向 Final 门禁：`4/4`
  4. M00-B：`974bccd01226467e3f5229bc324ba1d85c265cf0`
  5. M00 跨端合同、M00 范围全量、flag-off、本地自动化门禁：最终实现验收 `9b7a292b4432db2072c594c90a03d5071cef8c95`
- checkpoint：M00 首次集成特例，`release_id/checkpoint_slice/checkpoint_commit/last_integrated_commit` 不适用；触发条件为 M00-A/M00-B 均 `ready_for_integration` 且已 push
- single slot：开发者人工启动 `M00-I.1`，唯一候选、唯一集成人；最终状态 `merged`
- file ownership：A/B 两线锁定路径没有相互越界；共享状态只在候选内由唯一集成人更新
- feature flag：启动配置合同已实现，默认 `off + [] + 0 + false`；未接管现有业务
- production：未发布；未修改生产运行模式、intent 范围或 Feature Flag；没有发布批准记录
- automation：`automation_local_ready`；当前无 Jenkins 或其他远端 CI，未新增 `Jenkinsfile`，未要求管理员配置、保护分支或 WebHook
- tests：`docs/agentization/test-reports/M00-I.1.md`；M00-A Final `4/4`、M00 Final `8/8`、后端 `59 passed`、TypeScript `9 passed`、Web 聚合 `214 passed`、lint/build-prod 通过
- reviewer：`/root/m00_i1_independent_review`；终审无 Critical、Important 或 Minor 阻塞，`Ready to merge: Yes`
- conflicts：Agent+dev 的最新设计文档冲突保留 D-008 本地自动化边界和 dev 新增/替换业务事实；M00-A/B 状态冲突保留共同基线、准确远端 SHA 和双线证据
- migration/configuration：无 migration；新增四项 Agent Runtime 启动配置解析及 `backend/pixelflow/agent_runtime/CONFIGURATION.md` 逐项中文说明，非法配置启动即拒绝
- Chinese engineering policy：实现范围 3 个提交、12 个变更路径全部通过；提交/合并说明、人工注释和配置说明符合中文规范
- smoke：仅执行本地配置加载、gateway import、跨端 fixture、Web 聚合、lint 和生产构建；未调用真实图片、视频、PPT、剪映、LLM 或其他付费 API
- exclusions：未运行 M01–M13 模块门禁，未运行 M02 gateway runtime cleanup 等定向集合，未运行 M13 后端仓库全量 pytest
- remote guard：最终门禁和独立终审后重新 fetch，四条远端基线与冻结值完全一致
- rollback：如需撤回，基于本记录定位 Agent 更新范围后使用中文说明的 `git revert` 创建回滚提交；同时把四项 Agent Runtime 配置恢复为默认 `off + [] + 0 + false`。禁止改写共享分支历史
- synchronized docs：`docs/pixelflow-agent-skill-flow-latest-design.md`、M00 状态、BOARD、MERGE_LOG 和本测试报告；未修改 `CONTENT_APP_API_CALLS.md`，因为没有新增或变更 content-app API
