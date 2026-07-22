[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

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

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
Assert-AgentCleanWorktree -RepositoryPath $root
if (-not $SkipFetch) {
    Invoke-AgentGit -RepositoryPath $root -Arguments @("fetch", $RemoteName, "--prune") | Out-Null
}

$devSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
$agentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
if (Test-AgentAncestor -RepositoryPath $root -Ancestor $devSha -Descendant $agentSha) {
    return [pscustomobject]@{
        Status = "up_to_date"
        DevSha = $devSha
        AgentSha = $agentSha
        CandidateBranch = $null
        CandidatePath = $null
    }
}

$candidateBranch = New-AgentCandidateIdentity -Prefix "codex/sync-dev-to-agent"
$candidatePath = New-AgentCandidateWorktree -RepositoryPath $root -CandidateRoot $CandidateRoot -CandidateBranch $candidateBranch -StartRef $agentSha
$candidateSha = $null
try {
    Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("merge", "--no-ff", $devSha, "-m", "同步：纳入最新日常分支") | Out-Null
    & (Join-Path $PSScriptRoot "Test-ChineseEngineeringPolicy.ps1") -RepositoryPath $candidatePath -BaseRef $agentSha -HeadRef "HEAD" | Out-Null
    Invoke-AgentGateScript -GateScript $GateScript -RepositoryPath $candidatePath | Out-Null
    $candidateSha = (Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("rev-parse", "HEAD")).Output[-1]

    $latestDevSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $DevBranch
    $latestAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
    if ($latestDevSha -ne $devSha -or $latestAgentSha -ne $agentSha) {
        throw "候选验证期间远端 dev 或 Agent 已前进，必须从最新基线重建。"
    }

    if (-not $Apply) {
        return [pscustomobject]@{
            Status = "candidate_ready"
            DevSha = $devSha
            AgentSha = $agentSha
            CandidateBranch = $candidateBranch
            CandidatePath = $candidatePath
        }
    }

    Invoke-AgentGit -RepositoryPath $candidatePath -Arguments @("push", "--atomic", $RemoteName, "HEAD:refs/heads/$AgentBranch", "$devSha`:refs/heads/$DevBranch") | Out-Null
    $integratedSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
    return [pscustomobject]@{
        Status = "integrated"
        DevSha = $devSha
        PreviousAgentSha = $agentSha
        AgentSha = $integratedSha
        CandidateBranch = $candidateBranch
        CandidatePath = $candidatePath
    }
}
catch {
    $failureType = $_.Exception.GetType().Name
    if ($Apply -and $candidateSha) {
        try {
            $confirmedAgentSha = Get-AgentRemoteBranchSha -RepositoryPath $root -RemoteName $RemoteName -Branch $AgentBranch
        }
        catch {
            throw "dev→agent 同步结果未知；无法读取远端确认原子 push 是否已接受，禁止自动重试。候选：$candidatePath"
        }
        if ($confirmedAgentSha -eq $candidateSha) {
            return [pscustomobject]@{
                Status = "integrated"
                DevSha = $devSha
                PreviousAgentSha = $agentSha
                AgentSha = $candidateSha
                CandidateBranch = $candidateBranch
                CandidatePath = $candidatePath
                ConfirmedAfterError = $true
            }
        }
        if ($confirmedAgentSha -ne $agentSha) {
            throw "dev→agent 同步结果未知；Agent 既不是原基线也不是预期候选，禁止自动重试。候选：$candidatePath"
        }
    }
    throw "dev→agent 同步失败；Agent 保持原值，候选保留在 $candidatePath。错误类型：$failureType"
}
