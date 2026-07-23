# M00-A 门禁启用前历史精确豁免实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精确豁免中文门禁启用前的 `0af72ff6993e9e67636f21e8e16d641411702d67` 提交及仍由该提交拥有的英文行，同时保持后续英文提交、注释和 docstring fail-closed。

**Architecture:** 中文策略内部维护不可由调用方扩展的完整 SHA 映射。提交信息检查只跳过精确 SHA；行级检查通过 `git blame --porcelain` 判断当前 `HeadRef` 中的行是否仍由该提交拥有，无法证明来源时拒绝。状态恢复只在共同基线全门禁通过后进行。

**Tech Stack:** Windows PowerShell 5.1、Pester 3.4、Git、现有 `scripts/agentization` 自动化。

## Global Constraints

- 不 rebase、不 force-push，不改变 M00-A.1、A.2、A.3 既有 SHA。
- 不修改 `backend/tests/test_agent_runtime_legacy_invariants.py`、`web/**`、M00-B 或两个长期 feature 分支。
- 白名单只包含完整 SHA `0af72ff6993e9e67636f21e8e16d641411702d67`，不接受调用参数扩展。
- 新增或修改的注释、脚本说明、状态、测试记录和 commit 必须使用中文。
- 自动化最高仍为 `automation_local_ready`；本任务不启动 M00-I.1。
- 不调用真实图片、视频、PPT、剪映、LLM 或其他付费 API。

---

### Task 1: 用完整 SHA 与 blame 精确豁免门禁前历史

**Files:**
- Modify: `scripts/agentization/tests/ChineseEngineeringPolicy.Tests.ps1`
- Modify: `scripts/agentization/Test-ChineseEngineeringPolicy.ps1`

**Interfaces:**
- Consumes: `Invoke-AgentGit -RepositoryPath <path> -Arguments <string[]>`；策略参数 `RepositoryPath/BaseRef/HeadRef` 保持不变。
- Produces: 内部映射 `$GrandfatheredCommits`、`Get-AddedLineEntries`、`Get-LineOriginCommit`、`Test-GrandfatheredLine`；不增加公开豁免参数。

- [ ] **Step 1: 增加真实历史与后续修改 RED 测试**

在 `ChineseEngineeringPolicy.Tests.ps1` 顶部增加仓库根目录：

```powershell
$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $AgentizationRoot)
```

在 `Describe "中文工程规范门禁"` 中增加：

```powershell
It "只豁免门禁启用前的精确提交及其原始行" {
    $result = & $PolicyScript `
        -RepositoryPath $RepositoryRoot `
        -BaseRef "8e626ae232d984f14fa9954b672b4e025894d426" `
        -HeadRef "0af72ff6993e9e67636f21e8e16d641411702d67"

    $result.Passed | Should Be $true
}

It "豁免提交中的英文行被后续修改后重新拒绝" {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("pixelflow-grandfather-policy-" + [guid]::NewGuid().ToString("N"))
    $script:TemporaryRoots += $root
    & git clone --local $RepositoryRoot $root | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("config", "user.name", "历史豁免测试") | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("config", "user.email", "grandfather-tests@example.invalid") | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("checkout", "0af72ff6993e9e67636f21e8e16d641411702d67") | Out-Null
    $path = Join-Path $root "backend\tests\test_agent_runtime_legacy_invariants.py"
    $content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    $content = $content.Replace(
        "Characterization tests for v2 behavior that the Agent runtime must preserve.",
        "Updated English explanation."
    )
    [System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)
    Invoke-TestGit -RepositoryPath $root -Arguments @("add", "backend/tests/test_agent_runtime_legacy_invariants.py") | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("commit", "-m", "测试：修改历史英文说明") | Out-Null

    { & $PolicyScript -RepositoryPath $root -BaseRef "8e626ae232d984f14fa9954b672b4e025894d426" -HeadRef "HEAD" } | Should Throw
}
```

后续修改测试还要把新提交写入仓库 `blame.ignoreRevsFile`，证明门禁不受忽略修订配置影响；另加独立用例，把相同英文原文复制到新路径后仍要求策略抛出该路径的违规。

- [ ] **Step 2: 运行测试并确认 RED 原因**

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$result = Invoke-Pester -Script "scripts\agentization\tests\ChineseEngineeringPolicy.Tests.ps1" -PassThru
if ($result.FailedCount -eq 0) { throw "RED 测试未失败" }
```

Expected: “只豁免门禁启用前的精确提交及其原始行”失败，错误同时包含 `0af72ff` 英文标题和英文 docstring；现有新英文提交拒绝测试仍通过。

- [ ] **Step 3: 增加最小完整 SHA 白名单**

在 `Test-ChineseEngineeringPolicy.ps1` 加载公共 helper 后增加：

```powershell
# 用途：兼容中文门禁启用前已审核并推送的历史；影响：只跳过精确 SHA，后续提交仍执行全部中文检查。
$GrandfatheredCommits = @{
    "0af72ff6993e9e67636f21e8e16d641411702d67" = "M00-A.1 创建时尚未启用中文工程门禁，现经用户明确批准保留历史。"
}
```

提交信息循环改为：

```powershell
foreach ($commit in $commits) {
    if ($GrandfatheredCommits.ContainsKey($commit)) {
        continue
    }
    $subject = (Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "-s", "--format=%s", $commit)).Output -join "`n"
    $body = (Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "-s", "--format=%b", $commit)).Output -join "`n"
    if (-not (Test-AgentContainsChinese -Text $subject)) {
        $violations.Add("提交 $commit 的标题缺少中文主体语义：$subject")
    }
    if (-not [string]::IsNullOrWhiteSpace($body) -and -not (Test-AgentContainsChinese -Text $body)) {
        $violations.Add("提交 $commit 的正文缺少中文主体语义。")
    }
}
```

- [ ] **Step 4: 为新增行建立行号与 blame 来源接口**

用下列函数替换只返回行号的 `Get-AddedLineNumbers`，并保留兼容包装：

```powershell
function Get-AddedLineEntries {
    param([string[]]$DiffLines)

    $entries = New-Object System.Collections.Generic.List[object]
    $currentLine = 0
    $insideHunk = $false
    foreach ($line in $DiffLines) {
        if ($line -match "^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@") {
            $currentLine = [int]$matches[1]
            $insideHunk = $true
            continue
        }
        if (-not $insideHunk -or $line.StartsWith("+++")) {
            continue
        }
        if ($line.StartsWith("+")) {
            $entries.Add([pscustomobject]@{ LineNumber = $currentLine; Text = $line.Substring(1) })
            $currentLine++
        }
        elseif ($line.StartsWith("-")) {
            continue
        }
        elseif (-not $line.StartsWith("\")) {
            $currentLine++
        }
    }
    return $entries.ToArray()
}

function Get-AddedLineNumbers {
    param([string[]]$DiffLines)
    return @((Get-AddedLineEntries -DiffLines $DiffLines) | ForEach-Object { $_.LineNumber })
}

function Get-LineOriginCommit {
    param(
        [string]$RepositoryPath,
        [string]$HeadRef,
        [string]$RelativePath,
        [int]$LineNumber
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @(
        "blame", "--ignore-revs-file=", "--porcelain", $HeadRef, "-L", "$LineNumber,$LineNumber", "--", $RelativePath
    ) -AllowFailure
    if ($result.ExitCode -ne 0 -or $result.Output.Count -eq 0) {
        return $null
    }
    $origin = ($result.Output[0] -split "\s+")[0].TrimStart("^")
    if ($origin -notmatch "^[0-9a-fA-F]{40}$") {
        return $null
    }
    return $origin.ToLowerInvariant()
}

function Test-GrandfatheredLine {
    param(
        [string]$RepositoryPath,
        [string]$HeadRef,
        [string]$RelativePath,
        [int]$LineNumber
    )

    $origin = Get-LineOriginCommit -RepositoryPath $RepositoryPath -HeadRef $HeadRef -RelativePath $RelativePath -LineNumber $LineNumber
    return $origin -and $GrandfatheredCommits.ContainsKey($origin)
}
```

- [ ] **Step 5: 把人工注释和 docstring 违规绑定到行级来源**

代码注释循环改为遍历 `Get-AddedLineEntries`：

```powershell
$addedEntries = @(Get-AddedLineEntries -DiffLines $diff.Output)
foreach ($entry in $addedEntries) {
    $comment = Get-AddedCommentText -Line $entry.Text -Extension $extension
    if ($null -eq $comment -or [string]::IsNullOrWhiteSpace($comment)) {
        continue
    }
    if ((Test-MachineDirectiveComment -Comment $comment) -or (Test-AgentContainsChinese -Text $comment)) {
        continue
    }
    if ($comment -match "[A-Za-z]{2,}" -and -not (Test-GrandfatheredLine -RepositoryPath $root -HeadRef $HeadRef -RelativePath $relativePath -LineNumber $entry.LineNumber)) {
        $violations.Add("人工注释缺少中文说明：$relativePath 第 $($entry.LineNumber) 行 -> $comment")
    }
}
```

docstring 违规条件增加同样的来源判断：

```powershell
if (
    $docstringText -match "[A-Za-z]{2,}" -and
    -not (Test-AgentContainsChinese -Text $docstringText) -and
    -not (Test-GrandfatheredLine -RepositoryPath $root -HeadRef $HeadRef -RelativePath $relativePath -LineNumber $lineNumber)
) {
    $violations.Add("docstring 缺少中文说明：$relativePath 第 $lineNumber 行")
}
```

- [ ] **Step 6: 运行中文门禁测试并确认 GREEN**

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$result = Invoke-Pester -Script "scripts\agentization\tests\ChineseEngineeringPolicy.Tests.ps1" -PassThru
if ($result.FailedCount -gt 0) { exit 1 }
```

Expected: 全部测试通过；测试数量比实施前增加 3，定向结果为 `12 passed, 0 failed`。

- [ ] **Step 7: 运行完整 Pester 回归**

Run:

```powershell
$result = Invoke-Pester -Script "scripts\agentization\tests" -PassThru
if ($result.FailedCount -gt 0) { exit 1 }
```

Expected: 分支自动化与中文门禁全部通过，`0 failed`。

---

### Task 2: 恢复 M00-A 模块状态并完成交付

**Files:**
- Modify: `docs/agentization/status/M00-A-status.md`
- Modify: `docs/agentization/status/M00-A.3-status.md`
- Modify: `docs/agentization/test-reports/M00-A.3.md`
- Modify: `docs/agentization/M00-A-pre-policy-grandfather-design.md` only if implementation differs from approved design
- Track: `docs/agentization/plans/2026-07-23-m00-a-pre-policy-grandfather.md`

**Interfaces:**
- Consumes: Task 1 的全历史中文策略结果、M00-A 模块门禁。
- Produces: `phase=ready_for_integration` 的 M00-A 状态和可审计测试记录；不触发 M00-I.1。

- [ ] **Step 1: 请求独立只读审核**

审核范围：

```text
BASE: 57ccadd5325ce27052cc6fe87e5922f92b6fe8a6
HEAD: 当前工作区
重点：白名单是否只含完整 0af SHA；调用方能否扩展；blame 失败是否 fail-closed；ignore-revs 配置、复制或修改英文行能否绕过；状态是否过早 ready。
```

Expected: reviewer 明确“未发现本地 P0/P1”；如有 P0/P1，增加 RED 测试并修复后重新审核。

- [ ] **Step 2: 运行提交前验证**

Run:

```powershell
$errors = $null
Get-ChildItem "scripts\agentization" -Recurse -Filter "*.ps1" | ForEach-Object {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { $errors += $parseErrors }
}
if ($errors) { throw "PowerShell AST 检查失败" }
git diff --check

& ".\scripts\agentization\Test-ChineseEngineeringPolicy.ps1" `
    -RepositoryPath "." `
    -BaseRef "8e626ae232d984f14fa9954b672b4e025894d426" `
    -HeadRef "HEAD"
```

Run from `backend`:

```powershell
& "E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe" -m pytest `
    tests/test_agent_runtime_contracts.py `
    tests/test_agent_runtime_legacy_invariants.py `
    tests/test_openapi_operation_ids.py `
    tests/test_harness_boundary.py `
    tests/test_pixelflow_conversations_router.py `
    tests/test_pixelflow_task_store.py -q

& "E:\IntelliJIDEA\secondWorkSpaces\cmyqCode\pixelflow\backend\.venv\Scripts\python.exe" -m ruff check `
    pixelflow/agent_runtime `
    tests/test_agent_runtime_contracts.py `
    tests/test_agent_runtime_legacy_invariants.py
```

Expected: AST、whitespace 与共同基线中文门禁通过；后端 `65 passed`，Ruff `All checks passed`。

- [ ] **Step 3: 创建不含状态恢复的中文实现 commit**

```powershell
git add -- `
    scripts/agentization/Test-ChineseEngineeringPolicy.ps1 `
    scripts/agentization/tests/ChineseEngineeringPolicy.Tests.ps1 `
    docs/agentization/M00-A-pre-policy-grandfather-design.md `
    docs/agentization/plans/2026-07-23-m00-a-pre-policy-grandfather.md

git commit -m "修复(M00-A)：精确豁免门禁启用前历史" `
    -m "只放过完整 0af72ff 提交及仍由其 blame 拥有的英文行；显式清除 ignore-revs 配置影响，后续英文变更继续 fail-closed。"
```

Expected: commit 成功，标题和正文均含中文；M00-A 状态此时仍保持 `blocked`。

- [ ] **Step 4: 在状态恢复前运行完整模块门禁**

```powershell
& ".\scripts\agentization\Test-AgentBranchPolicy.ps1" `
    -RepositoryPath "." `
    -ModuleId "M00-A" `
    -ExpectedWriter "尚未领取" `
    -ExpectedBaseSha "8e626ae232d984f14fa9954b672b4e025894d426" `
    -BaseRef "57ccadd5325ce27052cc6fe87e5922f92b6fe8a6" `
    -RequireClean `
    -RunChinesePolicy

& ".\scripts\agentization\Invoke-AgentModuleGate.ps1" `
    -RepositoryPath "." `
    -ModuleId "M00-A" `
    -GateType "Final" `
    -ChinesePolicyBaseRef "8e626ae232d984f14fa9954b672b4e025894d426"
```

Expected: 分支策略、共同基线中文策略和 M00-A 完整模块门禁全部通过；失败时保持 `blocked` 并停止。

- [ ] **Step 5: 更新状态和测试记录**

在 `M00-A-status.md` 中：

```markdown
- phase：`ready_for_integration`
- 当前切片：`M00-A.3`（已完成）
- 当前唯一写入者：尚未领取
- 下一步第一动作：等待 M00-B.1 完成后，由开发者手动启动唯一集成人执行 `M00-I.1`
- 硬阻塞：无；`0af72ff...` 已按批准设计作为门禁启用前精确历史豁免，不改写历史。
```

在 `M00-A.3-status.md` 和 `M00-A.3.md` 追加：

```markdown
- 历史兼容修订：完整 SHA `0af72ff...` 获用户明确批准；只放过该提交和仍由其 blame 拥有的英文行。
- 防回退：后续英文提交与后续修改原行均继续失败。
```

- [ ] **Step 6: 创建独立中文状态 commit**

```powershell
git add -- `
    docs/agentization/status/M00-A-status.md `
    docs/agentization/status/M00-A.3-status.md `
    docs/agentization/test-reports/M00-A.3.md

git commit -m "文档(M00-A)：恢复模块待集成状态" `
    -m "记录门禁前历史精确豁免、独立审核与完整模块门禁证据；M00-A 停在 ready_for_integration，不启动 M00-I.1。"
```

Expected: commit 成功，标题和正文均含中文。

- [ ] **Step 7: 运行状态提交后的完整门禁**

Run:

```powershell
& ".\scripts\agentization\Test-AgentBranchPolicy.ps1" `
    -RepositoryPath "." `
    -ModuleId "M00-A" `
    -ExpectedWriter "尚未领取" `
    -ExpectedBaseSha "8e626ae232d984f14fa9954b672b4e025894d426" `
    -BaseRef "57ccadd5325ce27052cc6fe87e5922f92b6fe8a6" `
    -RequireClean `
    -RunChinesePolicy

& ".\scripts\agentization\Invoke-AgentModuleGate.ps1" `
    -RepositoryPath "." `
    -ModuleId "M00-A" `
    -GateType "Final" `
    -ChinesePolicyBaseRef "8e626ae232d984f14fa9954b672b4e025894d426"
```

Expected: 分支策略、共同基线中文策略和 M00-A 完整模块门禁全部通过。

- [ ] **Step 8: push 模块分支并核对远端**

```powershell
$localHead = (git rev-parse HEAD).Trim()
git push origin codex/agent-0.8.4-m00-a
$remoteHead = ((git ls-remote --heads origin refs/heads/codex/agent-0.8.4-m00-a) -split "\s+")[0]
if ($remoteHead -ne $localHead) { throw "远端 M00-A 与本地 HEAD 不一致" }
```

同时核对：

```text
origin/feature/agent_0.8.4_boguan = dbcdbc9e156abd5e964a9028cd44aaf0cdff4714
origin/feature/dev_0.8.4_boguan   = 02493711e8c9b74ec5f8e54cfadac3881297754c
```

Expected: 只更新 `origin/codex/agent-0.8.4-m00-a`；worktree 清洁；停止，不启动 M00-I.1。
