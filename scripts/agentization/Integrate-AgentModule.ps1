[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$ModuleId,

    [Parameter(Mandatory = $true)]
    [string]$ModuleBranch,

    [ValidateSet("Phase", "Final")]
    [string]$GateType,

    [string]$ReleaseId,

    [string]$Slice,

    [string]$RemoteName = "origin",

    [string]$DevBranch = "feature/dev_0.8.4_boguan",

    [string]$AgentBranch = "feature/agent_0.8.4_boguan",

    [Parameter(Mandatory = $true)]
    [string]$CandidateRoot,

    [Parameter(Mandatory = $true)]
    [string]$GateScript,

    [switch]$SkipFetch,

    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Agentization.Common.ps1")

function Set-ModuleIntegrationFields {
    param(
        [string]$StatusPath,
        [string]$Phase,
        [string]$LastIntegratedCommit,
        [string]$CheckpointStatus,
        [string]$Evidence,
        [string]$NextSlice
    )

    Set-AgentMarkdownField -Path $StatusPath -Field "phase" -Value $Phase
    Set-AgentMarkdownField -Path $StatusPath -Field "last_integrated_commit" -Value $LastIntegratedCommit
    Set-AgentMarkdownField -Path $StatusPath -Field "当前唯一写入者" -Value "尚未领取"
    Set-AgentMarkdownField -Path $StatusPath -Field "locked files" -Value "无"
    if (-not [string]::IsNullOrWhiteSpace($NextSlice)) {
        Set-AgentMarkdownField -Path $StatusPath -Field "当前切片" -Value $NextSlice
    }
    if (-not [string]::IsNullOrWhiteSpace($CheckpointStatus)) {
        Set-AgentMarkdownField -Path $StatusPath -Field "checkpoint_status" -Value $CheckpointStatus
    }
    if (-not [string]::IsNullOrWhiteSpace($Evidence)) {
        Set-AgentMarkdownField -Path $StatusPath -Field "integration failure evidence" -Value $Evidence
    }
}

function New-ModuleStateCommit {
    param(
        [string]$RepositoryPath,
        [string]$CandidateRoot,
        [pscustomobject]$Definition,
        [string]$ModuleId,
        [string]$ModuleSha,
        [string]$LastIntegratedCommit,
        [string]$Phase,
        [string]$CheckpointStatus,
        [string]$Evidence,
        [string]$NextSlice
    )

    $stateBranch = New-AgentCandidateIdentity -Prefix "codex/module-state-$($ModuleId.ToLowerInvariant())"
    $statePath = New-AgentCandidateWorktree -RepositoryPath $RepositoryPath -CandidateRoot $CandidateRoot -CandidateBranch $stateBranch -StartRef $ModuleSha
    $statusPath = Join-Path $statePath ($Definition.Status -replace "/", "\")
    Set-ModuleIntegrationFields -StatusPath $statusPath -Phase $Phase -LastIntegratedCommit $LastIntegratedCommit -CheckpointStatus $CheckpointStatus -Evidence $Evidence -NextSlice $NextSlice
    Invoke-AgentGit -RepositoryPath $statePath -Arguments @("add", "--", $Definition.Status) | Out-Null
    $commitTitle = if ($Phase -match "blocked$") { "集成($ModuleId)：记录单槽门禁阻塞" } else { "集成($ModuleId)：记录单槽门禁通过" }
    Invoke-AgentGit -RepositoryPath $statePath -Arguments @("commit", "-m", $commitTitle, "-m", "仅更新模块状态和安全证据；不包含凭据、业务内容或完整异常信息。") | Out-Null
    $stateSha = (Invoke-AgentGit -RepositoryPath $statePath -Arguments @("rev-parse", "HEAD")).Output[-1]
    return [pscustomobject]@{
        Branch = $stateBranch
        Path = $statePath
        Sha = $stateSha
    }
}

function Update-CandidateIntegrationRecords {
    param(
        [string]$CandidatePath,
        [pscustomobject]$Definition,
        [string]$ModuleId,
        [string]$GateType,
        [string]$ReleaseId,
        [string]$ModuleSha,
        [switch]$SkipStatus
    )

    $pathsToCommit = New-Object System.Collections.Generic.List[string]
    $statusPath = Join-Path $CandidatePath ($Definition.Status -replace "/", "\")
    if (-not $SkipStatus -and (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        $phase = if ($GateType -eq "Phase") { "phase_integrated" } else { "merged" }
        $checkpointStatus = if ($GateType -eq "Phase") { "phase_integrated:$ReleaseId" } else { "integrated" }
        Set-ModuleIntegrationFields -StatusPath $statusPath -Phase $phase -LastIntegratedCommit $ModuleSha -CheckpointStatus $checkpointStatus -Evidence "无" -NextSlice ""
        $pathsToCommit.Add($Definition.Status)
    }

    $boardRelativePath = "docs/agentization/status/BOARD.md"
    $boardPath = Join-Path $CandidatePath ($boardRelativePath -replace "/", "\")
    if (Test-Path -LiteralPath $boardPath -PathType Leaf) {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $lines = [System.IO.File]::ReadAllLines($boardPath, [System.Text.Encoding]::UTF8)
        for ($index = 0; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match "^\|\s*$([regex]::Escape($ModuleId))\s*\|") {
                $cells = $lines[$index] -split "\|"
                if ($cells.Count -ge 8) {
                    $cells[4] = if ($GateType -eq "Phase") { " ``phase_integrated`` " } else { " ``merged`` " }
                    $cells[7] = " ``$($ModuleSha.Substring(0, 7))`` "
                    $lines[$index] = $cells -join "|"
                }
            }
        }
        [System.IO.File]::WriteAllLines($boardPath, $lines, $encoding)
        $pathsToCommit.Add($boardRelativePath)
    }

    $mergeLogRelativePath = "docs/agentization/integration/MERGE_LOG.md"
    $mergeLogPath = Join-Path $CandidatePath ($mergeLogRelativePath -replace "/", "\")
    if (Test-Path -LiteralPath $mergeLogPath -PathType Leaf) {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $content = [System.IO.File]::ReadAllText($mergeLogPath, [System.Text.Encoding]::UTF8).TrimEnd()
        $scope = if ($GateType -eq "Phase") { "阶段 $ReleaseId" } else { "最终模块" }
        $entry = "- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')：$ModuleId $scope 候选通过，模块提交 ``$ModuleSha`` 已纳入最新 Agent/dev 基线。"
        [System.IO.File]::WriteAllText($mergeLogPath, $content + [Environment]::NewLine + $entry + [Environment]::NewLine, $encoding)
        $pathsToCommit.Add($mergeLogRelativePath)
    }

    if ($pathsToCommit.Count -gt 0) {
        Invoke-AgentGit -RepositoryPath $CandidatePath -Arguments (@("add", "--") + $pathsToCommit.ToArray()) | Out-Null
        $status = Invoke-AgentGit -RepositoryPath $CandidatePath -Arguments @("status", "--porcelain")
        if ($status.Output.Count -gt 0) {
            Invoke-AgentGit -RepositoryPath $CandidatePath -Arguments @("commit", "-m", "集成($ModuleId)：记录模块门禁结果", "-m", "更新模块状态、总看板和合并证据；本提交由单槽候选自动生成。") | Out-Null
        }
    }
}

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
$definition = Get-AgentModuleDefinition -ModuleId $ModuleId
if ($ModuleId -in @("M00-A", "M00-B")) {
    throw "M00-A/M00-B 只能由 M00-I.1 专用固定顺序候选集成，普通模块入口拒绝执行。"
}
if ($ModuleBranch -ne $definition.Branch) {
    throw "模块分支与冻结定义不一致：期望 $($definition.Branch)，实际 $ModuleBranch"
}
if ($GateType -eq "Phase" -and ([string]::IsNullOrWhiteSpace($ReleaseId) -or [string]::IsNullOrWhiteSpace($Slice) -or -not (Test-AgentReleaseCheckpoint -ModuleId $ModuleId -ReleaseId $ReleaseId -Slice $Slice))) {
    throw "非法阶段检查点：$ModuleId/$ReleaseId/$Slice"
}
Assert-AgentCleanWorktree -RepositoryPath $root
if (-not $SkipFetch) {
    Invoke-AgentGit -RepositoryPath $root -Arguments @("fetch", $RemoteName, "--prune") | Out-Null
}

$agentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
$devSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
$moduleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
$statusResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "$moduleSha`:$($definition.Status)")
$statusContent = $statusResult.Output -join "`n"
$phase = Get-AgentMarkdownField -Content $statusContent -Field "phase"
$expectedPhase = if ($GateType -eq "Phase") { "ready_for_phase_integration" } else { "ready_for_integration" }
if (($GateType -eq "Phase" -and $phase -eq "phase_integrated") -or ($GateType -eq "Final" -and $phase -eq "merged")) {
    $completedCommit = Get-AgentMarkdownField -Content $statusContent -Field "last_integrated_commit"
    $completedCheckpointStatus = Get-AgentMarkdownField -Content $statusContent -Field "checkpoint_status"
    $expectedCheckpointStatus = if ($GateType -eq "Phase") { "phase_integrated:$ReleaseId" } else { "integrated" }
    $completedMetadataMatches = $true
    if ($GateType -eq "Phase") {
        $completedReleaseId = Get-AgentMarkdownField -Content $statusContent -Field "release_id"
        $completedCheckpointSlice = Get-AgentMarkdownField -Content $statusContent -Field "checkpoint_slice"
        $completedMetadataMatches = $completedReleaseId -eq $ReleaseId -and $completedCheckpointSlice -eq $Slice
    }
    if ($completedMetadataMatches -and $completedCommit -and $completedCommit -ne "—" -and $completedCheckpointStatus -eq $expectedCheckpointStatus -and (Test-AgentAncestor -RepositoryPath $root -Ancestor $completedCommit -Descendant $agentSha) -and (Test-AgentAncestor -RepositoryPath $root -Ancestor $moduleSha -Descendant $agentSha)) {
        return [pscustomobject]@{
            Status = "already_integrated"
            ModuleId = $ModuleId
            AgentSha = $agentSha
            ModuleStateSha = $moduleSha
            ModuleSha = $completedCommit
        }
    }
}
if ($phase -ne $expectedPhase) {
    throw "模块状态不是合法集成入口：期望 $expectedPhase，实际 $phase"
}
if ($GateType -eq "Phase") {
    $statusReleaseId = Get-AgentMarkdownField -Content $statusContent -Field "release_id"
    $statusCheckpointSlice = Get-AgentMarkdownField -Content $statusContent -Field "checkpoint_slice"
    $statusCheckpointCommit = Get-AgentMarkdownField -Content $statusContent -Field "checkpoint_commit"
    $statusCheckpointState = Get-AgentMarkdownField -Content $statusContent -Field "checkpoint_status"
    if ($statusReleaseId -ne $ReleaseId -or $statusCheckpointSlice -ne $Slice -or $statusCheckpointState -ne "ready") {
        throw "模块状态中的 release/checkpoint 元数据与触发参数不一致。"
    }
    if ($statusCheckpointCommit -notmatch "^[0-9a-fA-F]{40}$" -or -not (Test-AgentAncestor -RepositoryPath $root -Ancestor $statusCheckpointCommit -Descendant $moduleSha)) {
        throw "checkpoint_commit 不是当前模块提交的合法祖先。"
    }
    $postCheckpointPaths = @(Invoke-AgentGit -RepositoryPath $root -Arguments @("diff", "--name-only", "$statusCheckpointCommit..$moduleSha")).Output
    $unexpectedPostCheckpointPaths = @($postCheckpointPaths | Where-Object { $_ -ne $definition.Status })
    if ($unexpectedPostCheckpointPaths.Count -gt 0) {
        throw "checkpoint_commit 之后存在未声明的模块变更：$($unexpectedPostCheckpointPaths -join ', ')"
    }
}
$lastIntegratedCommit = Get-AgentMarkdownField -Content $statusContent -Field "last_integrated_commit"
if ($lastIntegratedCommit -and $lastIntegratedCommit -ne "—") {
    if (-not (Test-AgentAncestor -RepositoryPath $root -Ancestor $lastIntegratedCommit -Descendant $moduleSha)) {
        throw "last_integrated_commit 不是当前模块提交的祖先，疑似发生 force-push/rebase。"
    }
    if (-not (Test-AgentAncestor -RepositoryPath $root -Ancestor $lastIntegratedCommit -Descendant $agentSha)) {
        throw "上次集成提交尚未进入 Agent，拒绝跳过前置检查点。"
    }
    $incrementPaths = @(Invoke-AgentGit -RepositoryPath $root -Arguments @("diff", "--name-only", "$lastIntegratedCommit..$moduleSha")).Output
    $moduleIncrementPaths = @($incrementPaths | Where-Object { $_ -ne $definition.Status })
    if ($moduleIncrementPaths.Count -eq 0) {
        throw "当前检查点相对 last_integrated_commit 没有模块增量，拒绝重复集成旧提交。"
    }
}

$commonDirResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("rev-parse", "--git-common-dir")
$commonDir = $commonDirResult.Output[-1]
if (-not [System.IO.Path]::IsPathRooted($commonDir)) {
    $commonDir = Join-Path $root $commonDir
}
$commonDir = [System.IO.Path]::GetFullPath($commonDir)
$lockPath = Join-Path $commonDir "agentization-integration.lock"
$lockStream = $null
try {
    try {
        $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    }
    catch {
        throw "另一集成任务已占用单槽锁：$lockPath"
    }

    $prefix = if ($GateType -eq "Phase") { "codex/integrate-$($ReleaseId.ToLowerInvariant())-$($ModuleId.ToLowerInvariant())" } else { "codex/integrate-$($ModuleId.ToLowerInvariant())" }
    $candidateBranch = New-AgentCandidateIdentity -Prefix $prefix
    $candidatePath = New-AgentCandidateWorktree -RepositoryPath $root -CandidateRoot $CandidateRoot -CandidateBranch $candidateBranch -StartRef $agentSha
    $moduleState = $null
    $candidateExpectedSha = $null
    try {
        if (-not (Test-AgentAncestor -RepositoryPath $candidatePath -Ancestor $devSha -Descendant "HEAD")) {
            Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("merge", "--no-ff", $devSha, "-m", "同步：候选纳入最新日常分支") | Out-Null
        }
        if (-not (Test-AgentAncestor -RepositoryPath $candidatePath -Ancestor $moduleSha -Descendant "HEAD")) {
            Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("merge", "--no-ff", $moduleSha, "-m", "集成($ModuleId)：纳入模块检查点") | Out-Null
        }
        & (Join-Path $PSScriptRoot "Test-ChineseEngineeringPolicy.ps1") -RepositoryPath $candidatePath -BaseRef $agentSha -HeadRef "HEAD" | Out-Null
        Invoke-AgentGateScript -GateScript $GateScript -RepositoryPath $candidatePath | Out-Null
        $successPhase = if ($GateType -eq "Phase") { "phase_integrated" } else { "merged" }
        $successCheckpointStatus = if ($GateType -eq "Phase") { "phase_integrated:$ReleaseId" } else { "integrated" }
        $nextSlice = ""
        if ($GateType -eq "Phase" -and $Slice -match "^(.*\.)(\d+)$") {
            $nextSlice = $matches[1] + ([int]$matches[2] + 1)
        }
        $moduleState = New-ModuleStateCommit -RepositoryPath $root -CandidateRoot $CandidateRoot -Definition $definition -ModuleId $ModuleId -ModuleSha $moduleSha -LastIntegratedCommit $moduleSha -Phase $successPhase -CheckpointStatus $successCheckpointStatus -Evidence "无" -NextSlice $nextSlice
        Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("merge", "--no-ff", $moduleState.Sha, "-m", "集成($ModuleId)：同步模块状态提交") | Out-Null
        Update-CandidateIntegrationRecords -CandidatePath $candidatePath -Definition $definition -ModuleId $ModuleId -GateType $GateType -ReleaseId $ReleaseId -ModuleSha $moduleSha -SkipStatus
        & (Join-Path $PSScriptRoot "Test-ChineseEngineeringPolicy.ps1") -RepositoryPath $candidatePath -BaseRef $agentSha -HeadRef "HEAD" | Out-Null
        Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("diff", "--check") | Out-Null
        $candidateExpectedSha = (Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("rev-parse", "HEAD")).Output[-1]

        $latestAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
        $latestDevSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
        $latestModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
        if ($latestAgentSha -ne $agentSha -or $latestDevSha -ne $devSha -or $latestModuleSha -ne $moduleSha) {
            throw "候选验证期间远端基线已变化，必须按最新 Agent + dev + 模块提交重建。"
        }

        if (-not $Apply) {
            return [pscustomobject]@{
                Status = "candidate_ready"
                ModuleId = $ModuleId
                CandidateBranch = $candidateBranch
                CandidatePath = $candidatePath
                ModuleStateBranch = $moduleState.Branch
                ModuleStatePath = $moduleState.Path
                ModuleStateSha = $moduleState.Sha
            }
        }

        $latestAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
        $latestDevSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
        $latestModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
        if ($latestAgentSha -ne $agentSha -or $latestDevSha -ne $devSha -or $latestModuleSha -ne $moduleSha) {
            throw "模块状态提交期间远端基线已变化，必须按最新 Agent + dev + 模块提交重建。"
        }

        Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("push", "--atomic", $RemoteName, "HEAD:refs/heads/$AgentBranch", "refs/heads/$($moduleState.Branch):refs/heads/$ModuleBranch", "$devSha`:refs/heads/$DevBranch") | Out-Null
        $integratedSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
        $integratedModuleStateSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
        return [pscustomobject]@{
            Status = "integrated"
            ModuleId = $ModuleId
            PreviousAgentSha = $agentSha
            AgentSha = $integratedSha
            DevSha = $devSha
            ModuleSha = $moduleSha
            ModuleStateSha = $integratedModuleStateSha
            CandidateBranch = $candidateBranch
            CandidatePath = $candidatePath
        }
    }
    catch {
        $failureType = $_.Exception.GetType().Name
        if ($Apply -and $moduleState -and $candidateExpectedSha) {
            try {
                $confirmedAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
                $confirmedModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
            }
            catch {
                throw "模块集成结果未知；无法读取远端确认原子 push 是否已接受，禁止自动重试。候选：$candidatePath"
            }
            if ($confirmedAgentSha -eq $candidateExpectedSha -and $confirmedModuleSha -eq $moduleState.Sha) {
                return [pscustomobject]@{
                    Status = "integrated"
                    ModuleId = $ModuleId
                    PreviousAgentSha = $agentSha
                    AgentSha = $candidateExpectedSha
                    DevSha = $devSha
                    ModuleSha = $moduleSha
                    ModuleStateSha = $moduleState.Sha
                    CandidateBranch = $candidateBranch
                    CandidatePath = $candidatePath
                    ConfirmedAfterError = $true
                }
            }
            if ($confirmedAgentSha -ne $agentSha -or $confirmedModuleSha -ne $moduleSha) {
                throw "模块集成结果未知；远端既不是原基线也不是预期原子结果，禁止自动重试。候选：$candidatePath"
            }
        }
        $blockedRecorded = $false
        if ($Apply) {
            try {
                $currentAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
                $currentModuleSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $ModuleBranch
                if ($currentAgentSha -eq $agentSha -and $currentModuleSha -eq $moduleSha) {
                    $blockedPhase = if ($GateType -eq "Phase") { "phase_integration_blocked" } else { "integration_blocked" }
                    $blockedStatus = if ($GateType -eq "Phase") { "blocked:$ReleaseId" } else { "blocked" }
                    $safeEvidence = "候选 $candidateBranch 已保留；Agent 未更新；错误类型 $failureType"
                    $blockedLastIntegratedCommit = if ($lastIntegratedCommit -and $lastIntegratedCommit -ne "—") { $lastIntegratedCommit } else { "—" }
                    $blockedState = New-ModuleStateCommit -RepositoryPath $root -CandidateRoot $CandidateRoot -Definition $definition -ModuleId $ModuleId -ModuleSha $moduleSha -LastIntegratedCommit $blockedLastIntegratedCommit -Phase $blockedPhase -CheckpointStatus $blockedStatus -Evidence $safeEvidence -NextSlice ""
                    Invoke-AgentGit -RepositoryPath $blockedState.Path -Arguments @("push", $RemoteName, "refs/heads/$($blockedState.Branch):refs/heads/$ModuleBranch") | Out-Null
                    $blockedRecorded = $true
                }
            }
            catch {
                $blockedRecorded = $false
            }
        }
        $recordMessage = if ($blockedRecorded) { "阻塞状态已安全写回模块分支" } else { "未写回阻塞状态，请按候选证据人工处理" }
        throw "模块集成失败；Agent 远端保持原值，候选保留用于审计；$recordMessage。错误类型：$failureType"
    }
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
