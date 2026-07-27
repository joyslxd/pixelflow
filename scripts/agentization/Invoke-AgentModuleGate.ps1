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

$backendModuleIds = @("M00", "M00-A", "M01", "M02", "M03", "M04", "M05", "M06", "M13")
$pythonExecutable = $null
if ($backendModuleIds -contains $ModuleId) {
    $pythonExecutable = Resolve-AgentPythonExecutable -RepositoryPath $root
}

$commands = New-Object System.Collections.Generic.List[object]
$commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "git"; Arguments = @("diff", "--check") })
if ($pythonExecutable) {
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = (Join-Path $root "backend")
            FilePath = $pythonExecutable
            Arguments = @(
                "-c",
                "import sys; print(sys.version); raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
            )
        }
    )
}
if ($ModuleId -eq "M00") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "powershell"; Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "`$r=Invoke-Pester -Script 'scripts/agentization/tests' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_agent_runtime_config.py", "tests/test_gateway_app_import_profile.py", "tests/test_profile_config.py", "tests/test_openapi_operation_ids.py", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "ruff", "check", "pixelflow/agent_runtime", "app/gateway/profile_config.py", "app/gateway/app.py", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_agent_runtime_config.py", "tests/test_gateway_app_import_profile.py", "tests/test_profile_config.py", "tests/test_openapi_operation_ids.py") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test:agent-runtime-contracts") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "lint") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "build-prod") })
}
elseif ($ModuleId -eq "M00-A") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = $root; FilePath = "powershell"; Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "`$r=Invoke-Pester -Script 'scripts/agentization/tests' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py", "tests/test_openapi_operation_ids.py", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "ruff", "check", "pixelflow/agent_runtime", "tests/test_agent_runtime_contracts.py", "tests/test_agent_runtime_legacy_invariants.py") })
}
elseif ($ModuleId -eq "M00-B") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "node"; Arguments = @("--test", "tests") })
}
elseif ($ModuleId -eq "M01") {
    $m01Tests = @(
        "tests/test_agent_runtime_conversation_cas.py",
        "tests/test_agent_runtime_event_outbox.py",
        "tests/test_agent_runtime_migration.py",
        "tests/test_agent_runtime_repositories.py",
        "tests/test_agent_runtime_turn_inbox.py",
        "tests/test_agent_runtime_contracts.py",
        "tests/test_agent_runtime_config.py",
        "tests/test_agent_runtime_legacy_invariants.py",
        "tests/test_pixelflow_task_store.py",
        "tests/test_pixelflow_conversations_router.py",
        "tests/test_owner_isolation.py",
        "tests/test_harness_boundary.py",
        "tests/test_pixelflow_jianying_draft_router.py",
        "tests/test_openapi_operation_ids.py"
    )
    $m01RuffPaths = @(
        "app/gateway/routers/pixelflow_conversations.py",
        "packages/harness/deerflow/persistence/migrations/versions/20260724_01_agent_runtime_tables.py",
        "packages/harness/deerflow/persistence/migrations/versions/20260724_02_conversation_revision.py",
        "packages/harness/deerflow/persistence/models/__init__.py",
        "pixelflow/agent_runtime/persistence",
        "pixelflow/tasks/__init__.py",
        "pixelflow/tasks/model.py",
        "pixelflow/tasks/mysql.py",
        "pixelflow/tasks/store.py"
    )
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest") + $m01Tests + @("-q") })
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = (Join-Path $root "backend")
            FilePath = $pythonExecutable
            Arguments = @("-m", "ruff", "check") + $m01RuffPaths + $m01Tests
        }
    )
}
elseif ($ModuleId -eq "M02") {
    $m02Tests = @(
        "tests/test_agent_runtime_graph_state.py",
        "tests/test_agent_runtime_graph_dispatcher.py",
        "tests/test_agent_runtime_graph_interrupts.py",
        "tests/test_agent_runtime_graph_composition.py",
        "tests/test_checkpointer.py",
        "tests/test_checkpointer_none_fix.py",
        "tests/test_run_manager.py",
        "tests/test_gateway_runtime_cleanup.py",
        "tests/test_gateway_run_recovery.py",
        "tests/test_harness_boundary.py",
        "tests/test_agent_runtime_legacy_invariants.py",
        "tests/test_pixelflow_task_store.py",
        "tests/test_openapi_operation_ids.py",
        "tests/test_langgraph_auth.py"
    )
    $m02RuffPaths = @(
        "app/gateway/deps.py",
        "app/gateway/pixelflow_agent_runtime.py",
        "pixelflow/agent_runtime/graph"
    )
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = $root
            FilePath = "powershell"
            Arguments = @(
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "`$r=Invoke-Pester -Script 'scripts/agentization/tests/BranchAutomation.Tests.ps1' -PassThru; if (`$r.FailedCount -gt 0) { exit 1 }"
            )
        }
    )
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest") + $m02Tests + @("-q") })
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = (Join-Path $root "backend")
            FilePath = $pythonExecutable
            Arguments = @("-m", "ruff", "check") + $m02RuffPaths + $m02Tests
        }
    )
}
elseif ($ModuleId -eq "M03") {
    $m03Tests = @(
        "tests/test_agent_runtime_context_externalizer.py",
        "tests/test_agent_runtime_context_assembler.py",
        "tests/test_agent_runtime_token_meter.py",
        "tests/test_agent_runtime_context_profiles.py",
        "tests/test_agent_runtime_contracts.py",
        "tests/test_agent_runtime_config.py",
        "tests/test_profile_config.py",
        "tests/test_pixelflow_memory_helper.py"
    )
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest") + $m03Tests + @("-q") })
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = (Join-Path $root "backend")
            FilePath = $pythonExecutable
            Arguments = @("-m", "ruff", "check", "pixelflow/agent_runtime/context") + $m03Tests
        }
    )
}
elseif ($ModuleId -eq "M04") {
    $m04RuntimeTests = @(
        "tests/test_agent_runtime_compaction_coordinator.py",
        "tests/test_agent_runtime_compaction_events.py",
        "tests/test_agent_runtime_compaction_queue.py",
        "tests/test_agent_runtime_config.py",
        "tests/test_agent_runtime_context_assembler.py",
        "tests/test_agent_runtime_context_externalizer.py",
        "tests/test_agent_runtime_context_profiles.py",
        "tests/test_agent_runtime_contracts.py",
        "tests/test_agent_runtime_conversation_cas.py",
        "tests/test_agent_runtime_event_outbox.py",
        "tests/test_agent_runtime_legacy_invariants.py",
        "tests/test_agent_runtime_migration.py",
        "tests/test_agent_runtime_repositories.py",
        "tests/test_agent_runtime_structured_summaries.py",
        "tests/test_agent_runtime_summary_builder.py",
        "tests/test_agent_runtime_summary_verification.py",
        "tests/test_agent_runtime_token_meter.py",
        "tests/test_agent_runtime_turn_inbox.py"
    )
    # Alembic fileConfig 会改写进程级 logger；边界测试使用独立进程保留原日志断言。
    $m04BoundaryTests = @(
        "tests/test_summarization_middleware.py",
        "tests/test_dynamic_context_middleware.py",
        "tests/test_harness_boundary.py"
    )
    $m04RuffPaths = @(
        "packages/harness/deerflow/persistence/migrations/versions/20260725_03_compaction_locks.py",
        "pixelflow/agent_runtime/context",
        "pixelflow/agent_runtime/persistence"
    )
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest") + $m04RuntimeTests + @("-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest") + $m04BoundaryTests + @("-q") })
    $commands.Add(
        [pscustomobject]@{
            WorkingDirectory = (Join-Path $root "backend")
            FilePath = $pythonExecutable
            Arguments = @("-m", "ruff", "check") + $m04RuffPaths + $m04RuntimeTests + $m04BoundaryTests
        }
    )
}
elseif ($ModuleId -match "^M0(5|6)$") {
    throw "模块 $ModuleId 尚未建立权威测试清单；禁止回退到后端全量门禁，请先由模块 owner 按 test-matrix.md 配置。"
}
elseif ($ModuleId -match "^M(07|12)$") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "lint") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "build-prod") })
}
elseif ($ModuleId -match "^M(0[8-9]|1[0-1])$") {
    throw "模块 $ModuleId 尚未建立包含后端范围的权威测试清单；禁止只运行前端门禁，请先由模块 owner 按 test-matrix.md 配置。"
}
elseif ($ModuleId -eq "M13") {
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "pytest", "-q") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "backend"); FilePath = $pythonExecutable; Arguments = @("-m", "ruff", "check", ".") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test:agent-runtime-contracts") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "test") })
    $commands.Add([pscustomobject]@{ WorkingDirectory = (Join-Path $root "web"); FilePath = "corepack"; Arguments = @("pnpm", "lint") })
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
