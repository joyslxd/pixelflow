[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$ModuleId,

    [ValidateSet("Slice", "Phase", "Final")]
    [string]$GateType = "Slice",

    [string]$ReleaseId,

    [string]$Slice,

    [string]$AdditionalGateScript,

    [string]$ChinesePolicyBaseRef,

    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Agentization.Common.ps1")

function Invoke-GateProcess {
    param(
        [string]$WorkingDirectory,
        [string]$FilePath,
        [string[]]$Arguments
    )

    Push-Location $WorkingDirectory
    try {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $output = & $FilePath @Arguments 2>&1
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($exitCode -ne 0) {
            throw "模块门禁命令失败（exit=$exitCode）：$FilePath $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
        }
    }
    finally {
        Pop-Location
    }
}

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
if ($ModuleId -eq "M00") {
    if ($GateType -ne "Final") {
        throw "M00-I.1 只允许执行 Final 门禁。"
    }
    if (-not [string]::IsNullOrWhiteSpace($AdditionalGateScript)) {
        throw "M00-I.1 禁止追加范围外门禁脚本。"
    }
}
else {
    Get-AgentModuleDefinition -ModuleId $ModuleId | Out-Null
}
if ($GateType -eq "Phase") {
    if ([string]::IsNullOrWhiteSpace($ReleaseId) -or [string]::IsNullOrWhiteSpace($Slice) -or -not (Test-AgentReleaseCheckpoint -ModuleId $ModuleId -ReleaseId $ReleaseId -Slice $Slice)) {
        throw "该模块/切片不在四阶段计划的中间检查点白名单中：$ModuleId/$ReleaseId/$Slice"
    }
}

$commands = New-Object System.Collections.Generic.List[object]
$commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "git"; Arguments = @("diff", "--check") })
if ($ModuleId -eq "M00") {
    $pythonExecutable = Resolve-AgentPythonExecutable -RepositoryPath $root
    $commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "powershell"; Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "`$r=Invoke-Pester -Script 'scripts/agentization/tests' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_agent_runtime_config.py", "tests/test_gateway_app_import_profile.py", "tests/test_profile_config.py", "tests/test_openapi_operation_ids.py", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "ruff", "check", "pixelflow/agent_runtime", "app/gateway/profile_config.py", "app/gateway/app.py", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_agent_runtime_config.py", "tests/test_gateway_app_import_profile.py", "tests/test_profile_config.py", "tests/test_openapi_operation_ids.py") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test:agent-runtime-contracts") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "lint") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "build-prod") })
}
elseif ($ModuleId -eq "M00-A") {
    $pythonExecutable = Resolve-AgentPythonExecutable -RepositoryPath $root
    $commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "powershell"; Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "`$r=Invoke-Pester -Script 'scripts/agentization/tests' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_openapi_operation_ids.py", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "ruff", "check", "pixelflow/agent_runtime", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py") })
}
elseif ($ModuleId -eq "M00-B") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "node"; Arguments = @("--test", "tests") })
}
elseif ($ModuleId -match "^M0[1-6]$") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = "python"; Arguments = @("-m", "pytest", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = "python"; Arguments = @("-m", "ruff", "check", "pixelflow", "app/gateway", "tests") })
}
elseif ($ModuleId -match "^M(0[7-9]|1[0-2])$") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "lint") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "build-prod") })
}
elseif ($ModuleId -eq "M13") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = "python"; Arguments = @("-m", "pytest", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "build-prod") })
}

if ($PlanOnly) {
    return $commands
}
if ([string]::IsNullOrWhiteSpace($ChinesePolicyBaseRef)) {
    throw "执行模块门禁必须提供 ChinesePolicyBaseRef，以检查本次将进入远端的全部提交与变更。"
}
& (Join-Path $PSScriptRoot "Test-ChineseEngineeringPolicy.ps1") -RepositoryPath $root -BaseRef $ChinesePolicyBaseRef -HeadRef "HEAD" | Out-Null
foreach ($command in $commands) {
    Invoke-GateProcess -WorkingDirectory $command.WorkingDirectory -FilePath $command.FilePath -Arguments $command.Arguments
}
if ($AdditionalGateScript) {
    Invoke-AgentGateScript -GateScript $AdditionalGateScript -RepositoryPath $root | Out-Null
}

[pscustomobject]@{
    Passed = $true
    ModuleId = $ModuleId
    GateType = $GateType
    ReleaseId = $ReleaseId
    CommandCount = $commands.Count
}
