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

$parameters = @{
    RepositoryPath = $RepositoryPath
    RemoteName = $RemoteName
    DevBranch = $DevBranch
    AgentBranch = $AgentBranch
    CandidateRoot = $CandidateRoot
    GateScript = $GateScript
}
if ($SkipFetch) {
    $parameters.SkipFetch = $true
}
if ($Apply) {
    $parameters.Apply = $true
}

& (Join-Path $PSScriptRoot "Sync-DevToAgent.ps1") @parameters
