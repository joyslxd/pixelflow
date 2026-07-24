Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AgentizationRoot = Split-Path -Parent $PSScriptRoot
$RepositoryRoot = Split-Path -Parent (Split-Path -Parent $AgentizationRoot)
$RequiredScripts = @(
    "Test-AgentBranchPolicy.ps1",
    "Test-ChineseEngineeringPolicy.ps1",
    "Sync-DevToAgent.ps1",
    "Start-AgentModule.ps1",
    "Invoke-AgentModuleGate.ps1",
    "Integrate-AgentModule.ps1",
    "Reconcile-DevToAgent.ps1"
)
$script:TemporaryRoots = @()

function Invoke-TestGit {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & git -C $RepositoryPath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "测试仓库 Git 命令失败：git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return @($output | ForEach-Object { "$_" })
}

function New-AutomationTestTopology {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("pixelflow-agentization-git-" + [guid]::NewGuid().ToString("N"))
    $remote = Join-Path $root "remote.git"
    $repository = Join-Path $root "repository"
    New-Item -ItemType Directory -Path $root | Out-Null
    $script:TemporaryRoots += $root

    & git init --bare $remote | Out-Null
    & git init $repository | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("config", "user.name", "分支自动化测试") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("config", "user.email", "branch-tests@example.invalid") | Out-Null
    Set-Content -LiteralPath (Join-Path $repository "README.md") -Value "自动化测试仓库" -Encoding UTF8
    Invoke-TestGit -RepositoryPath $repository -Arguments @("add", "README.md") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("commit", "-m", "初始化：建立自动化测试仓库") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("branch", "feature/dev_0.8.4_boguan") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("branch", "feature/agent_0.8.4_boguan") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("remote", "add", "origin", $remote) | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("push", "origin", "feature/dev_0.8.4_boguan", "feature/agent_0.8.4_boguan") | Out-Null
    Invoke-TestGit -RepositoryPath $repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null

    $passGate = Join-Path $root "Pass-Gate.ps1"
    $failGate = Join-Path $root "Fail-Gate.ps1"
    Set-Content -LiteralPath $passGate -Encoding UTF8 -Value 'param([string]$RepositoryPath) if (-not (Test-Path -LiteralPath $RepositoryPath)) { throw "候选目录不存在" }'
    Set-Content -LiteralPath $failGate -Encoding UTF8 -Value 'param([string]$RepositoryPath) throw "模拟门禁失败"'

    return @{
        Root = $root
        Remote = $remote
        Repository = $repository
        PassGate = $passGate
        FailGate = $failGate
    }
}

function Add-DevCommit {
    param([hashtable]$Topology)

    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "feature/dev_0.8.4_boguan") | Out-Null
    Set-Content -LiteralPath (Join-Path $Topology.Repository "dev-change.txt") -Value ([guid]::NewGuid().ToString("N")) -Encoding UTF8
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", "dev-change.txt") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "测试：增加日常分支变更") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("push", "origin", "feature/dev_0.8.4_boguan") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null
}

function Add-ModuleCommit {
    param(
        [hashtable]$Topology,
        [string]$Phase = "ready_for_integration"
    )

    $branch = "codex/agent-0.8.4-m01-runtime-store"
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "-b", $branch, "feature/agent_0.8.4_boguan") | Out-Null
    $statusDirectory = Join-Path $Topology.Repository "docs\agentization\status"
    New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
    $status = "# M01 状态`n`n- phase：``$Phase```n- branch：``$branch```n- 当前唯一写入者：尚未领取`n- 当前切片：``M01.5```n- last_integrated_commit：—`n"
    Set-Content -LiteralPath (Join-Path $statusDirectory "M01-status.md") -Value $status -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $Topology.Repository "module-change.txt") -Value "模块变更" -Encoding UTF8
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md", "module-change.txt") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "实现：增加 M01 测试模块变更") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("push", "-u", "origin", $branch) | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null
    return $branch
}

function Add-PhaseModuleCommit {
    param(
        [hashtable]$Topology,
        [string]$StatusSlice = "M12.3"
    )

    $branch = "codex/agent-0.8.4-m12-workspace-ui"
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "-b", $branch, "feature/agent_0.8.4_boguan") | Out-Null
    Set-Content -LiteralPath (Join-Path $Topology.Repository "module-change.txt") -Value "阶段模块变更" -Encoding UTF8
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", "module-change.txt") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "实现：增加 M12 阶段变更") | Out-Null
    $checkpointCommit = @(Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("rev-parse", "HEAD"))[-1]
    $statusDirectory = Join-Path $Topology.Repository "docs\agentization\status"
    New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
    $status = "# M12 状态`n`n- phase：``ready_for_phase_integration```n- branch：``$branch```n- 当前唯一写入者：尚未领取`n- 当前切片：``$StatusSlice```n- release_id：``R1```n- checkpoint_slice：``$StatusSlice```n- checkpoint_commit：``$checkpointCommit```n- checkpoint_status：``ready```n- last_integrated_commit：—`n"
    Set-Content -LiteralPath (Join-Path $statusDirectory "M12-status.md") -Value $status -Encoding UTF8
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", "docs/agentization/status/M12-status.md") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "状态：登记 M12 阶段检查点") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("push", "-u", "origin", $branch) | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null
    return $branch
}

function Add-M13PhaseCheckpoint {
    param(
        [hashtable]$Topology,
        [string]$ReleaseId,
        [string]$Slice,
        [switch]$ReuseBranch,
        [switch]$StatusOnly
    )

    $branch = "codex/agent-0.8.4-m13-integration"
    if ($ReuseBranch) {
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("fetch", "origin", $branch) | Out-Null
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("branch", "-f", $branch, "origin/$branch") | Out-Null
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", $branch) | Out-Null
    }
    else {
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "-b", $branch, "feature/agent_0.8.4_boguan") | Out-Null
    }

    $statusPath = Join-Path $Topology.Repository "docs\agentization\status\M13-status.md"
    $lastIntegratedCommit = "—"
    if (Test-Path -LiteralPath $statusPath) {
        $existingStatus = [System.IO.File]::ReadAllText($statusPath, [System.Text.Encoding]::UTF8)
        $lastIntegratedMatch = [regex]::Match($existingStatus, "(?m)^- last_integrated_commit：``?([^`\r\n]+)")
        if ($lastIntegratedMatch.Success) {
            $lastIntegratedCommit = $lastIntegratedMatch.Groups[1].Value.Trim()
        }
    }

    if ($StatusOnly) {
        $checkpointCommit = $lastIntegratedCommit
    }
    else {
        $changePath = Join-Path $Topology.Repository ("m13-" + $ReleaseId.ToLowerInvariant() + ".txt")
        Set-Content -LiteralPath $changePath -Value "$ReleaseId 阶段模块变更" -Encoding UTF8
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", ([System.IO.Path]::GetFileName($changePath))) | Out-Null
        Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "实现：增加 M13 $ReleaseId 阶段变更") | Out-Null
        $checkpointCommit = @(Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("rev-parse", "HEAD"))[-1]
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $statusPath) -Force | Out-Null
    $status = "# M13 状态`n`n- phase：``ready_for_phase_integration```n- branch：``$branch```n- 当前唯一写入者：尚未领取`n- 当前切片：``$Slice```n- release_id：``$ReleaseId```n- checkpoint_slice：``$Slice```n- checkpoint_commit：``$checkpointCommit```n- checkpoint_status：``ready```n- last_integrated_commit：``$lastIntegratedCommit```n"
    Set-Content -LiteralPath $statusPath -Value $status -Encoding UTF8
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("add", "docs/agentization/status/M13-status.md") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("commit", "-m", "状态：登记 M13 $ReleaseId 阶段检查点") | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("push", "origin", $branch) | Out-Null
    Invoke-TestGit -RepositoryPath $Topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null
    return $branch
}

function Get-RemoteBranchSha {
    param(
        [hashtable]$Topology,
        [string]$Branch
    )

    $lines = & git ls-remote $Topology.Remote ("refs/heads/" + $Branch)
    if ($LASTEXITCODE -ne 0 -or -not $lines) {
        throw "无法读取测试远端分支：$Branch"
    }
    return ($lines -split "\s+")[0]
}

function Remove-TestRoot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理临时目录之外的测试路径：$resolved"
    }
    Get-Process git -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Describe "Agent 分支自动化入口" {
    AfterEach {
        foreach ($root in $script:TemporaryRoots) {
            Remove-TestRoot -Path $root
        }
        $script:TemporaryRoots = @()
    }

    It "提供运行手册冻结的全部脚本入口" {
        foreach ($name in $RequiredScripts) {
            (Test-Path -LiteralPath (Join-Path $AgentizationRoot $name)) | Should Be $true
        }
    }

    It "忽略项目内模块 worktree 目录" {
        & git -C $RepositoryRoot check-ignore -q ".worktrees/probe"
        $LASTEXITCODE | Should Be 0
    }

    It "模块启动脚本创建唯一模块分支和 worktree 并可安全恢复" {
        $topology = New-AutomationTestTopology
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M01-status.md") -Encoding UTF8 -Value "# M01 状态`n`n- phase：``not_started```n- branch：计划 ``codex/agent-0.8.4-m01-runtime-store```n- 当前唯一写入者：尚未领取`n- 当前切片：``M01.1``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加模块状态") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/agent_0.8.4_boguan") | Out-Null
        $worktreeRoot = Join-Path $topology.Root "worktrees"

        $first = & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch
        $second = & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch

        $first.Action | Should Be "created"
        $second.Action | Should Be "restored"
        (Test-Path -LiteralPath $first.WorktreePath) | Should Be $true
        $branchOutput = @(Invoke-TestGit -RepositoryPath $first.WorktreePath -Arguments @("branch", "--show-current"))
        $branchOutput[-1] | Should Be "codex/agent-0.8.4-m01-runtime-store"
        [System.IO.File]::ReadAllText((Join-Path $first.WorktreePath "docs\agentization\status\M01-status.md"), [System.Text.Encoding]::UTF8) | Should Match "测试写入者"
    }

    It "模块启动脚本拒绝在上一切片提交尚未推送时启动下一片" {
        $topology = New-AutomationTestTopology
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M01-status.md") -Encoding UTF8 -Value "# M01 状态`n`n- phase：``not_started```n- branch：计划 ``codex/agent-0.8.4-m01-runtime-store```n- 当前唯一写入者：尚未领取`n- 当前切片：``M01.1``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加模块状态") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/agent_0.8.4_boguan") | Out-Null
        $worktreeRoot = Join-Path $topology.Root "worktrees"
        $started = & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch
        Invoke-TestGit -RepositoryPath $started.WorktreePath -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $started.WorktreePath -Arguments @("commit", "-m", "测试：模拟未推送的上一切片") | Out-Null

        { & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.2" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch } | Should Throw
    }

    It "模块 worktree 丢失后仍拒绝复用含未推送提交的本地分支" {
        $topology = New-AutomationTestTopology
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M01-status.md") -Encoding UTF8 -Value "# M01 状态`n`n- phase：``not_started```n- branch：计划 ``codex/agent-0.8.4-m01-runtime-store```n- 当前唯一写入者：尚未领取`n- 当前切片：``M01.1``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加模块状态") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/agent_0.8.4_boguan") | Out-Null
        $worktreeRoot = Join-Path $topology.Root "worktrees"
        $started = & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch
        Invoke-TestGit -RepositoryPath $started.WorktreePath -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $started.WorktreePath -Arguments @("commit", "-m", "测试：模拟 worktree 丢失前未推送") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("worktree", "remove", "--force", $started.WorktreePath) | Out-Null

        { & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot $worktreeRoot -RemoteName "origin" -SkipFetch } | Should Throw
    }

    It "模块启动脚本拒绝把切片编号当成模块或创建切片分支" {
        $topology = New-AutomationTestTopology

        { & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01.1" -Slice "M01.1" -Writer "测试写入者" -WorktreeRoot (Join-Path $topology.Root "worktrees") -RemoteName "origin" -SkipFetch } | Should Throw
    }

    It "模块启动脚本拒绝跳过状态文件声明的下一切片" {
        $topology = New-AutomationTestTopology
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M01-status.md") -Encoding UTF8 -Value "# M01 状态`n`n- phase：``ready```n- branch：计划 ``codex/agent-0.8.4-m01-runtime-store```n- 当前唯一写入者：尚未领取`n- 当前切片：``M01.1``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加模块状态") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/agent_0.8.4_boguan") | Out-Null

        { & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -Slice "M01.2" -Writer "测试写入者" -WorktreeRoot (Join-Path $topology.Root "worktrees") -RemoteName "origin" -SkipFetch } | Should Throw
    }

    It "分支策略拒绝 M00-A 修改 M00-B 的 web 锁定路径" {
        $topology = New-AutomationTestTopology
        $baseSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", "-b", "codex/agent-0.8.4-m00-a", $baseSha) | Out-Null
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M00-A-status.md") -Encoding UTF8 -Value "# M00-A 状态`n`n- phase：``in_progress```n- branch：``codex/agent-0.8.4-m00-a```n- 当前唯一写入者：``测试写入者``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M00-A-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：登记 M00-A 写入者") | Out-Null

        $pass = & (Join-Path $AgentizationRoot "Test-AgentBranchPolicy.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-A" -ExpectedWriter "测试写入者" -BaseRef $baseSha
        $pass.Passed | Should Be $true

        New-Item -ItemType Directory -Path (Join-Path $topology.Repository "web") -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $topology.Repository "web\forbidden.ts") -Value "export const value = 1;" -Encoding UTF8
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "web/forbidden.ts") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：模拟跨线修改") | Out-Null

        { & (Join-Path $AgentizationRoot "Test-AgentBranchPolicy.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-A" -ExpectedWriter "测试写入者" -BaseRef $baseSha } | Should Throw
    }

    It "分支策略拒绝 M00-B 修改共享状态和 A 线锁定路径" {
        $topology = New-AutomationTestTopology
        $baseSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", "-b", "codex/agent-0.8.4-m00-b", $baseSha) | Out-Null
        $statusDirectory = Join-Path $topology.Repository "docs\agentization\status"
        New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $statusDirectory "M00-B-status.md") -Encoding UTF8 -Value "# M00-B 状态`n`n- phase：``in_progress```n- branch：``codex/agent-0.8.4-m00-b```n- base Agent SHA：``$baseSha```n- 当前唯一写入者：``测试写入者``"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M00-B-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：登记 M00-B 写入者") | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $topology.Repository "docs\agentization\integration") -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $topology.Repository "docs\agentization\integration\MERGE_LOG.md") -Encoding UTF8 -Value "禁止跨线修改"
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/integration/MERGE_LOG.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：模拟修改共享集成记录") | Out-Null

        { & (Join-Path $AgentizationRoot "Test-AgentBranchPolicy.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-B" -ExpectedWriter "测试写入者" -ExpectedBaseSha $baseSha -BaseRef $baseSha } | Should Throw
    }

    It "模块门禁只接受四阶段计划列出的中间检查点" {
        $plan = & (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M12" -GateType "Phase" -ReleaseId "R1" -Slice "M12.3" -PlanOnly

        @($plan).Count | Should BeGreaterThan 0
        { & (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M12" -GateType "Phase" -ReleaseId "R1" -Slice "M12.4" -PlanOnly } | Should Throw
    }

    It "模块门禁优先使用仓库虚拟环境中的 Python" {
        $topology = New-AutomationTestTopology
        $pythonPath = Join-Path $topology.Repository "backend\.venv\Scripts\python.exe"
        New-Item -ItemType Directory -Path (Split-Path -Parent $pythonPath) -Force | Out-Null
        New-Item -ItemType File -Path $pythonPath -Force | Out-Null

        $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-A" -GateType "Final" -PlanOnly)
        $pythonCommands = @($plan | Where-Object { $_.Arguments -contains "-m" })

        $pythonCommands.Count | Should Be 2
        @($pythonCommands | Where-Object { $_.FilePath -eq $pythonPath }).Count | Should Be 2
    }

    It "M03 最终门禁只运行权威定向测试并使用项目 Python" {
        $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M03" -GateType "Final" -PlanOnly)
        $pytestCommands = @($plan | Where-Object { $_.Arguments -contains "pytest" })
        $ruffCommands = @($plan | Where-Object { $_.Arguments -contains "ruff" })
        $expectedTests = @(
            "tests/test_agent_runtime_context_externalizer.py",
            "tests/test_agent_runtime_context_assembler.py",
            "tests/test_agent_runtime_token_meter.py",
            "tests/test_agent_runtime_context_profiles.py",
            "tests/test_agent_runtime_contracts.py",
            "tests/test_agent_runtime_config.py",
            "tests/test_profile_config.py",
            "tests/test_pixelflow_memory_helper.py"
        )

        $pytestCommands.Count | Should Be 1
        $ruffCommands.Count | Should Be 1
        ($pytestCommands[0].FilePath -match "backend[\\/]\.venv[\\/]Scripts[\\/]python\.exe$") | Should Be $true
        ($ruffCommands[0].FilePath -eq $pytestCommands[0].FilePath) | Should Be $true
        ($pytestCommands[0].Arguments -join "`n") | Should Be ((@("-m", "pytest") + $expectedTests + @("-q")) -join "`n")
        ($ruffCommands[0].Arguments -join "`n") | Should Be (
            (
                @("-m", "ruff", "check", "pixelflow/agent_runtime/context") +
                $expectedTests
            ) -join "`n"
        )
    }

    It "M01 最终门禁只运行运行时持久化权威测试" {
        $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M01" -GateType "Final" -PlanOnly)
        $pytestCommands = @($plan | Where-Object { $_.Arguments -contains "pytest" })
        $ruffCommands = @($plan | Where-Object { $_.Arguments -contains "ruff" })
        $expectedTests = @(
            "tests/test_agent_runtime_conversation_cas.py",
            "tests/test_agent_runtime_migration.py",
            "tests/test_agent_runtime_repositories.py",
            "tests/test_agent_runtime_turn_inbox.py",
            "tests/test_pixelflow_task_store.py",
            "tests/test_pixelflow_conversations_router.py",
            "tests/test_pixelflow_jianying_draft_router.py"
        )

        $pytestCommands.Count | Should Be 1
        $ruffCommands.Count | Should Be 1
        ($pytestCommands[0].Arguments -join "`n") | Should Be ((@("-m", "pytest") + $expectedTests + @("-q")) -join "`n")
        ($ruffCommands[0].Arguments -join "`n") | Should Be (
            (
                @(
                    "-m", "ruff", "check",
                    "pixelflow/agent_runtime/persistence",
                    "pixelflow/tasks",
                    "app/gateway/routers/pixelflow_conversations.py",
                    "packages/harness/deerflow/persistence/migrations/versions",
                    "packages/harness/deerflow/persistence/models/__init__.py"
                ) +
                $expectedTests
            ) -join "`n"
        )
    }

    It "未配置权威测试清单的后端模块必须 fail-closed" {
        foreach ($moduleId in @("M02", "M04", "M05", "M06")) {
            { & (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId $moduleId -GateType "Final" -PlanOnly } | Should Throw
        }
    }

    It "M13 保留后端全量门禁并使用项目 Python" {
        $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M13" -GateType "Final" -PlanOnly)
        $pytestCommands = @($plan | Where-Object { $_.Arguments -contains "pytest" })
        $ruffCommands = @($plan | Where-Object { $_.Arguments -contains "ruff" })
        $webCommands = @($plan | Where-Object { $_.WorkingDirectory -eq (Join-Path $RepositoryRoot "web") })

        $pytestCommands.Count | Should Be 1
        $ruffCommands.Count | Should Be 1
        (($pytestCommands[0].Arguments -join " ") -eq "-m pytest -q") | Should Be $true
        (($ruffCommands[0].Arguments -join " ") -eq "-m ruff check .") | Should Be $true
        ($pytestCommands[0].FilePath -match "backend[\\/]\.venv[\\/]Scripts[\\/]python\.exe$") | Should Be $true
        ($ruffCommands[0].FilePath -eq $pytestCommands[0].FilePath) | Should Be $true
        @($webCommands | Where-Object { ($_.Arguments -join " ") -eq "pnpm test:agent-runtime-contracts" }).Count | Should Be 1
        @($webCommands | Where-Object { ($_.Arguments -join " ") -eq "pnpm test" }).Count | Should Be 1
        @($webCommands | Where-Object { ($_.Arguments -join " ") -eq "pnpm lint" }).Count | Should Be 1
        @($webCommands | Where-Object { ($_.Arguments -join " ") -eq "pnpm build-prod" }).Count | Should Be 1
    }

    It "缺少项目虚拟环境时不得回退到 PATH Python" {
        $topology = New-AutomationTestTopology

        { & (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $topology.Repository -ModuleId "M03" -GateType "Final" -PlanOnly } | Should Throw
    }

    It "M00 首次集成门禁只聚合 M00 范围命令" {
        $plan = @(& (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M00" -GateType "Final" -PlanOnly)
        $pythonCommand = @($plan | Where-Object { ($_.Arguments -contains "pytest") -and ($_.Arguments -contains "tests/test_agent_runtime_config.py") })
        $webCommands = @($plan | Where-Object { $_.WorkingDirectory -eq (Join-Path $RepositoryRoot "web") })
        $allArguments = @($plan | ForEach-Object { $_.Arguments }) -join " "

        $pythonCommand.Count | Should Be 1
        ($pythonCommand[0].Arguments -contains "tests/test_openapi_operation_ids.py") | Should Be $true
        @($webCommands | Where-Object { $_.Arguments -contains "test:agent-runtime-contracts" }).Count | Should Be 1
        @($webCommands | Where-Object { $_.Arguments -contains "test" }).Count | Should Be 1
        @($webCommands | Where-Object { $_.Arguments -contains "lint" }).Count | Should Be 1
        @($webCommands | Where-Object { $_.Arguments -contains "build-prod" }).Count | Should Be 1
        ($allArguments -match "test_gateway_runtime_cleanup") | Should Be $false
        ($allArguments -match "M0[1-9]|M1[0-3]") | Should Be $false
        { & (Join-Path $AgentizationRoot "Invoke-AgentModuleGate.ps1") -RepositoryPath $RepositoryRoot -ModuleId "M00" -GateType "Final" -AdditionalGateScript "outside-m00.ps1" -PlanOnly } | Should Throw
    }

    It "阶段集成把触发参数绑定到远端 checkpoint 元数据" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-PhaseModuleCommit -Topology $topology -StatusSlice "M12.4"
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M12" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R1" -Slice "M12.3" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }

    It "普通模块集成入口拒绝绕过 M00-I.1 单独集成 A 或 B" {
        $topology = New-AutomationTestTopology

        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-A" -ModuleBranch "codex/agent-0.8.4-m00-a" -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw
        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M00-B" -ModuleBranch "codex/agent-0.8.4-m00-b" -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw
    }

    It "合法阶段检查点绿色后原子更新 Agent 和模块状态" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-PhaseModuleCommit -Topology $topology
        $checkpointHead = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch

        $result = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M12" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R1" -Slice "M12.3" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply
        $agentSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"
        $moduleStateSha = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch
        $moduleStatus = (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("show", "$moduleStateSha`:docs/agentization/status/M12-status.md")) -join "`n"

        $result.Status | Should Be "integrated"
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $checkpointHead, $agentSha)) | Out-Null
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $result.ModuleStateSha, $agentSha)) | Out-Null
        $moduleStatus | Should Match "phase：``phase_integrated``"
        $moduleStatus | Should Match "checkpoint_status：``phase_integrated:R1``"
        $moduleStatus | Should Match "当前切片：``M12.4``"
        $confirmedAgain = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M12" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R1" -Slice "M12.3" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply
        $confirmedAgain.Status | Should Be "already_integrated"
        $resumed = & (Join-Path $AgentizationRoot "Start-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M12" -Slice "M12.4" -Writer "下一切片测试者" -WorktreeRoot (Join-Path $topology.Root "module-worktrees") -RemoteName "origin" -SkipFetch
        $resumed.Action | Should Be "created"
    }

    It "同一模块后续检查点只集成 last_integrated_commit 之后的增量" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-M13PhaseCheckpoint -Topology $topology -ReleaseId "R1" -Slice "M13.1"
        $first = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M13" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R1" -Slice "M13.1" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply
        $moduleBranch = Add-M13PhaseCheckpoint -Topology $topology -ReleaseId "R2" -Slice "M13.2" -ReuseBranch

        $second = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M13" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R2" -Slice "M13.2" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply
        $agentSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        $first.Status | Should Be "integrated"
        $second.Status | Should Be "integrated"
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $second.ModuleSha, $agentSha)) | Out-Null
    }

    It "同一模块拒绝只改状态后重复集成旧检查点" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-M13PhaseCheckpoint -Topology $topology -ReleaseId "R1" -Slice "M13.1"
        & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M13" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R1" -Slice "M13.1" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply | Out-Null
        $moduleBranch = Add-M13PhaseCheckpoint -Topology $topology -ReleaseId "R2" -Slice "M13.2" -ReuseBranch -StatusOnly
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M13" -ModuleBranch $moduleBranch -GateType "Phase" -ReleaseId "R2" -Slice "M13.2" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }

    It "dev 同步仅在门禁绿色后更新远端 Agent" {
        $topology = New-AutomationTestTopology
        Add-DevCommit -Topology $topology
        $devSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/dev_0.8.4_boguan"

        $result = & (Join-Path $AgentizationRoot "Sync-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "candidates") -GateScript $topology.PassGate -SkipFetch -Apply
        $agentSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        $result.Status | Should Be "integrated"
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $devSha, $agentSha)) | Out-Null
    }

    It "dev 同步门禁失败时保持远端 Agent 不变" {
        $topology = New-AutomationTestTopology
        Add-DevCommit -Topology $topology
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Sync-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "candidates") -GateScript $topology.FailGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }

    It "dev 同步遇到冲突时保留候选且不污染远端 Agent" {
        $topology = New-AutomationTestTopology
        Set-Content -LiteralPath (Join-Path $topology.Repository "README.md") -Value "Agent 分支内容" -Encoding UTF8
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "README.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加 Agent 冲突变更") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/agent_0.8.4_boguan") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", "feature/dev_0.8.4_boguan") | Out-Null
        Set-Content -LiteralPath (Join-Path $topology.Repository "README.md") -Value "日常分支内容" -Encoding UTF8
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "README.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "测试：增加日常冲突变更") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", "feature/dev_0.8.4_boguan") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Sync-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "candidates") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
        @(Get-ChildItem -LiteralPath (Join-Path $topology.Root "candidates") -Directory).Count | Should BeGreaterThan 0
    }

    It "dev 同步在脏工作区或缺少远端分支时 fail-closed" {
        $topology = New-AutomationTestTopology
        Set-Content -LiteralPath (Join-Path $topology.Repository "dirty.txt") -Value "用户未提交修改" -Encoding UTF8
        { & (Join-Path $AgentizationRoot "Sync-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "candidates") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw
        Remove-Item -LiteralPath (Join-Path $topology.Repository "dirty.txt")
        { & (Join-Path $AgentizationRoot "Sync-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -DevBranch "feature/missing" -CandidateRoot (Join-Path $topology.Root "candidates") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw
    }

    It "单槽集成按最新 Agent、最新 dev 和模块提交构建候选" {
        $topology = New-AutomationTestTopology
        Add-DevCommit -Topology $topology
        $moduleBranch = Add-ModuleCommit -Topology $topology
        $moduleSha = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch
        $devSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/dev_0.8.4_boguan"

        $result = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -ModuleBranch $moduleBranch -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply
        $agentSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        $result.Status | Should Be "integrated"
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $devSha, $agentSha)) | Out-Null
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $moduleSha, $agentSha)) | Out-Null
        $moduleStateSha = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch
        $moduleStatus = (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("show", "$moduleStateSha`:docs/agentization/status/M01-status.md")) -join "`n"
        $moduleStatus | Should Match "phase：``merged``"
        $moduleStatus | Should Match "last_integrated_commit：``$moduleSha``"
    }

    It "单槽集成拒绝非法阶段状态且不修改远端 Agent" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-ModuleCommit -Topology $topology -Phase "in_progress"
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -ModuleBranch $moduleBranch -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }

    It "单槽集成门禁失败时保留候选并保持远端 Agent 不变" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-ModuleCommit -Topology $topology
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -ModuleBranch $moduleBranch -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.FailGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
        @(Get-ChildItem -LiteralPath (Join-Path $topology.Root "integration") -Directory).Count | Should BeGreaterThan 0
        $blockedSha = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch
        $blockedStatus = (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("show", "$blockedSha`:docs/agentization/status/M01-status.md")) -join "`n"
        $blockedStatus | Should Match "phase：``integration_blocked``"
        $blockedStatus | Should Match "integration failure evidence"
        $blockedStatus | Should Match "last_integrated_commit：``—``"
        $failedCandidate = @(Get-ChildItem -LiteralPath (Join-Path $topology.Root "integration") -Directory | Where-Object { $_.Name -like "codex-integrate-m01-*" })[0]
        $candidateStatus = (Invoke-TestGit -RepositoryPath $failedCandidate.FullName -Arguments @("show", "HEAD:docs/agentization/status/M01-status.md")) -join "`n"
        $candidateStatus | Should Not Match "phase：``merged``"

        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("fetch", "origin", $moduleBranch) | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("branch", "-f", $moduleBranch, "origin/$moduleBranch") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", $moduleBranch) | Out-Null
        $statusPath = Join-Path $topology.Repository "docs\agentization\status\M01-status.md"
        $repairedStatus = [System.IO.File]::ReadAllText($statusPath, [System.Text.Encoding]::UTF8).Replace("phase：``integration_blocked``", "phase：``ready_for_integration``")
        [System.IO.File]::WriteAllText($statusPath, $repairedStatus, [System.Text.Encoding]::UTF8)
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("add", "docs/agentization/status/M01-status.md") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("commit", "-m", "修复：重新开放 M01 集成检查点") | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("push", "origin", $moduleBranch) | Out-Null
        Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("checkout", "feature/agent_0.8.4_boguan") | Out-Null

        $retried = & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -ModuleBranch $moduleBranch -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration-retry") -GateScript $topology.PassGate -SkipFetch -Apply
        $retried.Status | Should Be "integrated"
        $retriedModuleSha = Get-RemoteBranchSha -Topology $topology -Branch $moduleBranch
        $retriedStatus = (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("show", "$retriedModuleSha`:docs/agentization/status/M01-status.md")) -join "`n"
        $retriedStatus | Should Match "integration failure evidence：``无``"
    }

    It "单槽锁被占用时第二个集成任务 fail-closed" {
        $topology = New-AutomationTestTopology
        $moduleBranch = Add-ModuleCommit -Topology $topology
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"
        $commonDirectory = @(Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("rev-parse", "--git-common-dir"))[-1]
        if (-not [System.IO.Path]::IsPathRooted($commonDirectory)) {
            $commonDirectory = Join-Path $topology.Repository $commonDirectory
        }
        $lockPath = Join-Path ([System.IO.Path]::GetFullPath($commonDirectory)) "agentization-integration.lock"
        $lock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        try {
            { & (Join-Path $AgentizationRoot "Integrate-AgentModule.ps1") -RepositoryPath $topology.Repository -ModuleId "M01" -ModuleBranch $moduleBranch -GateType "Final" -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "integration") -GateScript $topology.PassGate -SkipFetch -Apply } | Should Throw
        }
        finally {
            $lock.Dispose()
        }

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }

    It "每日漂移入口复用安全同步并报告无漂移" {
        $topology = New-AutomationTestTopology

        $result = & (Join-Path $AgentizationRoot "Reconcile-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "reconcile") -GateScript $topology.PassGate -SkipFetch -Apply

        $result.Status | Should Be "up_to_date"
    }

    It "每日漂移入口在 dev 领先时复用绿色门禁完成同步" {
        $topology = New-AutomationTestTopology
        Add-DevCommit -Topology $topology
        $devSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/dev_0.8.4_boguan"

        $result = & (Join-Path $AgentizationRoot "Reconcile-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "reconcile") -GateScript $topology.PassGate -SkipFetch -Apply
        $agentSha = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        $result.Status | Should Be "integrated"
        (Invoke-TestGit -RepositoryPath $topology.Repository -Arguments @("merge-base", "--is-ancestor", $devSha, $agentSha)) | Out-Null
    }

    It "每日漂移门禁失败时不污染远端 Agent" {
        $topology = New-AutomationTestTopology
        Add-DevCommit -Topology $topology
        $before = Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan"

        { & (Join-Path $AgentizationRoot "Reconcile-DevToAgent.ps1") -RepositoryPath $topology.Repository -RemoteName "origin" -CandidateRoot (Join-Path $topology.Root "reconcile") -GateScript $topology.FailGate -SkipFetch -Apply } | Should Throw

        (Get-RemoteBranchSha -Topology $topology -Branch "feature/agent_0.8.4_boguan") | Should Be $before
    }
}
