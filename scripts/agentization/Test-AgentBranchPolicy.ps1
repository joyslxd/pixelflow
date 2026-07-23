[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$ModuleId,

    [string]$ExpectedWriter,

    [string]$ExpectedBaseSha,

    [string]$BaseRef,

    [string]$DevRef,

    [string]$AgentRef,

    [switch]$RequireClean,

    [switch]$RunChinesePolicy
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Agentization.Common.ps1")

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
$definition = Get-AgentModuleDefinition -ModuleId $ModuleId
$branch = (Invoke-AgentGit -RepositoryPath $root -Arguments @("branch", "--show-current")).Output[-1]
if ($branch -ne $definition.Branch) {
    throw "当前分支不符合模块策略：期望 $($definition.Branch)，实际 $branch"
}
if ($branch -match "(?i)m\d{2}-s\d+" -or $branch -in @("feature/dev_0.8.4_boguan", "feature/agent_0.8.4_boguan")) {
    throw "拒绝切片分支或长期 feature 分支写入：$branch"
}

$statusPath = Join-Path $root ($definition.Status -replace "/", "\")
if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
    throw "模块状态文件不存在：$($definition.Status)"
}
$statusContent = [System.IO.File]::ReadAllText($statusPath, [System.Text.Encoding]::UTF8)
$statusBranch = Get-AgentMarkdownField -Content $statusContent -Field "branch"
if ($statusBranch -and $statusBranch -ne $definition.Branch) {
    throw "状态文件分支与冻结分支不一致：$statusBranch"
}
$writer = Get-AgentMarkdownField -Content $statusContent -Field "当前唯一写入者"
if ($ExpectedWriter -and $writer -ne $ExpectedWriter) {
    throw "当前唯一写入者不匹配：期望 $ExpectedWriter，实际 $writer"
}
if ($ExpectedBaseSha) {
    $statusBaseSha = Get-AgentMarkdownField -Content $statusContent -Field "base Agent SHA"
    if ($statusBaseSha -ne $ExpectedBaseSha -or -not (Test-AgentAncestor -RepositoryPath $root -Ancestor $ExpectedBaseSha -Descendant "HEAD")) {
        throw "模块不是从指定的共同 Agent SHA 创建：期望 $ExpectedBaseSha，状态记录 $statusBaseSha"
    }
}

$worktreeResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("worktree", "list", "--porcelain")
$branchMarker = "branch refs/heads/$($definition.Branch)"
$worktreeCount = @($worktreeResult.Output | Where-Object { $_ -eq $branchMarker }).Count
if ($worktreeCount -ne 1) {
    throw "模块分支必须且只能绑定一个 worktree，实际数量：$worktreeCount"
}

if ($BaseRef) {
    $changedResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("diff", "--name-only", "$BaseRef..HEAD")
    $changedPaths = @($changedResult.Output)
    if ($ModuleId -eq "M00-A") {
        $forbidden = @($changedPaths | Where-Object { $_ -match "^(web/|docs/agentization/status/M00-B-status\.md$|docs/agentization/status/M00-status\.md$|docs/agentization/status/BOARD\.md$|docs/agentization/integration/)" })
        if ($forbidden.Count -gt 0) {
            throw "M00-A 修改了其他 owner 路径：$($forbidden -join ', ')"
        }
    }
    if ($ModuleId -eq "M00-B") {
        $forbidden = @($changedPaths | Where-Object { $_ -match "^(\.gitignore$|backend/pixelflow/agent_runtime/|backend/tests/fixtures/agent_runtime/|scripts/agentization/|docs/agentization/status/M00-A-status\.md$|docs/agentization/status/M00-status\.md$|docs/agentization/status/BOARD\.md$|docs/agentization/integration/)" })
        if ($forbidden.Count -gt 0) {
            throw "M00-B 修改了 Python 权威或 A 线锁定路径：$($forbidden -join ', ')"
        }
    }
    if ($RunChinesePolicy) {
        & (Join-Path $PSScriptRoot "Test-ChineseEngineeringPolicy.ps1") -RepositoryPath $root -BaseRef $BaseRef -HeadRef "HEAD" | Out-Null
    }
}

if ($DevRef -and $AgentRef -and -not (Test-AgentAncestor -RepositoryPath $root -Ancestor $DevRef -Descendant $AgentRef)) {
    throw "最新 dev 不是 Agent 的祖先，必须先执行安全同步。"
}
if ($RequireClean) {
    Assert-AgentCleanWorktree -RepositoryPath $root
}

[pscustomobject]@{
    Passed = $true
    ModuleId = $ModuleId
    Branch = $branch
    Writer = $writer
    WorktreeCount = $worktreeCount
}
