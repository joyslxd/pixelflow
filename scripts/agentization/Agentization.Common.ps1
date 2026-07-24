Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-AgentGit {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [switch]$AllowFailure
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $rawOutput = & git -C $RepositoryPath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $output = @($rawOutput | ForEach-Object { "$_" })
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "Git 命令失败（exit=$exitCode）：git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
    }
}

function Resolve-AgentRepositoryRoot {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RepositoryPath)

    if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
        throw "仓库目录不存在：$RepositoryPath"
    }
    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("rev-parse", "--show-toplevel")
    return [System.IO.Path]::GetFullPath($result.Output[-1])
}

function Resolve-AgentPythonExecutable {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RepositoryPath)

    $root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
    $candidateRoots = New-Object System.Collections.Generic.List[string]
    $candidateRoots.Add($root)

    $commonResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("rev-parse", "--git-common-dir")
    $commonDirectory = $commonResult.Output[-1]
    if (-not [System.IO.Path]::IsPathRooted($commonDirectory)) {
        $commonDirectory = Join-Path $root $commonDirectory
    }
    $primaryRoot = Split-Path -Parent ([System.IO.Path]::GetFullPath($commonDirectory))
    if ($primaryRoot -and $primaryRoot -ne $root) {
        $candidateRoots.Add($primaryRoot)
    }

    foreach ($candidateRoot in $candidateRoots) {
        foreach ($relativePath in @("backend\.venv\Scripts\python.exe", "backend/.venv/bin/python")) {
            $candidate = Join-Path $candidateRoot $relativePath
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return [System.IO.Path]::GetFullPath($candidate)
            }
        }
    }

    throw "未找到项目 Python 虚拟环境：请先创建 backend/.venv；模块门禁禁止回退到 PATH Python。"
}

function Assert-AgentCleanWorktree {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryPath,
        [string[]]$AllowedRelativePaths = @()
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("status", "--porcelain=v1", "--untracked-files=all")
    $unexpected = @()
    foreach ($line in $result.Output) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $relativePath = $line.Substring(3).Trim('"') -replace "\\", "/"
        $allowed = $false
        foreach ($candidate in $AllowedRelativePaths) {
            if ($relativePath -eq ($candidate -replace "\\", "/")) {
                $allowed = $true
                break
            }
        }
        if (-not $allowed) {
            $unexpected += $line
        }
    }
    if ($unexpected.Count -gt 0) {
        throw "工作区存在未授权修改，自动化按 fail-closed 停止：`n$($unexpected -join [Environment]::NewLine)"
    }
}

function Test-AgentAncestor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryPath,
        [Parameter(Mandatory = $true)][string]$Ancestor,
        [Parameter(Mandatory = $true)][string]$Descendant
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("merge-base", "--is-ancestor", $Ancestor, $Descendant) -AllowFailure
    if ($result.ExitCode -eq 0) {
        return $true
    }
    if ($result.ExitCode -eq 1) {
        return $false
    }
    throw "无法判断祖先关系：$Ancestor -> $Descendant"
}

function Get-AgentRemoteBranchSha {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryPath,
        [Parameter(Mandatory = $true)][string]$RemoteName,
        [Parameter(Mandatory = $true)][string]$Branch
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("ls-remote", "--heads", $RemoteName, "refs/heads/$Branch")
    if ($result.Output.Count -eq 0 -or [string]::IsNullOrWhiteSpace($result.Output[-1])) {
        throw "远端分支不存在：$RemoteName/$Branch"
    }
    return ($result.Output[-1] -split "\s+")[0]
}

function New-AgentCandidateIdentity {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Prefix)

    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $suffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
    return "$Prefix-$timestamp-$suffix"
}

function New-AgentCandidateWorktree {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryPath,
        [Parameter(Mandatory = $true)][string]$CandidateRoot,
        [Parameter(Mandatory = $true)][string]$CandidateBranch,
        [Parameter(Mandatory = $true)][string]$StartRef
    )

    $root = [System.IO.Path]::GetFullPath($CandidateRoot)
    if (-not (Test-Path -LiteralPath $root)) {
        New-Item -ItemType Directory -Path $root -Force | Out-Null
    }
    $leaf = ($CandidateBranch -replace "[^A-Za-z0-9._-]", "-")
    $path = Join-Path $root $leaf
    if (Test-Path -LiteralPath $path) {
        throw "候选 worktree 路径已存在：$path"
    }
    Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @("worktree", "add", "-b", $CandidateBranch, $path, $StartRef) | Out-Null
    return $path
}

function Invoke-AgentGateScript {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$GateScript,
        [Parameter(Mandatory = $true)][string]$RepositoryPath
    )

    if (-not (Test-Path -LiteralPath $GateScript -PathType Leaf)) {
        throw "门禁脚本不存在：$GateScript"
    }
    $result = & $GateScript -RepositoryPath $RepositoryPath
    if ($LASTEXITCODE -ne 0) {
        throw "门禁脚本返回失败：$GateScript"
    }
    return $result
}

function Get-AgentModuleDefinition {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ModuleId)

    $definitions = @{
        "M00-A" = @{ Branch = "codex/agent-0.8.4-m00-a"; Worktree = "m00-a"; Status = "docs/agentization/status/M00-A-status.md" }
        "M00-B" = @{ Branch = "codex/agent-0.8.4-m00-b"; Worktree = "m00-b"; Status = "docs/agentization/status/M00-B-status.md" }
        "M01" = @{ Branch = "codex/agent-0.8.4-m01-runtime-store"; Worktree = "m01-runtime-store"; Status = "docs/agentization/status/M01-status.md" }
        "M02" = @{ Branch = "codex/agent-0.8.4-m02-graph-kernel"; Worktree = "m02-graph-kernel"; Status = "docs/agentization/status/M02-status.md" }
        "M03" = @{ Branch = "codex/agent-0.8.4-m03-context-runtime"; Worktree = "m03-context-runtime"; Status = "docs/agentization/status/M03-status.md" }
        "M04" = @{ Branch = "codex/agent-0.8.4-m04-context-compaction"; Worktree = "m04-context-compaction"; Status = "docs/agentization/status/M04-status.md" }
        "M05" = @{ Branch = "codex/agent-0.8.4-m05-supervisor"; Worktree = "m05-supervisor"; Status = "docs/agentization/status/M05-status.md" }
        "M06" = @{ Branch = "codex/agent-0.8.4-m06-external-jobs"; Worktree = "m06-external-jobs"; Status = "docs/agentization/status/M06-status.md" }
        "M07" = @{ Branch = "codex/agent-0.8.4-m07-web-runtime"; Worktree = "m07-web-runtime"; Status = "docs/agentization/status/M07-status.md" }
        "M08" = @{ Branch = "codex/agent-0.8.4-m08-image-workflow"; Worktree = "m08-image-workflow"; Status = "docs/agentization/status/M08-status.md" }
        "M09" = @{ Branch = "codex/agent-0.8.4-m09-ppt-workflow"; Worktree = "m09-ppt-workflow"; Status = "docs/agentization/status/M09-status.md" }
        "M10" = @{ Branch = "codex/agent-0.8.4-m10-video-analysis"; Worktree = "m10-video-analysis"; Status = "docs/agentization/status/M10-status.md" }
        "M11" = @{ Branch = "codex/agent-0.8.4-m11-video-workflow"; Worktree = "m11-video-workflow"; Status = "docs/agentization/status/M11-status.md" }
        "M12" = @{ Branch = "codex/agent-0.8.4-m12-workspace-ui"; Worktree = "m12-workspace-ui"; Status = "docs/agentization/status/M12-status.md" }
        "M13" = @{ Branch = "codex/agent-0.8.4-m13-integration"; Worktree = "m13-integration"; Status = "docs/agentization/status/M13-status.md" }
    }
    if (-not $definitions.ContainsKey($ModuleId)) {
        throw "未知模块 ID 或误用了切片 ID：$ModuleId"
    }
    $definition = $definitions[$ModuleId]
    return [pscustomobject]@{
        ModuleId = $ModuleId
        Branch = $definition.Branch
        Worktree = $definition.Worktree
        Status = $definition.Status
    }
}

function Get-AgentMarkdownField {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][string]$Field
    )

    $escaped = [regex]::Escape($Field)
    $match = [regex]::Match($Content, "(?m)^-\s+$escaped：\s*(.+?)\s*$")
    if (-not $match.Success) {
        return $null
    }
    return $match.Groups[1].Value.Trim().Trim('`')
}

function Set-AgentMarkdownField {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Field,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $escaped = [regex]::Escape($Field)
    $pattern = "(?m)^-\s+$escaped：\s*.+?$"
    $replacement = "- $Field：``$Value``"
    if ([regex]::IsMatch($content, $pattern)) {
        $content = [regex]::Replace($content, $pattern, $replacement, 1)
    }
    else {
        $content = $content.TrimEnd() + [Environment]::NewLine + $replacement + [Environment]::NewLine
    }
    [System.IO.File]::WriteAllText($Path, $content, $encoding)
}

function Test-AgentReleaseCheckpoint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ModuleId,
        [Parameter(Mandatory = $true)][string]$ReleaseId,
        [Parameter(Mandatory = $true)][string]$Slice
    )

    $allowed = @{
        "M12|R1|M12.3" = $true
        "M13|R1|M13.1" = $true
        "M13|R2|M13.2" = $true
        "M13|R3|M13.3" = $true
        "M13|R4|M13.4" = $true
    }
    return $allowed.ContainsKey("$ModuleId|$ReleaseId|$Slice")
}

function Test-AgentContainsChinese {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)

    return -not [string]::IsNullOrEmpty($Text) -and $Text -match "[\u3400-\u9fff]"
}
