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
