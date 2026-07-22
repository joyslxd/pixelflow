Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AgentizationRoot = Split-Path -Parent $PSScriptRoot
$PolicyScript = Join-Path $AgentizationRoot "Test-ChineseEngineeringPolicy.ps1"
$script:TemporaryRoots = @()

function Invoke-TestGit {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & git -C $RepositoryPath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "测试仓库 Git 命令失败：git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return @($output | ForEach-Object { "$_" })
}

function New-PolicyTestRepository {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("pixelflow-agentization-policy-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $root | Out-Null
    $script:TemporaryRoots += $root

    & git init $root | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("config", "user.name", "策略测试") | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("config", "user.email", "policy-tests@example.invalid") | Out-Null
    Set-Content -LiteralPath (Join-Path $root "README.md") -Value "测试仓库" -Encoding UTF8
    Invoke-TestGit -RepositoryPath $root -Arguments @("add", "README.md") | Out-Null
    Invoke-TestGit -RepositoryPath $root -Arguments @("commit", "-m", "初始化：建立策略测试仓库") | Out-Null
    return $root
}

function Add-TestCommit {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath,

        [Parameter(Mandatory = $true)]
        [string]$RelativePath,

        [Parameter(Mandatory = $true)]
        [string]$Content,

        [Parameter(Mandatory = $true)]
        [string]$Message,

        [string]$Body
    )

    $path = Join-Path $RepositoryPath $RelativePath
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Set-Content -LiteralPath $path -Value $Content -Encoding UTF8
    Invoke-TestGit -RepositoryPath $RepositoryPath -Arguments @("add", "--", $RelativePath) | Out-Null
    if ([string]::IsNullOrWhiteSpace($Body)) {
        Invoke-TestGit -RepositoryPath $RepositoryPath -Arguments @("commit", "-m", $Message) | Out-Null
    }
    else {
        Invoke-TestGit -RepositoryPath $RepositoryPath -Arguments @("commit", "-m", $Message, "-m", $Body) | Out-Null
    }
}

function Remove-TestRoot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理临时目录之外的测试路径：$resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Describe "中文工程规范门禁" {
    AfterEach {
        foreach ($root in $script:TemporaryRoots) {
            Remove-TestRoot -Path $root
        }
        $script:TemporaryRoots = @()
    }

    It "提供独立的中文规范检查入口" {
        (Test-Path -LiteralPath $PolicyScript) | Should Be $true
    }

    It "拒绝只有英文语义的提交标题或正文" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "plain.txt" -Content "value" -Message "test: english only"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw

        Invoke-TestGit -RepositoryPath $repository -Arguments @("reset", "--hard", "HEAD~1") | Out-Null
        Add-TestCommit -RepositoryPath $repository -RelativePath "plain.txt" -Content "value" -Message "测试：提交标题使用中文" -Body "english body only"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw
    }

    It "接受中文提交和中文解释性注释" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "sample.py" -Content "# 说明：返回稳定测试值`ndef value():`n    return 1" -Message "测试：增加中文说明示例" -Body "验证提交正文也使用中文。"

        $result = & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD"

        $result.Passed | Should Be $true
    }

    It "拒绝英文人工注释但允许最小机器指令例外" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "sample.py" -Content "# explain the behavior`ndef value():`n    return 1" -Message "测试：验证英文注释拒绝"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw

        Invoke-TestGit -RepositoryPath $repository -Arguments @("reset", "--hard", "HEAD~1") | Out-Null
        Add-TestCommit -RepositoryPath $repository -RelativePath "sample.py" -Content "import os  # noqa: F401" -Message "测试：验证机器指令例外"

        $result = & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD"
        $result.Passed | Should Be $true
    }

    It "拒绝多行英文 Python docstring" {
        $repository = New-PolicyTestRepository
        $content = "def value():`n    `"`"`"`n    English explanation.`n    `"`"`"`n    return 1"
        Add-TestCommit -RepositoryPath $repository -RelativePath "sample.py" -Content $content -Message "测试：验证多行英文文档拒绝"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw
    }

    It "要求 YAML 叶子配置项紧邻中文用途和影响说明" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "config.test.yml" -Content "feature:`n  enabled: true" -Message "测试：验证配置说明拒绝"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw

        Invoke-TestGit -RepositoryPath $repository -Arguments @("reset", "--hard", "HEAD~1") | Out-Null
        Add-TestCommit -RepositoryPath $repository -RelativePath "config.test.yml" -Content "feature:`n  # 用途：控制测试功能；影响：开启后仅测试仓库启用该功能。`n  enabled: true" -Message "测试：补齐配置逐项说明"

        $result = & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD"
        $result.Passed | Should Be $true
    }

    It "要求 YAML 数组对象中的叶子配置也有逐项说明" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "config.test.yml" -Content "features:`n  - enabled: true" -Message "测试：验证数组配置说明拒绝"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw
    }

    It "要求 JSON 配置通过同目录 schema 逐键提供中文 description" {
        $repository = New-PolicyTestRepository
        Add-TestCommit -RepositoryPath $repository -RelativePath "settings.json" -Content '{"enabled":true}' -Message "测试：验证 JSON 配置说明拒绝"

        { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw

        $schema = '{"type":"object","properties":{"enabled":{"type":"boolean","description":"用途：控制测试功能；影响：开启后启用测试行为。"}}}'
        Set-Content -LiteralPath (Join-Path $repository "settings.schema.json") -Value $schema -Encoding UTF8
        Invoke-TestGit -RepositoryPath $repository -Arguments @("add", "settings.schema.json") | Out-Null
        Invoke-TestGit -RepositoryPath $repository -Arguments @("commit", "--amend", "--no-edit") | Out-Null

        $result = & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD"
        $result.Passed | Should Be $true
    }

    It "把 package plugin 和 langgraph JSON 识别为配置" {
        foreach ($name in @("package.json", "plugin.json", "langgraph.json")) {
            $repository = New-PolicyTestRepository
            Add-TestCommit -RepositoryPath $repository -RelativePath $name -Content '{"enabled":true}' -Message "测试：验证常见 JSON 配置识别"

            { & $PolicyScript -RepositoryPath $repository -BaseRef "HEAD~1" -HeadRef "HEAD" } | Should Throw
        }
    }
}
