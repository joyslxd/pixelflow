[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$BaseRef,

    [string]$HeadRef = "HEAD"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Agentization.Common.ps1")

# 用途：兼容中文门禁启用前已审核并推送的历史；影响：只跳过精确 SHA，后续提交仍执行全部中文检查。
$GrandfatheredCommits = @{
    "0af72ff6993e9e67636f21e8e16d641411702d67" = "M00-A.1 创建时尚未启用中文工程门禁，现经用户明确批准保留历史。"
}

function Test-MachineDirectiveComment {
    param([string]$Comment)

    $trimmed = $Comment.Trim()
    $patterns = @(
        "^#!",
        "^(noqa|type:\s*ignore|pyright:|mypy:|ruff:)",
        "^(eslint|prettier|istanbul|c8)\b",
        "^SPDX-",
        "^(http|https)://",
        "^[\-=_*#/\.]+$"
    )
    foreach ($pattern in $patterns) {
        if ($trimmed -match $pattern) {
            return $true
        }
    }
    return $false
}

function Get-AddedCommentText {
    param(
        [string]$Line,
        [string]$Extension
    )

    $trimmed = $Line.TrimStart([char]0xFEFF).Trim()
    if ($trimmed -match "^(#|//|/\*+|\*+|<!--|'''|`"`"`")\s*(.*)$") {
        return $matches[2].Trim()
    }
    if ($Extension -in @(".py", ".ps1", ".psm1", ".sh", ".yml", ".yaml", ".toml")) {
        if ($Line -match "\s+#\s*(.+)$") {
            return $matches[1].Trim()
        }
    }
    if ($Extension -in @(".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) {
        if ($Line -match "\s+//\s*(.+)$") {
            return $matches[1].Trim()
        }
    }
    return $null
}

function Get-AddedLineEntries {
    param([string[]]$DiffLines)

    $entries = New-Object System.Collections.Generic.List[object]
    $currentLine = 0
    $insideHunk = $false
    foreach ($line in $DiffLines) {
        if ($line -match "^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@") {
            $currentLine = [int]$matches[1]
            $insideHunk = $true
            continue
        }
        if (-not $insideHunk -or $line.StartsWith("+++")) {
            continue
        }
        if ($line.StartsWith("+")) {
            $entries.Add([pscustomobject]@{
                LineNumber = $currentLine
                Text = $line.Substring(1)
            })
            $currentLine++
        }
        elseif ($line.StartsWith("-")) {
            continue
        }
        elseif (-not $line.StartsWith("\")) {
            $currentLine++
        }
    }
    return $entries.ToArray()
}

function Get-AddedLineNumbers {
    param([string[]]$DiffLines)

    return @(
        (Get-AddedLineEntries -DiffLines $DiffLines) |
            ForEach-Object { $_.LineNumber }
    )
}

function Get-LineOriginCommit {
    param(
        [string]$RepositoryPath,
        [string]$HeadRef,
        [string]$RelativePath,
        [int]$LineNumber
    )

    $result = Invoke-AgentGit -RepositoryPath $RepositoryPath -Arguments @(
        "blame",
        "--ignore-revs-file=",
        "--porcelain",
        $HeadRef,
        "-L",
        "$LineNumber,$LineNumber",
        "--",
        $RelativePath
    ) -AllowFailure
    if ($result.ExitCode -ne 0 -or $result.Output.Count -eq 0) {
        return $null
    }
    $origin = ($result.Output[0] -split "\s+")[0].TrimStart("^")
    if ($origin -notmatch "^[0-9a-fA-F]{40}$") {
        return $null
    }
    return $origin.ToLowerInvariant()
}

function Test-GrandfatheredLine {
    param(
        [string]$RepositoryPath,
        [string]$HeadRef,
        [string]$RelativePath,
        [int]$LineNumber
    )

    $origin = Get-LineOriginCommit `
        -RepositoryPath $RepositoryPath `
        -HeadRef $HeadRef `
        -RelativePath $RelativePath `
        -LineNumber $LineNumber
    if ([string]::IsNullOrWhiteSpace($origin)) {
        return $false
    }
    return $GrandfatheredCommits.ContainsKey($origin)
}

function Get-TripleQuotedLineNumbers {
    param([string[]]$Lines)

    $numbers = New-Object System.Collections.Generic.List[int]
    $delimiter = $null
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        $line = $Lines[$index]
        if ($delimiter) {
            $numbers.Add($index + 1)
            if ($line.Contains($delimiter)) {
                $delimiter = $null
            }
            continue
        }
        $doubleIndex = $line.IndexOf('"""')
        $singleIndex = $line.IndexOf("'''")
        if ($doubleIndex -lt 0 -and $singleIndex -lt 0) {
            continue
        }
        if ($doubleIndex -ge 0 -and ($singleIndex -lt 0 -or $doubleIndex -lt $singleIndex)) {
            $candidateDelimiter = '"""'
            $startIndex = $doubleIndex
        }
        else {
            $candidateDelimiter = "'''"
            $startIndex = $singleIndex
        }
        $numbers.Add($index + 1)
        $remaining = $line.Substring($startIndex + 3)
        if (-not $remaining.Contains($candidateDelimiter)) {
            $delimiter = $candidateDelimiter
        }
    }
    return $numbers.ToArray()
}

function Test-AdjacentChineseConfigComment {
    param(
        [string[]]$Lines,
        [int]$Index
    )

    $current = $Lines[$Index]
    if ($current -match "#\s*(.+)$") {
        $inline = $matches[1]
        if ((Test-AgentContainsChinese -Text $inline) -and $inline -match "用途" -and $inline -match "影响") {
            return $true
        }
    }
    for ($cursor = $Index - 1; $cursor -ge 0; $cursor--) {
        $candidate = $Lines[$cursor].Trim()
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if ($candidate.StartsWith("#")) {
            return (Test-AgentContainsChinese -Text $candidate) -and $candidate -match "用途" -and $candidate -match "影响"
        }
        return $false
    }
    return $false
}

function Get-JsonLeafPaths {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [string]$Prefix = ""
    )

    $paths = @()
    if ($null -eq $Value) {
        return @($Prefix)
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            $child = if ($Prefix) { "$Prefix.$($property.Name)" } else { $property.Name }
            $paths += Get-JsonLeafPaths -Value $property.Value -Prefix $child
        }
        return $paths
    }
    if ($Value -is [System.Collections.IList] -and -not ($Value -is [string])) {
        if ($Value.Count -eq 0) {
            return @($Prefix)
        }
        for ($index = 0; $index -lt $Value.Count; $index++) {
            $paths += Get-JsonLeafPaths -Value $Value[$index] -Prefix "$Prefix[$index]"
        }
        return $paths
    }
    return @($Prefix)
}

function Get-SchemaDescription {
    param(
        [Parameter(Mandatory = $true)]$Schema,
        [Parameter(Mandatory = $true)][string]$LeafPath
    )

    $node = $Schema
    $segments = $LeafPath -split "\."
    foreach ($segmentWithIndex in $segments) {
        $segment = $segmentWithIndex -replace "\[\d+\]$", ""
        if ($null -eq $node.properties) {
            return $null
        }
        $property = $node.properties.PSObject.Properties[$segment]
        if ($null -eq $property) {
            return $null
        }
        $node = $property.Value
        if ($segmentWithIndex -match "\[\d+\]$" -and $null -ne $node.items) {
            $node = $node.items
        }
    }
    return $node.description
}

$root = Resolve-AgentRepositoryRoot -RepositoryPath $RepositoryPath
$violations = New-Object System.Collections.Generic.List[string]
$commitResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("rev-list", "--reverse", "$BaseRef..$HeadRef")
$commits = @($commitResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
foreach ($commit in $commits) {
    if ($GrandfatheredCommits.ContainsKey($commit)) {
        continue
    }
    $subject = (Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "-s", "--format=%s", $commit)).Output -join "`n"
    $body = (Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "-s", "--format=%b", $commit)).Output -join "`n"
    if (-not (Test-AgentContainsChinese -Text $subject)) {
        $violations.Add("提交 $commit 的标题缺少中文主体语义：$subject")
    }
    if (-not [string]::IsNullOrWhiteSpace($body) -and -not (Test-AgentContainsChinese -Text $body)) {
        $violations.Add("提交 $commit 的正文缺少中文主体语义。")
    }
}

$pathResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("diff", "--name-only", "--diff-filter=ACMR", $BaseRef, $HeadRef)
$changedPaths = @($pathResult.Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$codeExtensions = @(".py", ".ps1", ".psm1", ".sh", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
foreach ($relativePath in $changedPaths) {
    $extension = [System.IO.Path]::GetExtension($relativePath).ToLowerInvariant()
    if ($codeExtensions -contains $extension) {
        $diff = Invoke-AgentGit -RepositoryPath $root -Arguments @("diff", "--unified=0", "--no-color", $BaseRef, $HeadRef, "--", $relativePath)
        $addedEntries = @(Get-AddedLineEntries -DiffLines $diff.Output)
        foreach ($entry in $addedEntries) {
            $comment = Get-AddedCommentText -Line $entry.Text -Extension $extension
            if ($null -eq $comment -or [string]::IsNullOrWhiteSpace($comment)) {
                continue
            }
            if ((Test-MachineDirectiveComment -Comment $comment) -or (Test-AgentContainsChinese -Text $comment)) {
                continue
            }
            if (
                $comment -match "[A-Za-z]{2,}" -and
                -not (Test-GrandfatheredLine `
                    -RepositoryPath $root `
                    -HeadRef $HeadRef `
                    -RelativePath $relativePath `
                    -LineNumber $entry.LineNumber)
            ) {
                $violations.Add("人工注释缺少中文说明：$relativePath 第 $($entry.LineNumber) 行 -> $comment")
            }
        }
        if ($extension -eq ".py") {
            $headLines = @((Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "$HeadRef`:$relativePath")).Output)
            $addedLineNumbers = @(Get-AddedLineNumbers -DiffLines $diff.Output)
            $tripleQuotedLineNumbers = @(Get-TripleQuotedLineNumbers -Lines $headLines)
            foreach ($lineNumber in $addedLineNumbers) {
                if ($tripleQuotedLineNumbers -notcontains $lineNumber) {
                    continue
                }
                $docstringText = $headLines[$lineNumber - 1].Trim().Replace('"""', "").Replace("'''", "").Trim()
                if (
                    $docstringText -match "[A-Za-z]{2,}" -and
                    -not (Test-AgentContainsChinese -Text $docstringText) -and
                    -not (Test-GrandfatheredLine `
                        -RepositoryPath $root `
                        -HeadRef $HeadRef `
                        -RelativePath $relativePath `
                        -LineNumber $lineNumber)
                ) {
                    $violations.Add("docstring 缺少中文说明：$relativePath 第 $lineNumber 行")
                }
            }
        }
    }

    if ($extension -in @(".yml", ".yaml", ".toml", ".ini", ".conf", ".properties")) {
        $contentResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "$HeadRef`:$relativePath")
        $lines = @($contentResult.Output)
        for ($index = 0; $index -lt $lines.Count; $index++) {
            $line = $lines[$index]
            $isYamlLeaf = $extension -in @(".yml", ".yaml") -and $line -match "^\s*(?:-\s*)?[A-Za-z0-9_.-]+\s*:\s*([^#\s].*?)\s*(#.*)?$"
            $isEqualsLeaf = $extension -in @(".toml", ".ini", ".conf", ".properties") -and $line -match "^\s*[A-Za-z0-9_.-]+\s*=\s*([^#;\s].*?)\s*([#;].*)?$"
            if (($isYamlLeaf -or $isEqualsLeaf) -and -not (Test-AdjacentChineseConfigComment -Lines $lines -Index $index)) {
                $violations.Add("叶子配置缺少紧邻的中文用途和影响说明：$relativePath 第 $($index + 1) 行")
            }
        }
    }

    $isJsonConfig = $extension -eq ".json" -and ($relativePath -match "(?i)(config|settings|manifest)[^/\\]*\.json$" -or $relativePath -match "(?i)(^|/)(package|plugin|langgraph|tsconfig[^/\\]*)\.json$") -and $relativePath -notmatch "(?i)\.schema\.json$" -and $relativePath -notmatch "(?i)(^|/)(tests?|fixtures?)/"
    if ($isJsonConfig) {
        $configText = (Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "$HeadRef`:$relativePath")).Output -join "`n"
        $config = $configText | ConvertFrom-Json
        $directory = [System.IO.Path]::GetDirectoryName($relativePath) -replace "\\", "/"
        $baseName = [System.IO.Path]::GetFileNameWithoutExtension($relativePath)
        $schemaPath = if ([string]::IsNullOrWhiteSpace($directory)) { "$baseName.schema.json" } else { "$directory/$baseName.schema.json" }
        $schemaResult = Invoke-AgentGit -RepositoryPath $root -Arguments @("show", "$HeadRef`:$schemaPath") -AllowFailure
        if ($schemaResult.ExitCode -ne 0) {
            $violations.Add("JSON 配置缺少同目录 schema：$relativePath -> $schemaPath")
            continue
        }
        $schema = (($schemaResult.Output -join "`n") | ConvertFrom-Json)
        foreach ($leafPath in (Get-JsonLeafPaths -Value $config)) {
            $description = Get-SchemaDescription -Schema $schema -LeafPath $leafPath
            if (-not (Test-AgentContainsChinese -Text $description) -or $description -notmatch "用途" -or $description -notmatch "影响") {
                $violations.Add("JSON 配置键缺少中文用途和影响 description：$relativePath -> $leafPath")
            }
        }
    }
}

if ($violations.Count -gt 0) {
    throw "中文工程规范检查失败：`n- $($violations -join "`n- ")"
}

[pscustomobject]@{
    Passed = $true
    CommitCount = $commits.Count
    ChangedPathCount = $changedPaths.Count
    BaseRef = $BaseRef
    HeadRef = $HeadRef
}
