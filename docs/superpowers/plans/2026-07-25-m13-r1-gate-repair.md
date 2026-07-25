# M13.1 / R1 门禁入口修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增兼容 Windows PowerShell 5.1 的 M13.1 / R1 固定门禁入口，并把 M13 从失败关闭状态一致恢复为可重新触发状态。

**Architecture:** 固定入口只接收候选仓库路径，读取 fetch 后冻结的 `origin/feature/agent_0.8.4_boguan` 跟踪引用作为中文工程门禁基线，再调用候选内 canonical `Invoke-AgentModuleGate.ps1`。公共集成器保持不变，状态恢复和修复证据只写入 M13 模块分支。

**Tech Stack:** Windows PowerShell 5.1、Pester 3.4、Git、Markdown。

## Global Constraints

- 只修复 M13.1 / R1，不执行 `Integrate-AgentModule.ps1`，不创建或复用集成候选。
- 不执行 M13.2 或其他模块门禁，不调用真实付费 API。
- Agent、dev、BOARD、MERGE_LOG 和生产配置保持不变。
- 自动化状态保持 `automation_local_ready`，不得写为 `automation_active`。
- 新增 PowerShell 脚本必须使用 UTF-8 BOM、CRLF 和中文人工注释。
- Git commit、状态、测试报告和交接记录必须使用中文。

---

### Task 1: 固定 M13.1 / R1 门禁调用合同

**Files:**
- Create: `scripts/agentization/Invoke-M13R1PhaseGate.ps1`
- Modify: `scripts/agentization/tests/BranchAutomation.Tests.ps1`

**Interfaces:**
- Consumes: `RepositoryPath: string`、可选 `PlanOnly: switch`、候选仓库内 `refs/remotes/origin/feature/agent_0.8.4_boguan`。
- Produces: 对 `Invoke-AgentModuleGate.ps1` 的固定调用：`ModuleId=M13`、`GateType=Phase`、`ReleaseId=R1`、`Slice=M13.1`、`ChinesePolicyBaseRef=<冻结 Agent SHA>`。

- [ ] **Step 1: 写固定入口缺失时会失败的 Pester 合同**

在 `$RequiredScripts` 增加 `"Invoke-M13R1PhaseGate.ps1"`，并在 M13 门禁测试附近增加：

```powershell
It "M13 R1 固定入口绑定冻结 Agent 和唯一阶段参数" {
    $topology = New-AutomationTestTopology
    $fakeGateDirectory = Join-Path $topology.Repository "scripts\agentization"
    New-Item -ItemType Directory -Path $fakeGateDirectory -Force | Out-Null
    $fakeGate = @'
param(
    [string]$RepositoryPath,
    [string]$ModuleId,
    [string]$GateType,
    [string]$ReleaseId,
    [string]$Slice,
    [string]$ChinesePolicyBaseRef,
    [switch]$PlanOnly
)
[pscustomobject]@{
    RepositoryPath = $RepositoryPath
    ModuleId = $ModuleId
    GateType = $GateType
    ReleaseId = $ReleaseId
    Slice = $Slice
    ChinesePolicyBaseRef = $ChinesePolicyBaseRef
    PlanOnly = [bool]$PlanOnly
}
'@
    Set-Content -LiteralPath (Join-Path $fakeGateDirectory "Invoke-AgentModuleGate.ps1") -Value $fakeGate -Encoding UTF8
    $expectedAgent = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

    $result = & (Join-Path $AgentizationRoot "Invoke-M13R1PhaseGate.ps1") -RepositoryPath $topology.Repository -PlanOnly

    $result.ModuleId | Should Be "M13"
    $result.GateType | Should Be "Phase"
    $result.ReleaseId | Should Be "R1"
    $result.Slice | Should Be "M13.1"
    $result.ChinesePolicyBaseRef | Should Be $expectedAgent
    $result.PlanOnly | Should Be $true
}
```

- [ ] **Step 2: 运行 RED 并确认失败原因**

Run:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; `$r=Invoke-Pester -Script 'scripts/agentization/tests/BranchAutomation.Tests.ps1' -TestName 'M13 R1 固定入口绑定冻结 Agent 和唯一阶段参数' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }"
```

Expected: FAIL，原因是 `Invoke-M13R1PhaseGate.ps1` 不存在。

- [ ] **Step 3: 实现最小固定入口**

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 用途：把 M13.1 / R1 单槽集成固定到唯一获批的阶段门禁；执行后会运行 M13 全量非付费命令。
$rootOutput = & git -C $RepositoryPath rev-parse --show-toplevel 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "无法解析 M13 R1 候选仓库。"
}
$root = [System.IO.Path]::GetFullPath(@($rootOutput)[-1].Trim())

# 用途：读取本次 fetch 冻结的 Agent 基线；缺失或不是候选祖先时立即停止，禁止猜测或回退旧 SHA。
$agentReference = "refs/remotes/origin/feature/agent_0.8.4_boguan"
$agentOutput = & git -C $root rev-parse --verify "$agentReference^{commit}" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "缺少冻结 Agent 跟踪引用：$agentReference"
}
$agentSha = @($agentOutput)[-1].Trim()
if ($agentSha -notmatch "^[0-9a-fA-F]{40}$") {
    throw "冻结 Agent 跟踪引用不是合法提交 SHA。"
}
& git -C $root merge-base --is-ancestor $agentSha HEAD
if ($LASTEXITCODE -ne 0) {
    throw "冻结 Agent 不是当前 M13 R1 候选的祖先。"
}

$moduleGateScript = Join-Path $root "scripts/agentization/Invoke-AgentModuleGate.ps1"
if (-not (Test-Path -LiteralPath $moduleGateScript -PathType Leaf)) {
    throw "候选缺少 canonical 模块门禁脚本。"
}
$gateParameters = @{
    RepositoryPath = $root
    ModuleId = "M13"
    GateType = "Phase"
    ReleaseId = "R1"
    Slice = "M13.1"
    ChinesePolicyBaseRef = $agentSha
}
if ($PlanOnly) {
    $gateParameters["PlanOnly"] = $true
}
& $moduleGateScript @gateParameters
```

使用编码规范化命令把脚本保存为 UTF-8 BOM + CRLF。

- [ ] **Step 4: 增加 fail-closed 和编码回归测试**

增加一个测试删除测试仓库中的 Agent 跟踪引用，并断言固定入口抛错；读取脚本前三个字节断言 `EF BB BF`，并确认测试进程是 Windows PowerShell 5.1。

- [ ] **Step 5: 运行 GREEN 和完整 Pester**

先运行 Step 2 的单测试，Expected: PASS。再显式设置 UTF-8 控制台运行完整文件：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "[Console]::InputEncoding=New-Object Text.UTF8Encoding(`$false); [Console]::OutputEncoding=New-Object Text.UTF8Encoding(`$false); `$OutputEncoding=New-Object Text.UTF8Encoding(`$false); `$r=Invoke-Pester -Script 'scripts/agentization/tests/BranchAutomation.Tests.ps1' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }"
```

Expected: 所有测试通过，新增测试不少于 2 项。

### Task 2: 恢复 M13 可重试状态并记录证据

**Files:**
- Modify: `docs/agentization/status/M13-status.md`
- Create: `docs/agentization/test-reports/M13-R1-gate-repair.md`

**Interfaces:**
- Consumes: 原阻塞提交 `5f444442b05f073b74d9a691aaae06fbf32e0f07`、原 checkpoint `e4eb45838d20bf110841aa360f24d699b32ead3d`。
- Produces: `phase=ready_for_phase_integration`、`checkpoint_status=ready`、`last_integrated_commit=—`，以及下一次必须使用新固定入口和全新候选的中文证据。

- [ ] **Step 1: 一致恢复状态字段和停止点**

只修改 M13 状态文件，把阻塞字段恢复为 ready，并把旧的“当前停止点”统一为下一次任务使用 `Invoke-M13R1PhaseGate.ps1` 重新触发；保留 checkpoint commit 和生产未变说明。

- [ ] **Step 2: 写中文修复报告**

记录根因、TDD RED/GREEN、Pester 数量、UTF-8 BOM/CRLF、Agent/dev/生产未变、未执行集成和付费 API，以及历史失败候选不得复用。

- [ ] **Step 3: 校验状态合同**

Run:

```powershell
$content = Get-Content -LiteralPath 'docs/agentization/status/M13-status.md' -Raw -Encoding UTF8
$content | Select-String 'phase：`ready_for_phase_integration`'
$content | Select-String 'checkpoint_status：`ready`'
$content | Select-String 'last_integrated_commit：`—`'
```

Expected: 三项均匹配，且不存在权威阻塞字段。

### Task 3: 完成前验证、中文提交和 push

**Files:**
- Verify all changed files from Tasks 1–2.

**Interfaces:**
- Consumes: 当前修复 worktree。
- Produces: 已 push 的 `codex/agent-0.8.4-m13-integration` 修复提交，供下一次 M13.1 / R1 任务重新冻结。

- [ ] **Step 1: 运行格式和中文工程门禁**

```powershell
git diff --check 5f444442b05f073b74d9a691aaae06fbf32e0f07..HEAD
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/agentization/Test-ChineseEngineeringPolicy.ps1 -RepositoryPath . -BaseRef 5f444442b05f073b74d9a691aaae06fbf32e0f07 -HeadRef HEAD
```

- [ ] **Step 2: 验证外部边界未变化**

比较远端 Agent、dev、BOARD、MERGE_LOG、`backend/config.prod.yml`，确认与修复前冻结值一致；确认未出现 `automation_active` 状态更新。

- [ ] **Step 3: 提交实现与状态**

```powershell
git add scripts/agentization/Invoke-M13R1PhaseGate.ps1 scripts/agentization/tests/BranchAutomation.Tests.ps1 docs/agentization/status/M13-status.md docs/agentization/test-reports/M13-R1-gate-repair.md
git commit -m "修复(M13)：固定 R1 阶段门禁入口" -m "增加 Windows PowerShell 5.1 编码回归合同，恢复 M13.1/R1 可重新触发状态；未执行单槽集成。"
```

- [ ] **Step 4: push 前复验并推送**

重新运行完整 Pester、中文工程门禁和 `git status --short`，然后：

```powershell
git push origin codex/agent-0.8.4-m13-integration
```

Expected: 远端 M13 指向修复提交，Agent/dev 仍保持冻结值。
