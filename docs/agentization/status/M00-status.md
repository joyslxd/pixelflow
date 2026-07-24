# M00 合同、分支自动化、feature flag 与测试入口

- phase：`merged`
- owner：A（后端合同/自动化）+ B（前端镜像合同）
- reviewer：A/B 互审 + 首次集成独立终审 `/root/m00_i1_independent_review` + 门禁基线修复独立终审 `/root/m00_gate_repair_integration_review`
- base SHA：`8e626ae232d984f14fa9954b672b4e025894d426`
- branches：
  - A 线：`codex/agent-0.8.4-m00-a`，本轮冻结远端 HEAD `89cf1ff4dfcd7dd73f1c471935f00c149a7093ef`
  - B 线：`codex/agent-0.8.4-m00-b`，本轮冻结远端 HEAD `efadb5d48a9c81655332acb2369918c5af88db27`
  - 集成候选：`codex/integrate-m00-20260724-0043`
  - 门禁基线修复：`codex/agent-0.8.4-m00-gate-baseline-repair@1aba4ae9e4670930fd456519d8ecc7d4cef39880`
  - 门禁基线修复候选：`codex/integrate-m00-gate-repair-20260724-164428`
- contract/design base SHA：`8e626ae232d984f14fa9954b672b4e025894d426`；M00-A/M00-B 已证明从同一 SHA 创建
- 当前切片：`M00-I.1` 已完成
- automation state：`automation_local_ready`
- feature flag：已实现启动期配置合同，默认严格为 `off + [] + 0 + false`；尚未接管任何业务流程
- 文件锁：A/B 开发写锁均已释放；M00-I.1 仅由唯一集成人写候选和汇总状态

## 切片

- [x] M00-A.1 characterization tests（A，2h）
- [x] M00-A.2 Python DTO/Ports/fakes/规范 fixture（A，3h；依赖 A.1）
- [x] M00-A.3 dev→agent 同步、模块分支/worktree、单槽集成和中文提交/注释/配置说明门禁脚本（A，3h；依赖 A.2）
- [x] M00-B.1 TypeScript 镜像合同、wire event 与 web 测试入口（B，2.5h；与 A 线并行，只读 `contracts-v1.md`）
- [x] M00-I.1 首次 M00 集成、跨端合同、中文工程规范、本地单槽门禁和 `automation_local_ready` 验收（A+B 评审，单一集成人写入）

## 集成记录

- 四条冻结远端引用：
  - Agent：`90ace58e58a665d54219698bdf46bf4ba9543610`
  - dev：`fb7450775a227d891372c19eae1b308045c51e68`
  - M00-A：`89cf1ff4dfcd7dd73f1c471935f00c149a7093ef`
  - M00-B：`efadb5d48a9c81655332acb2369918c5af88db27`
- 固定集成顺序已执行：`最新 Agent + 最新 dev → M00-A → M00-A定向测试 → M00-B → M00跨端合同/M00范围全量/flag-off/本地自动化门禁`。
- 关键候选提交：dev 同步 `55e187b`、M00-A 合入 `ccc5881`、M00-B 合入 `974bccd`、M00-I.1 最终实现与审核整改 `9b7a292`。
- M00-A 定向 Final 门禁 `4/4`；M00 Final 门禁 `8/8`。完整证据见 `docs/agentization/test-reports/M00-I.1.md`。
- Python/TypeScript 共用唯一 Python fixture；前端聚合入口会生成并严格编译 TypeScript fixture 类型，不存在第二权威 fixture。
- Agent Runtime 启动配置非法时 fail-closed；默认关闭。现有 OpenAPI 合同未删除或改名。
- Windows PowerShell 5.1 + Pester 3.4 使用 `-contains` 布尔数组断言；未把文件内容断言 `Should Contain` 用于数组。
- 本轮未执行 M01–M13 模块门禁，未执行 M02 gateway runtime cleanup 等定向测试，未执行 M13 后端仓库全量测试。
- 当前没有 Jenkins 或其他远端 CI；未新增 `Jenkinsfile`，未要求管理员配置、保护分支或 WebHook，自动化状态保持 `automation_local_ready`，绝不记为 `automation_active`。
- 未调用真实图片、视频、PPT、剪映、LLM 或其他付费 API。
- 独立终审无 Critical、Important 或 Minor 阻塞；四条远端基线在候选验收后复核未变化。

## 后续门禁基线修复

- 开发者于 2026-07-24 明确批准一次性单槽集成，把门禁与后端基线修复分支纳入长期 Agent；该批准不改变普通模块的 9.10A 人工触发规则。
- 候选从 `origin/feature/agent_0.8.4_boguan@5826c741180b58c9e8d3cdbbcb092d38e5f04b0d` 创建；冻结 dev 为 `fb7450775a227d891372c19eae1b308045c51e68`，已是 Agent 祖先；修复源为 `1aba4ae9e4670930fd456519d8ecc7d4cef39880`。
- 项目虚拟环境为 Python 3.12.13。候选首轮 Pester `45 passed, 0 failed`、M13 Final `8/8`；独立审核发现普通模块门禁漏测后，按 TDD 得到 `44 passed, 3 failed` 的预期红灯，再完成修复并达到 `47 passed, 0 failed`，复跑 M13 Final 仍为 `8/8`。
- 审核加固后，M01 在 M01.5 Event Outbox 权威清单完成前 fail-closed；M07/M12 运行 `pnpm test`、lint 和生产构建；M08–M11 在后端权威清单冻结前 fail-closed；M03 的 Pester 合同精确断言项目 Python 3.12 检查命令。
- 产品已确认不保留缺失的旧 Docker/provisioner/Sandbox 能力，未恢复对应文件；当前 `LocalSandboxProvider`、Gateway Dockerfile、公开 Skill 根目录和 `/mnt/...` 虚拟路径能力均保留。
- 没有 migration、配置、content-app API、生产运行模式、intent 范围或 Feature Flag 变更；未调用真实图片、视频、PPT、剪映、LLM 或其他付费 API；自动化状态仍为 `automation_local_ready`。
- 完整集成证据见 `docs/agentization/test-reports/2026-07-24-agent-gate-baseline-repair-integration.md`；本次只修复共享基线，不修改 M03 模块状态。

## 下一步

- M00 已完成且门禁基线已修复，无硬阻塞。恢复原 M03 模块分支执行 M03.4 真实 Final 门禁；绿色后由该切片写 `ready_for_integration` 并 push，再由开发者复制 9.10A 话术手动启动 M03 单槽集成。
- 后续阶段/最终单槽集成和 dev→agent 漂移检查继续由开发者按执行手册人工触发仓库脚本；未来实际部署并验收远端 CI 后，才能提升为 `automation_active`。
- 生产运行模式、`enabled_intents`、真实付费冒烟及 Agent→dev 收口仍需人工明确批准；M00 不授权发布。
