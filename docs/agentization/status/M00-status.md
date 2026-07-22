# M00 合同、分支自动化、feature flag 与测试入口

- phase：`ready`
- owner：A（后端合同/自动化）+ B（前端镜像合同）
- reviewer：A/B 互审
- base SHA：`02493711e8c9b74ec5f8e54cfadac3881297754c`
- branches：
  - A 线：计划 `codex/agent-0.8.4-m00-a`
  - B 线：计划 `codex/agent-0.8.4-m00-b`
  - 首次集成：按需创建临时 `codex/integrate-m00-YYYYMMDD-HHMM`
- contract/design base SHA：待本设计包进入同步后的 Agent 基线后记录；M00-A/M00-B 必须从同一 SHA 创建
- 当前切片：A 可领取 `M00-A.1`，B 可同时领取 `M00-B.1`
- feature flag：尚未实现；设计要求默认 `off`
- 文件锁：A 只写后端合同/fixture、`scripts/agentization/**` 及其测试；B 只写 TypeScript 镜像合同、前端合同测试和 web 测试入口；两线分别更新自己的状态文件

## 切片

- [ ] M00-A.1 characterization tests（A，2h）
- [ ] M00-A.2 Python DTO/Ports/fakes/规范 fixture（A，3h；依赖 A.1）
- [ ] M00-A.3 dev→agent 同步、模块分支/worktree、单槽集成和中文提交/注释/配置说明门禁脚本（A，3h；依赖 A.2）
- [ ] M00-B.1 TypeScript 镜像合同、wire event 与 web 测试入口（B，2.5h；与 A 线并行，只读 `contracts-v1.md`）
- [ ] M00-I.1 首次 M00 集成、跨端合同、中文工程规范、Gitee/Jenkins 门禁和自动化验收（A+B 评审，单一集成人写入，3h；A/B 两线完成后由开发者手动启动）

## 当前记录

- 已完成：设计级合同、模块拆分和测试矩阵。
- 尚未完成：任何业务代码、fixture、脚本或测试实现。
- 下一步第一动作：分别使用运行手册的 M00-A/M00-B 首次话术启动 `M00-A.1`、`M00-B.1`；两条线各自按状态文件串行并由开发者逐切片手动继续。
- 已知风险：Python/TS 字段命名必须在 `M00-I.1` 用 A 线规范 fixture 做跨端验证；B 线不得创建第二权威 fixture。
- 固定集成顺序：`最新 Agent + 最新 dev → M00-A → 定向测试 → M00-B → 跨端/全量/flag-off/自动化门禁`。临时集成候选只有一个写入者。
- 自动化状态：`design_only`；PowerShell 脚本、Gitee/Jenkins 保护分支、最后一片触发自动集成和每日 02:00 调度尚未实现，不能视为已启用。
- 启动例外：M00 自身是自动化的引导模块，因此 `M00-I.1` 需要开发者手动启动一次；M00 验收并完成一次性管理员配置后，M01–M12 的普通模块集成和每日漂移检查才可无人值守运行。
