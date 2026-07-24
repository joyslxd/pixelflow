# Agent 模块门禁与后端基线修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正普通模块门禁范围并固定项目 Python 3.12，退役确认废弃的 Docker/Sandbox 测试，使当前产品合同下的后端全量测试与 Ruff 恢复绿色。

**Architecture:** 自动化层以模块权威测试清单替代 M01–M06 裸全量测试，并从主 worktree 的 `backend/.venv` 解析 Python 3.12。测试基线层删除无实现、无产品需求的基础设施合同；其余失败按 content-app 鉴权、实际 Skill 根目录和 POSIX 虚拟路径合同修复。

**Tech Stack:** Windows PowerShell 5.1、Pester 3.4、Python 3.12、pytest、Ruff、FastAPI、DeerFlow harness。

## Global Constraints

- 只在 `codex/agent-0.8.4-m00-gate-baseline-repair` 与独立 worktree 修改。
- 不直接修改 `feature/dev_0.8.4_boguan` 或 `feature/agent_0.8.4_boguan`。
- 不恢复缺失的 Docker/provisioner/Sandbox 诊断脚本。
- 不恢复本地登录、管理员初始化、cookie session 或旧 CSRF 登录态。
- 本机验证统一设置
  `$python = "E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe"`；
  禁止改用 PATH 中的 Python。
- 所有新增/修改人工注释、脚本说明、状态和提交信息使用中文。
- 不调用真实 LLM 或图片、视频、PPT、剪映供应商。
- 每个行为修复先观察测试红灯，再写最小实现转绿。

---

### Task 1: 固定模块门禁范围和 Python 3.12

**Files:**
- Modify: `scripts/agentization/Agentization.Common.ps1:47-79`
- Modify: `scripts/agentization/Invoke-AgentModuleGate.ps1:72-104`
- Modify: `scripts/agentization/tests/BranchAutomation.Tests.ps1:343-371`

**Interfaces:**
- Consumes: `Resolve-AgentRepositoryRoot()`、`Invoke-GateProcess()`、模块 ID。
- Produces: 只返回项目虚拟环境解释器的 `Resolve-AgentPythonExecutable()`；M01/M03 定向门禁计划；M13 全量门禁计划。

- [ ] **Step 1: 写 Pester 红灯测试**

在 `BranchAutomation.Tests.ps1` 增加以下断言：

```powershell
It "M03 最终门禁只运行权威定向测试并使用项目 Python" {
    $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") `
        -RepositoryPath $RepositoryRoot -ModuleId "M03" -GateType "Final" -PlanOnly)
    $pythonPath = Resolve-TestRepositoryPython -RepositoryPath $RepositoryRoot
    $pytest = @($plan | Where-Object { $_.Arguments -contains "pytest" })
    $allArguments = @($pytest[0].Arguments) -join " "

    $pytest.Count | Should Be 1
    $pytest[0].FilePath | Should Be $pythonPath
    ($allArguments -match "test_agent_runtime_context_externalizer.py") | Should Be $true
    ($allArguments -match "test_pixelflow_memory_helper.py") | Should Be $true
    @($pytest[0].Arguments).Count | Should BeGreaterThan 3
}

It "M13 保留后端全量门禁但不使用 PATH Python" {
    $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") `
        -RepositoryPath $RepositoryRoot -ModuleId "M13" -GateType "Final" -PlanOnly)
    $pytest = @($plan | Where-Object { $_.Arguments -contains "pytest" })

    @($pytest[0].Arguments) -join " " | Should Match "^-m pytest -q$"
    $pytest[0].FilePath | Should Not Be "python"
}
```

并补充 M01 自身测试清单、M02/M04/M05/M06 无清单时 fail-closed、缺少项目 venv
不允许退回 PATH Python 的测试。

- [ ] **Step 2: 运行 Pester 并确认预期失败**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command `
  "$r=Invoke-Pester -Script 'scripts/agentization/tests/BranchAutomation.Tests.ps1' -PassThru; if($r.FailedCount -gt 0){exit 1}"
```

Expected: 新增测试因 M03 仍返回裸 `pytest -q`、FilePath 仍为 `python` 而失败。

- [ ] **Step 3: 最小修改 Python resolver**

删除 PATH fallback，只接受主 worktree 或当前 worktree 的项目虚拟环境：

```powershell
foreach ($candidateRoot in $candidateRoots) {
    foreach ($relativePath in @("backend\.venv\Scripts\python.exe", "backend/.venv/bin/python")) {
        $candidate = Join-Path $candidateRoot $relativePath
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
}
throw "未找到项目 Python 虚拟环境：请先创建 backend/.venv；模块门禁禁止回退到 PATH Python。"
```

- [ ] **Step 4: 最小修改门禁计划**

为后端模块统一加入版本检查：

```powershell
$commands.Add([pscustomobject]@{
    WorkingDirectory = (Join-Path $root "backend")
    FilePath = $pythonExecutable
    Arguments = @(
        "-c",
        "import sys; print(sys.version); raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
    )
})
```

M03 pytest 参数使用设计文档中的八个文件，Ruff 限定到
`pixelflow/agent_runtime/context` 和对应测试。M01 使用当前模块新增的四个
runtime persistence 测试和 task/conversation/Jianying 回归。M02/M04/M05/M06
在权威清单尚未建立时抛出中文异常，不得回退全量。M13 使用项目解释器运行
`-m pytest -q` 和 `-m ruff check pixelflow app/gateway tests`。

- [ ] **Step 5: 运行 Pester 转绿**

Run: 与 Step 2 相同。

Expected: `FailedCount = 0`。

- [ ] **Step 6: 提交**

```powershell
git add scripts/agentization
git commit -m "修复(M00)：限定模块门禁与 Python 版本"
```

---

### Task 2: 退役无产品需求的 Docker/Sandbox 测试

**Files:**
- Delete: `backend/tests/test_dev_entrypoint.py`
- Delete: `backend/tests/test_provisioner_kubeconfig.py`
- Delete: `backend/tests/test_provisioner_pvc_volumes.py`
- Delete: `backend/tests/test_sandbox_memory_profile_script.py`
- Modify: `backend/tests/conftest.py:1-70`
- Modify: `backend/tests/test_gateway_runtime_cleanup.py`

**Interfaces:**
- Consumes: 用户确认的产品范围和 Git 历史证据。
- Produces: 不再收集无实现基础设施合同；仍保留当前 Gateway/CORS/API 路由回归。

- [ ] **Step 1: 固定退役前证据**

Run:

```powershell
git log --all --oneline -- Makefile docker/dev-entrypoint.sh docker/provisioner/app.py scripts/sandbox_memory_profile.py
& $python -m pytest `
  tests/test_dev_entrypoint.py `
  tests/test_provisioner_kubeconfig.py `
  tests/test_provisioner_pvc_volumes.py `
  tests/test_sandbox_memory_profile_script.py -q
```

Expected: Git 历史没有实现文件；测试稳定失败于 FileNotFound。

- [ ] **Step 2: 删除过期测试与专用 fixture**

从 `conftest.py` 删除 `provisioner_module` fixture 及其只服务
`docker/provisioner/app.py` 的 import。删除四个过期测试文件。

- [ ] **Step 3: 收紧 Gateway cleanup 测试**

删除读取缺失根 `Makefile`、根 `scripts/*.sh`、`docker/**`、旧 `frontend/**`
和 `.agent/**` 的断言；保留对当前以下文件的合同：

```python
CURRENT_GATEWAY_FILES = (
    "backend/app/gateway/config.py",
    "backend/app/gateway/app.py",
    "backend/app/gateway/csrf_middleware.py",
)
```

继续验证 `GATEWAY_CORS_ORIGINS`、统一 Gateway runtime 和不暴露 2024 端口等可从
当前仓库证明的行为。

- [ ] **Step 4: 验证无遗留引用**

Run:

```powershell
rg -n "provisioner_module|docker/provisioner/app.py|sandbox_memory_profile.py|docker/dev-entrypoint.sh" backend/tests
& $python -m pytest tests/test_gateway_runtime_cleanup.py -q
```

Expected: `rg` 无匹配；保留的 Gateway 测试全绿。

- [ ] **Step 5: 提交**

```powershell
git add -A backend/tests
git commit -m "测试：退役无产品需求的基础设施合同"
```

---

### Task 3: 对齐 content-app Authorization 鉴权

**Files:**
- Modify: `backend/app/gateway/deps.py:222-296`
- Modify: `backend/app/gateway/routers/auth.py`
- Delete: `backend/tests/test_initialize_admin.py`
- Delete: `backend/tests/test_ensure_admin.py`
- Modify/Delete: `backend/tests/test_auth_type_system.py`
- Modify/Delete: `backend/tests/test_langgraph_auth.py`
- Modify: `backend/tests/test_content_app_auth.py`
- Modify: `backend/tests/test_content_app_auth_middleware.py`
- Modify: `backend/tests/test_runtime_lifecycle_e2e.py`
- Modify: `backend/tests/test_setup_agent_http_e2e_real_server.py`

**Interfaces:**
- Consumes: `Authorization: Bearer <content-app-jwt>` 和 `request.state.user`。
- Produces: content-app-only CurrentUser resolver；旧 cookie 永远不能认证。

- [ ] **Step 1: 写 direct resolver 红灯**

```python
@pytest.mark.asyncio
async def test_current_user_resolver_ignores_legacy_cookie():
    request = SimpleNamespace(
        state=SimpleNamespace(user=None),
        headers={},
        cookies={"access_token": "legacy-token"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_from_request(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "not_authenticated"
```

再增加 `/agent/auth/me` 只复用 `request.state.user`、不设置 cookie 的路由测试。

- [ ] **Step 2: 运行新增测试确认 cookie fallback 红灯**

Run:

```powershell
& $python -m pytest `
  tests/test_content_app_auth.py `
  tests/test_content_app_auth_middleware.py -q
```

Expected: direct resolver 测试进入旧 JWT/local provider 路径而失败。

- [ ] **Step 3: 删除生产兼容 fallback**

`get_current_user_from_request()` 只允许两条路径：

```python
if cached_user is not None and not isinstance(cached_user, Mock):
    return cached_user
if authorization is None:
    raise HTTPException(
        status_code=401,
        detail=AuthErrorResponse(
            code=AuthErrorCode.NOT_AUTHENTICATED,
            message="Unauthorized",
        ).model_dump(),
    )
```

Authorization 存在时调用 `authenticate_authorization_header()` 并把
`ContentAppAuthError` 映射为结构化错误。删除 `get_local_provider()` 和 auth Router
中的注册 DTO、密码规则、登录限流兼容代码，只保留 `/agent/auth/me`。

- [ ] **Step 4: 退役旧测试并迁移 E2E fixture**

删除管理员初始化和本地用户迁移测试。旧 auth type/langgraph auth 文件中若无
content-app 合同则整体删除，否则只保留通用结构化错误断言。runtime/setup-agent
E2E 为请求注入完整 `ContentAppUser` 或可验证 Authorization 替身。

- [ ] **Step 5: 运行鉴权相关回归**

Run:

```powershell
& $python -m pytest `
  tests/test_content_app_auth.py `
  tests/test_content_app_auth_middleware.py `
  tests/test_auth_middleware.py `
  tests/test_runtime_lifecycle_e2e.py `
  tests/test_setup_agent_http_e2e_real_server.py -q
```

Expected: 全绿，且 cookie 仍返回 401。

- [ ] **Step 6: 提交**

```powershell
git add -A backend/app/gateway backend/tests
git commit -m "修复：统一 content-app Authorization 鉴权合同"
```

---

### Task 4: 修复 Skill 根目录与 Windows 虚拟路径

**Files:**
- Modify: `backend/tests/test_skills_bundled.py`
- Modify: `backend/tests/test_client_e2e.py`
- Modify: `backend/packages/harness/deerflow/config/paths.py`
- Modify: `backend/packages/harness/deerflow/sandbox/local/local_sandbox_provider.py`
- Modify: `backend/packages/harness/deerflow/tools/sandbox_search.py`
- Modify: corresponding sandbox/path tests

**Interfaces:**
- Consumes: 实际 Skill 根 `backend/skills/public`、宿主 Windows 路径。
- Produces: Agent 可见路径始终为 `/mnt/...` POSIX 形式。

- [ ] **Step 1: 写/收紧路径红灯**

```python
def test_windows_nested_path_is_rendered_as_posix_virtual_path(tmp_path):
    local_file = tmp_path / "workspace" / "pkg" / "util.py"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("ok", encoding="utf-8")

    assert provider.reverse_resolve_path(str(local_file)) == "/mnt/user-data/workspace/pkg/util.py"
```

Skill 测试把根目录固定为：

```python
SKILLS_PUBLIC_DIR = Path(__file__).resolve().parents[1] / "skills" / "public"
```

- [ ] **Step 2: 运行当前失败集合确认红灯**

Run:

```powershell
& $python -m pytest `
  tests/test_skills_bundled.py `
  tests/test_client_e2e.py::TestConfigManagement::test_list_skills_returns_list `
  tests/test_local_sandbox_provider_mounts.py `
  tests/test_local_sandbox_virtual_path_contract.py `
  tests/test_sandbox_search_tools.py -q
```

Expected: Skill 空列表或虚拟路径含 `\`。

- [ ] **Step 3: 最小实现 POSIX 虚拟路径**

所有宿主相对路径先拆分为 path parts，再用 `PurePosixPath` 拼接：

```python
virtual = PurePosixPath(mount.virtual_path).joinpath(*relative.parts)
return virtual.as_posix()
```

不对宿主实际访问路径强制改为 POSIX；只规范返回给 Agent 的虚拟路径。

- [ ] **Step 4: 处理 Windows 能力差异**

符号链接测试通过共享 helper 尝试创建最小链接；仅当 `OSError.winerror == 1314`
时 `pytest.skip("当前 Windows 未启用符号链接权限")`。POSIX mode 测试在
`os.name == "nt"` 时跳过 chmod 位断言。普通路径逃逸和覆盖保护测试继续执行。

- [ ] **Step 5: 运行路径与文件安全回归**

Run:

```powershell
& $python -m pytest `
  tests/test_skills_bundled.py `
  tests/test_client_e2e.py `
  tests/test_local_sandbox_provider_mounts.py `
  tests/test_local_sandbox_virtual_path_contract.py `
  tests/test_local_skill_storage_write.py `
  tests/test_sandbox_search_tools.py `
  tests/test_skill_permissions.py `
  tests/test_uploads_router.py -q
```

Expected: 支持场景通过；无符号链接权限的测试明确 skip。

- [ ] **Step 6: 提交**

```powershell
git add backend/packages/harness backend/tests
git commit -m "修复：统一 Skill 根目录与 Windows 虚拟路径"
```

---

### Task 5: 修复确定性文件失败测试和真实剩余回归

**Files:**
- Modify: `backend/tests/test_tool_output_budget_middleware.py`
- Modify: `backend/tests/test_detect_blocking_io_static.py`
- Modify: 根据重新聚类确定的当前产品源文件与测试

**Interfaces:**
- Consumes: 跨平台临时目录和当前 API 合同。
- Produces: 不依赖 Unix 路径假设的确定性错误测试；剩余真实回归零失败。

- [ ] **Step 1: 把无效路径测试改成确定性红灯**

```python
def test_returns_none_when_output_parent_is_a_file(tmp_path):
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    result = middleware._externalize(
        content="payload",
        outputs_path=str(blocked_parent / "child"),
        tool_name="test",
    )

    assert result is None
```

fallback 测试使用同一不可创建子目录场景，不再传 `/tmp/test` 或
`/nonexistent/...`。

- [ ] **Step 2: 修复静态扫描器路径断言**

期望值统一通过 `Path(...).as_posix()`，只比较稳定 schema 字段，不把 Windows
临时目录分隔符当作扫描器业务结果。

- [ ] **Step 3: 运行后端全量并重新聚类**

Run:

```powershell
& $python -m pytest -q --tb=short
```

Expected: 已知三大类失败消失；记录仍失败的测试名和首个 traceback。

- [ ] **Step 4: 对每个剩余根因执行单独 Red-Green**

每个剩余根因必须：

1. 先单测稳定复现。
2. 判断测试是否符合当前产品合同。
3. 符合则修生产代码；过期则用现有替代测试证明覆盖后退役。
4. 定向集合转绿后才处理下一根因。

- [ ] **Step 5: 提交**

```powershell
git add backend
git commit -m "修复：关闭后端剩余基线回归"
```

---

### Task 6: 清理 Ruff 基线

**Files:**
- Modify: `backend/app/gateway/auth/__init__.py`
- Modify: `backend/app/gateway/deps.py`
- Modify: `backend/app/gateway/routers/auth.py`
- Modify: `backend/pixelflow/creative/asset_manifest.py`
- Modify: `backend/pixelflow/creative/plan_llm.py`
- Modify: `backend/pixelflow/tracing/conversation_trace.py`
- Modify: `backend/tests/test_setup_wizard.py`

**Interfaces:**
- Consumes: Ruff 0.14.11 规则。
- Produces: `ruff check pixelflow app/gateway tests` 零错误且语义不变。

- [ ] **Step 1: 记录 Ruff 红灯**

Run:

```powershell
& $python -m ruff check pixelflow app/gateway tests --output-format concise
```

Expected: import 排序、UP012、E501、UP035 等已知错误。

- [ ] **Step 2: 运行安全自动修复**

Run:

```powershell
& $python -m ruff check `
  app/gateway/auth/__init__.py `
  app/gateway/deps.py `
  app/gateway/routers/auth.py `
  pixelflow/creative/asset_manifest.py `
  pixelflow/tracing/conversation_trace.py `
  tests/test_setup_wizard.py --fix
```

- [ ] **Step 3: 人工拆分 plan_llm 长行**

只把长 Prompt 字符串拆成相邻字符串字面量或多行括号表达式，不修改文本内容。

- [ ] **Step 4: 全量 Ruff 转绿**

Run:

```powershell
& $python -m ruff check pixelflow app/gateway tests
```

Expected: `All checks passed!`

- [ ] **Step 5: 提交**

```powershell
git add backend
git commit -m "样式：清理后端 Ruff 基线"
```

---

### Task 7: 最终验证、审核与交付

**Files:**
- Modify: `docs/agentization/plans/2026-07-24-agent-gate-baseline-repair-implementation.md`
- Create: `docs/agentization/test-reports/2026-07-24-agent-gate-baseline-repair.md`

**Interfaces:**
- Consumes: Tasks 1–6 的全部提交。
- Produces: 可独立审核、可推送、可供 Agent 单槽集成的修复分支。

- [ ] **Step 1: 运行 Pester**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command `
  "$r=Invoke-Pester -Script 'scripts/agentization/tests' -PassThru; if($r.FailedCount -gt 0){exit 1}"
```

- [ ] **Step 2: 运行 M03 权威定向集合**

```powershell
& $python -m pytest `
  tests/test_agent_runtime_context_externalizer.py `
  tests/test_agent_runtime_context_assembler.py `
  tests/test_agent_runtime_token_meter.py `
  tests/test_agent_runtime_context_profiles.py `
  tests/test_agent_runtime_contracts.py `
  tests/test_agent_runtime_config.py `
  tests/test_profile_config.py `
  tests/test_pixelflow_memory_helper.py -q
```

Expected: `119 passed`。

- [ ] **Step 3: 运行后端全量与 Ruff**

```powershell
& $python -m pytest -q
& $python -m ruff check pixelflow app/gateway tests
```

Expected: pytest 零失败、零错误；Ruff `All checks passed!`。

- [ ] **Step 4: 运行差异与中文工程规范**

```powershell
git diff --check origin/feature/agent_0.8.4_boguan..HEAD
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts/agentization/Test-ChineseEngineeringPolicy.ps1 `
  -RepositoryPath . `
  -BaseRef origin/feature/agent_0.8.4_boguan `
  -HeadRef HEAD
```

- [ ] **Step 5: 写中文测试报告并提交**

报告记录解释器绝对路径、Python 版本、Pester、M03 定向、后端全量、Ruff、中文门禁
和跳过测试原因。

```powershell
git add docs/agentization
git commit -m "文档：记录门禁与后端基线修复结论"
```

- [ ] **Step 6: 请求只读独立审核**

审核范围为 `5826c741..HEAD`，要求检查：

- 模块范围是否与 test-matrix 一致。
- 是否误删当前产品仍需要的测试。
- content-app-only 鉴权是否 fail-closed。
- Windows skip 是否只限平台能力而非业务失败。
- 全量测试证据是否可复现。

- [ ] **Step 7: 处理 Critical/Important 后重新验证并 push**

```powershell
git push -u origin codex/agent-0.8.4-m00-gate-baseline-repair
```

修复分支进入 Agent 前不得修改 M03 为 `ready_for_integration`；进入 Agent 后恢复
M03 模块候选并重新执行修正后的 Final 门禁。
