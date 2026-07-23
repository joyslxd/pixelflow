# M00 合同、分支自动化、feature flag 与测试入口

- phase：`in_progress`
- owner：A（后端合同/自动化）+ B（前端镜像合同）
- reviewer：A/B 互审
- base SHA：`8e626ae232d984f14fa9954b672b4e025894d426`
- branches：
  - A 线：`codex/agent-0.8.4-m00-a`，远端 HEAD `89cf1ff4dfcd7dd73f1c471935f00c149a7093ef`
  - B 线：`codex/agent-0.8.4-m00-b`，远端 HEAD `efadb5d48a9c81655332acb2369918c5af88db27`
  - 首次集成：按需创建临时 `codex/integrate-m00-YYYYMMDD-HHMM`
- contract/design base SHA：`8e626ae232d984f14fa9954b672b4e025894d426`；M00-A/M00-B 已证明从同一 SHA 创建
- 当前切片：`M00-I.1`，按 D-008 和执行手册 9.5 重新创建候选
- feature flag：尚未实现；设计要求默认 `off`
- 文件锁：A 只写后端合同/fixture、`scripts/agentization/**` 及其测试；B 只写 TypeScript 镜像合同、前端合同测试和 web 测试入口；两线分别更新自己的状态文件

## 切片

- [x] M00-A.1 characterization tests（A，2h）
- [x] M00-A.2 Python DTO/Ports/fakes/规范 fixture（A，3h；依赖 A.1）
- [x] M00-A.3 dev→agent 同步、模块分支/worktree、单槽集成和中文提交/注释/配置说明门禁脚本（A，3h；依赖 A.2）
- [x] M00-B.1 TypeScript 镜像合同、wire event 与 web 测试入口（B，2.5h；与 A 线并行，只读 `contracts-v1.md`）
- [ ] M00-I.1 首次 M00 集成、跨端合同、中文工程规范、本地单槽门禁和 `automation_local_ready` 验收（A+B 评审，单一集成人写入，3h；A/B 两线完成后由开发者手动启动）

## 当前记录

- 已完成：设计级合同、模块拆分、测试矩阵、M00-A.1–M00-A.3 和 M00-B.1；A/B 两条远端分支均为 `ready_for_integration`。
- 尚未完成：M00-I.1 尚未进入长期 Agent；上一条候选仅作为 blocked 证据，不得直接合入。
- 下一步第一动作：复制执行手册 9.5 修订话术，按最新四条远端引用创建新的 M00-I.1 候选。
- 已知风险：Python/TS 字段命名必须在 `M00-I.1` 用 A 线规范 fixture 做跨端验证；B 线不得创建第二权威 fixture。
- 固定集成顺序：`最新 Agent + 最新 dev → M00-A → M00-A定向测试 → M00-B → M00跨端合同/M00范围全量/flag-off/本地自动化门禁`。临时集成候选只有一个写入者；不得运行 M02 定向集合或 M13 后端仓库全量。
- Pester 兼容：候选必须在 Windows PowerShell 5.1 + Pester 3.4 执行；数组成员检查使用 `-contains` 布尔断言，禁止把 Pester 3.4 的文件断言 `Should Contain` 用于数组。
- 自动化状态：A 线脚本已达到 `automation_local_ready`，但 M00 整体尚未集成，整体状态仍为 `design_only`；M00-I.1 本地候选门禁绿色后把整体更新为 `automation_local_ready` 即可完成 M00。当前没有 Jenkins 或其他远端 CI，不新增无效 `Jenkinsfile`，也不得写 `automation_active`。
- 启动例外：M00-I.1 由开发者手动启动一次。后续 M01–M12 的阶段/最终集成和 dev→agent 漂移检查继续由开发者按执行手册人工触发仓库脚本；未来实际部署并验收远端 CI 后，才改为无人值守触发。
