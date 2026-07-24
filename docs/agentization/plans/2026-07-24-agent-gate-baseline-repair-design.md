# Agent 模块门禁与后端基线修复设计

## 1. 背景与目标

M03.4 的定向测试、范围内 Ruff 和独立审核均已通过，但 `Invoke-AgentModuleGate.ps1`
把 M01–M06 的 Final 门禁错误地实现为后端全量测试，并使用 PATH 中未限定版本的
`python`。这与 `docs/agentization/test-matrix.md` 中“普通模块运行模块最小测试集，
后端全量只在 M13 执行”的合同冲突，也让当前 Windows 环境误用全局 Python 3.13，
而不是项目虚拟环境 Python 3.12。

本次修复目标如下：

1. 普通后端模块只运行权威模块测试集和对应静态检查；M13 保留后端全量门禁。
2. 所有后端模块门禁统一使用仓库虚拟环境解释器，并验证其为 Python 3.12。
3. 退役已经确认不属于 PixelFlow 产品范围、且仓库从未提供实现文件的
   Docker/provisioner/Sandbox 诊断脚本测试。
4. 将仍属于当前产品的鉴权、Skill、虚拟路径和文件安全测试对齐到
   content-app Authorization、`backend/skills/public` 和 Windows 本地开发环境。
5. 清理后端全量 Ruff 基线，为后续 M13 全量门禁保留真实而可执行的测试集合。

## 2. 边界

### 2.1 本次包含

- `scripts/agentization/Invoke-AgentModuleGate.ps1` 的后端模块命令规划。
- `scripts/agentization/tests/BranchAutomation.Tests.ps1` 的门禁规划回归测试。
- 已确认废弃能力对应的测试文件和仅服务这些测试的 fixture。
- content-app 鉴权迁移后仍残留的本地登录、管理员初始化、cookie/CSRF 旧合同测试。
- Skill 根目录定位、Windows 虚拟路径、符号链接权限和确定性文件写入失败测试。
- 当前全量测试暴露的少量真实业务回归，以及 14 个 Ruff 基线错误。

### 2.2 本次不包含

- 不恢复 `Makefile`、`docker/dev-entrypoint.sh`、`docker/provisioner/app.py`、
  `scripts/sandbox_memory_profile.py` 或其他已确认无需保留的基础设施。
- 不恢复 PixelFlow 本地登录、注册、管理员初始化或 cookie session。
- 不删除仍被 PixelFlow Agent/Skill 运行时使用的 LocalSandboxProvider、虚拟
  `/mnt/...` 路径和 `backend/skills/public`。
- 不直接修改 `feature/dev_0.8.4_boguan` 或
  `feature/agent_0.8.4_boguan`，不把自动化修复混入 M03 模块提交。
- 不调用真实图片、视频、PPT、剪映或 LLM 付费接口。

## 3. 设计

### 3.1 门禁命令规划

门禁脚本通过仓库已有的 `Resolve-AgentPythonExecutable` 解析解释器。M01–M06 和
M13 的 pytest、Ruff 命令都使用该绝对路径，不再依赖 PATH。脚本在执行 Python
门禁前运行轻量版本检查，要求解释器主次版本为 `3.12`；版本不符时 fail-closed，
错误信息同时输出实际解释器路径和版本。

M03 使用已经在状态文件中验证的八个测试文件：

- `tests/test_agent_runtime_context_externalizer.py`
- `tests/test_agent_runtime_context_assembler.py`
- `tests/test_agent_runtime_token_meter.py`
- `tests/test_agent_runtime_context_profiles.py`
- `tests/test_agent_runtime_contracts.py`
- `tests/test_agent_runtime_config.py`
- `tests/test_profile_config.py`
- `tests/test_pixelflow_memory_helper.py`

M01 使用当前模块分支新增的 runtime persistence/CAS/Inbox 测试，以及现有
conversation/task store/Jianying 原子 patch 回归。尚未完成的 M02、M04、M05、M06
不得回退到全量 pytest；在各模块提供权威测试清单前，门禁明确 fail-closed。
M13 继续运行后端全量 pytest，但同样固定使用项目 Python 3.12。

Pester 测试直接检查 `-PlanOnly` 返回的命令对象，证明：

- M03 包含全部八个权威测试且不存在裸 `pytest -q`。
- M01 包含自身测试且不包含 M03 测试。
- M02/M04/M05/M06 在清单未建立时明确拒绝执行。
- M13 保留全量 pytest。
- 所有后端命令使用仓库虚拟环境解释器。

### 3.2 废弃测试退役

下列测试只验证仓库从未提供、且产品已确认不需要的实现，因此删除而不是补空文件：

- `backend/tests/test_dev_entrypoint.py`
- `backend/tests/test_provisioner_kubeconfig.py`
- `backend/tests/test_provisioner_pvc_volumes.py`
- `backend/tests/test_sandbox_memory_profile_script.py`

同时从 `backend/tests/conftest.py` 删除只负责加载
`docker/provisioner/app.py` 的 fixture。删除前通过单测错误和 `git log --all`
证明目标实现从未进入当前仓库；删除后通过 `rg` 保证没有遗留引用。

`test_gateway_runtime_cleanup.py` 不整体删除。它同时覆盖当前网关、CORS 和 API
路由合同；仅退役依赖仓库不存在的 DeerFlow 根级部署文件的断言，保留并调整能映射
到当前 PixelFlow 文件结构的测试。

### 3.3 content-app 鉴权合同

提交 `456fa33` 已声明本地登录体系为 breaking removal，但部分测试只替换了 URL
前缀，仍验证本地登录、cookie 和管理员初始化。修复时删除以下过期测试：

- `test_initialize_admin.py`
- `test_ensure_admin.py`
- `test_auth_type_system.py` 中仅属于本地 JWT/cookie/CSRF 的部分
- `test_langgraph_auth.py` 中仅属于旧 cookie provider 的部分

保留或补充 content-app 合同测试，覆盖：

- 缺少或非法 `Authorization` 时返回结构化 401。
- AuthMiddleware 已解析用户时复用 `request.state.user`。
- `/agent/auth/me` 不创建本地 session，不设置登录 cookie。
- runtime、setup-agent 和 thread/run E2E 使用完整 content-app 用户结构与
  Authorization 测试替身，不通过恢复旧登录接口绕过鉴权。

生产代码若仍存在与正式设计冲突的本地 cookie fallback，先用失败测试证明当前
content-app-only 合同，再删除 fallback；不为了保留过期测试恢复废弃功能。

### 3.4 Skill 与 Windows 可移植性

`backend/skills/public` 是 PixelFlow 实际 Skill 根目录。Skill frontmatter 和
客户端列表测试统一定位该目录，不再读取不存在的仓库根 `skills/public`。

对外暴露的虚拟路径固定使用 POSIX `/mnt/...` 格式。路径转换实现使用
`PurePosixPath` 或显式分隔符归一化，不把 Windows `\` 泄漏给 Agent。符号链接
安全测试先探测当前 Windows 是否具备创建链接权限；缺少开发者模式/管理员权限时
只跳过链接创建场景，不能跳过普通路径逃逸测试。POSIX chmod 位测试在 Windows
改为验证调用意图或按平台跳过，不把 NTFS 权限模型误判为产品缺陷。

大 tool 输出外置的失败测试不再依赖 `/nonexistent` 这类平台相关路径，而是通过
“父路径是普通文件”或最小 monkeypatch 制造确定性写入失败，继续验证真实 fallback
行为。

### 3.5 Ruff 与真实剩余回归

先运行 Ruff 的安全自动修复处理 import 排序和标准库导入升级，再人工拆分超长
Prompt 字符串，确保语义不变。全量 pytest 在前三类修复后重新聚类；只修复仍能在
Python 3.12、当前产品合同下稳定复现的行为问题，不用删除测试掩盖真实回归。

## 4. 测试策略

每个行为变更都遵循 Red-Green-Refactor：

1. 先补 Pester/Pytest 回归并观察预期红灯。
2. 进行单一最小修复。
3. 运行定向测试转绿。
4. 每个修复组结束后运行相关回归集合。
5. 最终运行项目 Python 3.12 下的后端全量 pytest、全量 Ruff、Pester、
   `git diff --check` 和中文工程规范门禁。

不调用真实外部供应商。LLM/付费测试继续按现有条件跳过。

## 5. 交付与集成

所有改动位于独立分支
`codex/agent-0.8.4-m00-gate-baseline-repair` 和独立 worktree。完成后进行只读
独立审核，使用中文独立提交并 push。由于当前自动化状态为
`automation_local_ready`，本任务不直接写两个长期 feature 分支；修复分支进入
Agent 后，再基于最新 Agent 恢复 M03 Final 门禁。只有修正后的 M03 门禁全绿，
才允许把 M03 状态改为 `ready_for_integration` 并启动 9.10A 单槽集成。

## 6. 完成标准

- 门禁规划 Pester 全绿，M03/M13 范围与测试矩阵一致。
- 所有后端命令实际使用项目虚拟环境 Python 3.12。
- 已废弃 Docker/provisioner/Sandbox 脚本没有测试或 fixture 遗留引用。
- M03 八文件集合保持 `119 passed`。
- 后端全量 pytest 在 Python 3.12 下零失败、零错误。
- `ruff check pixelflow app/gateway tests` 零错误。
- `git diff --check`、中文工程规范门禁和独立审核通过。
