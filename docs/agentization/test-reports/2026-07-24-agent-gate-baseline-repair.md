# Agent 门禁与后端基线修复报告

## 结论

- 修复分支：`codex/agent-0.8.4-m00-gate-baseline-repair`
- 基线：`origin/feature/agent_0.8.4_boguan@5826c741180b58c9e8d3cdbbcb092d38e5f04b0d`
- Python：项目虚拟环境 Python 3.12.13
- Docker/provisioner/旧 Sandbox memory profile：产品已确认不再保留，未恢复缺失文件；对应过期测试已退役。
- 修复分支后端全量测试、Pester、Ruff 和中文工程规范均为绿色。
- M03 模块分支按修正版权威清单运行 119 项测试及定向 Ruff，结果绿色。
- 当前仍为 `automation_local_ready`。修复分支进入长期 Agent 分支后，必须由开发者手动启动单槽集成，再在包含修复提交和 M03 提交的候选上执行真实 M03 Final 门禁。

## 根因与修复

| 根因 | 修复 |
| --- | --- |
| 后端模块门禁误用宽泛范围，且可回退到 PATH Python | 为 M01、M03 建立权威测试清单；M02/M04/M05/M06 未配置时 fail-closed；所有后端门禁强制使用项目虚拟环境并校验 Python 3.12 |
| M01 Ruff 未覆盖模块拥有的 harness 持久化迁移与模型文件 | 把 migrations/versions 与 persistence models 纳入 M01 精确 Ruff 参数合同 |
| M13 聚合门禁只构建前端，且后端 Ruff 不是全量 | M13 固定运行后端全量 pytest、`ruff check .`、前端合同测试、前端全量测试、lint 与生产构建 |
| Docker/provisioner/Sandbox memory profile 文件已删除，但旧测试仍要求其存在 | 删除四组过期基础设施合同测试，保留当前 Gateway Dockerfile 与 CORS 合同测试 |
| 网关仍残留本地 Cookie 注册、登录与用户表兼容合同 | 网关统一使用 content-app `Authorization`；运行时 E2E 在 content-app Client 边界替换外部鉴权，保留真实认证传播链 |
| Skill 扫描指向仓库根目录的不存在路径 | 统一到 `backend/skills/public`，并让测试优先加载当前 worktree 的 harness 源码 |
| Windows 路径反向映射泄露宿主路径或产生反斜杠虚拟路径 | 使用路径对象做包含判断，所有虚拟路径统一输出 POSIX 分隔符；Windows 优先使用可用 PowerShell，子进程按 UTF-8 解码 |
| Windows 未授权创建符号链接，且不提供可验证的 POSIX chmod 语义 | 仅在 `WinError 1314` 时跳过符号链接测试；POSIX 权限位测试在 Windows 条件跳过，其他异常仍失败 |
| 内部系统身份覆盖渠道传入的真实用户 | `inject_authenticated_user_context()` 对 `system_role=internal` 保留已有终端用户 |
| 剪映终态任务依赖低分辨率墙钟排序 | 增加锁内单调完成序号，按真实完成顺序回收最早终态任务 |
| 测试依赖 `/nonexistent` 不可写、Windows 墙钟分辨率和原生路径分隔符 | 改为父路径是文件的确定性写失败、显式时间戳和 POSIX 输出断言 |
| 后端全量 Ruff 存在 14 个历史问题 | 清理导入顺序、过时类型来源、无用导入和超长提示词行 |

## TDD 与定向验证

- Docker/provisioner/Sandbox 旧合同红灯：27 项失败、24 项错误，均指向已确认退役的缺失文件。
- content-app 鉴权红灯：旧 Cookie 可绕过当前合同；修复后鉴权与运行时回归 `122 passed`。
- Skill、路径和上传回归：`174 passed, 26 skipped`。
- 权限与 Skill 写入回归：`76 passed, 5 skipped`。
- 后端剩余 8 个失败项修复后，相关模块回归：`262 passed`。
- Ruff 相关行为回归：`146 passed`。

## 最终验证

```text
项目 Python
Python 3.12.13

后端全量 pytest
4209 passed, 48 skipped, 7 warnings

Agentization Pester
45 passed, 0 failed

后端全量 Ruff
All checks passed

M01 模块扩展 Ruff
All checks passed

前端 Agent runtime 合同
9 passed, 0 failed

前端全量测试
214 passed, 0 failed

前端 lint
tsc --noEmit 通过

前端生产构建
build-prod 通过

中文工程规范
Passed=True
CommitCount=10
ChangedPathCount=43

M03 模块分支权威 pytest
119 passed, 1 warning

M03 定向 Ruff
All checks passed
```

现存 7 条 pytest warning 均来自第三方弃用提示或测试用短 JWT key，不是本次失败原因。

## 门禁拓扑说明

修复分支基于长期 Agent 分支，而 M03.1–M03.4 仍只存在于
`codex/agent-0.8.4-m03-context-runtime`。因此：

1. 在修复分支直接执行 M03 Final 门禁会因 M03 测试文件尚未进入该分支而 fail-closed；
2. 在 M03 分支运行同一权威测试清单与 Ruff 已绿色；
3. Pester 已验证新门禁精确生成这 8 个测试文件和定向 Ruff 命令；
4. 只有单槽候选同时包含修复分支与 M03 分支后，才能形成最终有效的真实 M03 Final 门禁证据。

不得据此提前修改 M03 状态为 `ready_for_integration`。集成候选绿色后，再由集成流程写入对应状态。

## 独立审核闭环

独立 reviewer 首轮没有发现 Critical，提出 3 个 Important：

1. M13 缺少前端测试/lint 且 Ruff 非全量；
2. M01 Ruff 漏掉 harness 持久化文件；
3. Pester 只验证测试文件“被包含”，没有验证参数集合精确相等。

上述问题已全部修复并通过红绿 TDD：旧实现的新增 Pester 合同为 31 通过、2 失败；修复后全部
45 项通过。最终增量复核不得存在未关闭的 Critical 或 Important。
