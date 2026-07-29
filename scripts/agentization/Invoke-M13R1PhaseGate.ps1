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

# 用途：读取集成器在本次单槽任务中冻结的 Agent SHA；缺失时立即停止，禁止从可变远端引用猜测或回退旧 SHA。
$agentSha = [System.Environment]::GetEnvironmentVariable("PIXELFLOW_AGENTIZATION_FROZEN_AGENT_SHA", "Process")
if ([string]::IsNullOrWhiteSpace($agentSha)) {
    throw "缺少集成器冻结 Agent SHA。"
}
$agentSha = $agentSha.Trim()
if ($agentSha -notmatch "^[0-9a-fA-F]{40}$") {
    throw "集成器冻结 Agent SHA 不是合法提交。"
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
