[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$ModuleId,

    [Parameter(Mandatory = $true)]
    [string]$Slice,

    [Parameter(Mandatory = $true)]
    [string]$Writer,

    [Parameter(Mandatory = $true)]
    [string]$WorktreeRoot,

    [string]$RemoteName = "origin",

    [string]$DevBranch = "feature/dev_0.8.4_boguan",

    [string]$AgentBranch = "feature/agent_0.8.4_boguan",

    [string]$SyncGateScript,

    [switch]$SkipFetch,

    [switch]$NoPush
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Agentization.Common.ps1")

function Get-ModuleWorktreePath {
    param(
        [string]$RepositoryPath,
        [string]$Branch
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("worktree", "list", "--porcelain")
    $currentPath = $null
    foreach ($line in $result.Output) {
        if ($line.StartsWith("worktree ")) {
            $currentPath = $line.Substring(9)
        }
        elseif ($line -eq "branch refs/heads/$Branch") {
            return $currentPath
        }
    }
    return $null
}

function Set-ModuleWriterState {
    param(
        [string]$WorktreePath,
        [pscustomobject]$Definition,
        [string]$Slice,
        [string]$Writer
    )

    $statusPath = Join-Path $WorktreePath ($Definition.Status -replace "/", "\")
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        throw "模块状态文件不存在：$($Definition.Status)"
    }
    $content = [System.IO.File]::ReadAllText($statusPath, [System.Text.Encoding]::UTF8)
    $declaredSlice = Get-AgentMarkdownField -Content $content -Field "当前切片"
    $declaredSliceMatch = [regex]::Match("$declaredSlice", "[A-Z0-9-]+\.\d+")
    if (-not $declaredSliceMatch.Success -or $declaredSliceMatch.Value -ne $Slice) {
        throw "请求切片不是状态文件声明的唯一下一片：期望 $declaredSlice，实际 $Slice"
    }
    $existingWriter = Get-AgentMarkdownField -Content $content -Field "当前唯一写入者"
    if ($existingWriter -and $existingWriter -ne "尚未领取" -and $existingWriter -ne $Writer) {
        throw "模块已被其他写入者领取：$existingWriter"
    }
    Set-AgentMarkdownField -Path $statusPath -Field "phase" -Value "in_progress"
    Set-AgentMarkdownField -Path $statusPath -Field "branch" -Value $Definition.Branch
    Set-AgentMarkdownField -Path $statusPath -Field "当前切片" -Value $Slice
    Set-AgentMarkdownField -Path $statusPath -Field "当前唯一写入者" -Value $Writer
}

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
$definition = Get-AgentModuleDefinition -ModuleId $ModuleId
if ($Slice -notmatch "^$([regex]::Escape($ModuleId))(?:-[AB])?\.\d+$" -and $Slice -notmatch "^$([regex]::Escape($ModuleId))\.\d+$") {
    throw "切片编号与模块不匹配：module=$ModuleId, slice=$Slice"
}
if (-not $SkipFetch) {
    Invoke-AgentGit -RepositoryPath $root -Arguments @("fetch", $RemoteName, "--prune") | Out-Null
}

$existingWorktree = Get-ModuleWorktreePath -RepositoryPath $root -Branch $definition.Branch
if ($existingWorktree) {
    $remoteModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $definition.Branch
    $localModuleSha = (Invoke-AgentGit -RepositoryPath $existingWorktree -Arguments @("rev-parse", "HEAD")).Output[-1]
    if ($localModuleSha -ne $remoteModuleSha) {
        Assert-AgentCleanWorktree -RepositoryPath $existingWorktree
        if (-not (Test-AgentAncestor -RepositoryPath $existingWorktree -Ancestor $localModuleSha -Descendant $remoteModuleSha)) {
            throw "模块 worktree 与远端分支已分叉；上一切片必须先完成审核、提交和 push，或人工处理远端前进：local=$localModuleSha, remote=$remoteModuleSha"
        }
        Invoke-AgentGit -RepositoryPath $existingWorktree -Arguments @("merge", "--ff-only", $remoteModuleSha) | Out-Null
    }
    $statusRelativePath = $definition.Status -replace "\\", "/"
    Assert-AgentCleanWorktree -RepositoryPath $existingWorktree -AllowedRelativePaths @($statusRelativePath)
    Set-ModuleWriterState -WorktreePath $existingWorktree -Definition $definition -Slice $Slice -Writer $Writer
    return [pscustomobject]@{
        Action = "restored"
        ModuleId = $ModuleId
        Branch = $definition.Branch
        WorktreePath = [System.IO.Path]::GetFullPath($existingWorktree)
    }
}

Assert-AgentCleanWorktree -RepositoryPath $root
$devSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
$agentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
if (-not (Test-AgentAncestor -RepositoryPath $root -Ancestor $devSha -Descendant $agentSha)) {
    if ([string]::IsNullOrWhiteSpace($SyncGateScript)) {
        throw "最新 dev 尚未进入 Agent；必须提供 SyncGateScript 先执行安全同步。"
    }
    $syncParameters = @{
        RepositoryPath = $root
        RemoteName = $RemoteName
        DevBranch = $DevBranch
        AgentBranch = $AgentBranch
        CandidateRoot = (Join-Path $WorktreeRoot "_sync")
        GateScript = $SyncGateScript
        Apply = $true
    }
    if ($SkipFetch) {
        $syncParameters.SkipFetch = $true
    }
    & (Join-Path $PSScriptRoot "Sync-DevToAgent.ps1") @syncParameters | Out-Null
    $agentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
}

$localBranch = Invoke-AgentGit -RepositoryPath $root -Arguments @("show-ref", "--verify", "--quiet", "refs/heads/$($definition.Branch)") -AllowFailure
if ($localBranch.ExitCode -eq 0) {
    $localModuleSha = (Invoke-AgentGit -RepositoryPath $root -Arguments @("rev-parse", "refs/heads/$($definition.Branch)")).Output[-1]
    $remoteModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $definition.Branch
    if ($localModuleSha -ne $remoteModuleSha) {
        if (-not (Test-AgentAncestor -RepositoryPath $root -Ancestor $localModuleSha -Descendant $remoteModuleSha)) {
            throw "本地模块分支包含未推送或分叉提交，拒绝在 worktree 丢失后绕过串行保护：local=$localModuleSha, remote=$remoteModuleSha"
        }
        Invoke-AgentGit -RepositoryPath $root -Arguments @("branch", "-f", $definition.Branch, $remoteModuleSha) | Out-Null
    }
}
else {
    $remoteBranch = Invoke-AgentGit -RepositoryPath $root -Arguments @("show-ref", "--verify", "--quiet", "refs/remotes/$RemoteName/$($definition.Branch)") -AllowFailure
    if ($remoteBranch.ExitCode -eq 0) {
        Invoke-AgentGit -RepositoryPath $root -Arguments @("branch", "--track", $definition.Branch, "$RemoteName/$($definition.Branch)") | Out-Null
    }
    else {
        Invoke-AgentGit -RepositoryPath $root -Arguments @("branch", $definition.Branch, $agentSha) | Out-Null
        if (-not $NoPush) {
            Invoke-AgentGit -RepositoryPath $root -Arguments @("push", "-u", $RemoteName, $definition.Branch) | Out-Null
        }
    }
}

$worktreeRootPath = [System.IO.Path]::GetFullPath($WorktreeRoot)
if (-not (Test-Path -LiteralPath $worktreeRootPath)) {
    New-Item -ItemType Directory -Path $worktreeRootPath -Force | Out-Null
}
$worktreePath = Join-Path $worktreeRootPath $definition.Worktree
if (Test-Path -LiteralPath $worktreePath) {
    throw "目标 worktree 路径已存在但未注册到模块分支：$worktreePath"
}
Invoke-AgentGit -RepositoryPath $root -Arguments @("worktree", "add", $worktreePath, $definition.Branch) | Out-Null
Set-ModuleWriterState -WorktreePath $worktreePath -Definition $definition -Slice $Slice -Writer $Writer

[pscustomobject]@{
    Action = "created"
    ModuleId = $ModuleId
    Branch = $definition.Branch
    WorktreePath = [System.IO.Path]::GetFullPath($worktreePath)
}
