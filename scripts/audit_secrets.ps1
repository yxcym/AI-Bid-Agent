<#
凭据外泄审计：检查真实凭据是否出现在任何"可公开或被共享"的位置。

设计原则：脚本不含任何密钥字面量——运行时从 secrets.yaml 与环境变量读出待查字符串再比对，
因此把它提交进公开仓库是安全的。凭据无论放在文件还是环境变量里，都能被本脚本覆盖。

用法
    pwsh -File scripts\audit_secrets.ps1

退出码
    0 = 无阻断项（提示项可能有）
    1 = 有阻断项，需人工处置

九项检查
    1  secrets.yaml 是否被 gitignore 排除
    2  全部提交里是否出现过授权码（文件来源与环境变量来源都查）
    3  全部提交里是否有形如真实值的凭证字段
    4  当前已跟踪文件里是否出现授权码或真实邮箱
    5  本机磁盘（排除依赖与产物）是否另有副本
    6  PowerShell 命令历史是否留过授权码或邮箱
    7  远端仓库可见性
    8  历史提交里的真实邮箱（提示项，历史需重写才能清除）
    9  凭据是否存在于环境变量/注册表（提示项，同用户进程均可读取）
#>
#requires -Version 7
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepoRoot

# ---------- 收集待查字符串（不回显具体值） ----------
$secretsPath = Join-Path $RepoRoot 'config/secrets.yaml'

function Read-SecretsFile([string]$path) {
    $result = @{ tokens = @(); mails = @() }
    if (-not (Test-Path -LiteralPath $path)) { return $result }
    $raw = Get-Content -LiteralPath $path -Raw
    if ($raw -match '(?m)^\s*password\s*:\s*["'']?([^"''\r\n#]+)') {
        $result.tokens += $Matches[1].Trim()
    }
    $result.mails += [regex]::Matches($raw, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}') |
        ForEach-Object { $_.Value }
    return $result
}

function Read-EnvCreds {
    $result = @{ tokens = @(); mails = @() }
    $pw = $env:BIDAGENT_SMTP_PASSWORD
    if ($pw -and $pw.Trim()) { $result.tokens += $pw.Trim() }
    foreach ($v in @($env:BIDAGENT_SMTP_USERNAME, $env:BIDAGENT_SMTP_TO)) {
        if ($v) {
            $result.mails += [regex]::Matches($v, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}') |
                ForEach-Object { $_.Value }
        }
    }
    return $result
}

$fromFile = Read-SecretsFile $secretsPath
$fromEnv = Read-EnvCreds
$Tokens = @(@($fromFile.tokens) + @($fromEnv.tokens) | Where-Object { $_ } | Sort-Object -Unique)
$Mails = @(@($fromFile.mails) + @($fromEnv.mails) | Where-Object { $_ } | Sort-Object -Unique)

$results = [System.Collections.Generic.List[object]]::new()
function Add-Result([string]$Id, [string]$Name, [string]$Status, [string]$Detail) {
    $results.Add([pscustomobject]@{ 编号 = $Id; 检查项 = $Name; 结果 = $Status; 说明 = $Detail })
}
function Add-Check([string]$Id, [string]$Name, [bool]$Pass, [string]$Detail) {
    Add-Result $Id $Name $(if ($Pass) { 'PASS' } else { 'FAIL' }) $Detail
}

if (-not $Tokens.Count) {
    Write-Host '提示：未读到任何授权码（secrets.yaml 缺失、字段为空，且环境变量未设），第 2/4/5/6 项将跳过' -ForegroundColor Yellow
}

$allRevs = @(git rev-list --all 2>$null)
$commitCount = $allRevs.Count

# ---------- 1. gitignore 覆盖 ----------
$ignored = git check-ignore -v config/secrets.yaml 2>$null
Add-Check '1' 'secrets.yaml 被 gitignore 排除' ([bool]$ignored) $(if ($ignored) { $ignored } else { '未被忽略——凭据随时可能被提交' })

# ---------- 2. 全历史里的授权码 ----------
if ($Tokens.Count) {
    $hits = @()
    foreach ($token in $Tokens) {
        foreach ($rev in $allRevs) {
            $found = git grep -I -F -l -- $token $rev 2>$null
            if ($found) { $hits += $found }
        }
    }
    $hits = @($hits | Sort-Object -Unique)
    $lengths = ($Tokens | ForEach-Object { $_.Length }) -join '/'
    Add-Check '2' "全部 $commitCount 个提交中的授权码" ($hits.Count -eq 0) `
        $(if ($hits.Count -eq 0) { "零命中（逐个 revision 扫描，密文长度 $lengths）" } else { ($hits | Select-Object -First 5) -join '; ' })
}
else { Add-Check '2' '全部提交中的授权码' $true '跳过（无授权码可查）' }

# ---------- 3. 全历史里的凭证字段 ----------
$credPattern = 'password\s*[:=]\s*[''"]?[A-Za-z0-9+/=_\-]{12,}|(api_?key|auth_?code|token|secret)\s*[:=]\s*[''"]?[A-Za-z0-9+/=_\-]{16,}'
$credHits = @(git grep -I -n -E -- $credPattern $allRevs 2>$null)
Add-Check '3' '全部提交中的凭证字段' ($credHits.Count -eq 0) `
    $(if ($credHits.Count -eq 0) { '零命中' } else { ($credHits | Select-Object -First 5) -join '; ' })

# ---------- 4. 已跟踪文件里的授权码与邮箱 ----------
$trackedHits = @()
foreach ($token in $Tokens) { $trackedHits += @(git grep -I -F -l -- $token 2>$null) }
foreach ($m in $Mails) { $trackedHits += @(git grep -I -F -l -- $m 2>$null) }
$trackedHits = @($trackedHits | Where-Object { $_ } | Sort-Object -Unique)
Add-Check '4' '已跟踪文件中的授权码/真实邮箱' ($trackedHits.Count -eq 0) `
    $(if ($trackedHits.Count -eq 0) { "零命中（比对 $($Tokens.Count + $Mails.Count) 个敏感串）" } else { $trackedHits -join '; ' })

# ---------- 5. 本机磁盘副本 ----------
$skipDir = '\\(\.venv|node_modules|\.git|frontend\\dist|output|data|__pycache__)\\'
$scanFiles = Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Include *.yaml,*.yml,*.json,*.ini,*.ps1,*.bat,*.py,*.md,*.txt,*.env,*.cfg -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $skipDir -and $_.FullName -ne $secretsPath }
$diskHits = @()
foreach ($token in $Tokens) { $diskHits += @($scanFiles | Select-String -SimpleMatch -Pattern $token -ErrorAction SilentlyContinue) }
foreach ($m in $Mails) { $diskHits += @($scanFiles | Select-String -SimpleMatch -Pattern $m -ErrorAction SilentlyContinue) }
$diskHits = @($diskHits | Where-Object { $_ })
Add-Check '5' '本机磁盘副本（secrets.yaml 之外）' ($diskHits.Count -eq 0) `
    $(if ($diskHits.Count -eq 0) { "零命中（扫描 $($scanFiles.Count) 个文本文件）" } else { ($diskHits | Select-Object -First 5 | ForEach-Object { "$($_.Path):$($_.LineNumber)" }) -join '; ' })

# ---------- 6. PowerShell 命令历史 ----------
$histPath = (Get-PSReadLineOption).HistorySavePath
$histHits = @()
if (Test-Path -LiteralPath $histPath) {
    foreach ($token in $Tokens) {
        $histHits += @(Select-String -LiteralPath $histPath -SimpleMatch -Pattern $token -ErrorAction SilentlyContinue)
    }
    foreach ($m in $Mails) {
        $histHits += @(Select-String -LiteralPath $histPath -SimpleMatch -Pattern $m -ErrorAction SilentlyContinue)
    }
}
$histHits = @($histHits | Where-Object { $_ })
Add-Check '6' 'PowerShell 命令历史' ($histHits.Count -eq 0) `
    $(if ($histHits.Count -eq 0) { "零命中（$histPath）" } else { "$($histHits.Count) 条命中，建议清历史：Remove-Item -LiteralPath '$histPath'" })

# ---------- 7. 远端可见性 ----------
$remote = (git remote get-url origin 2>$null)
if ($remote -match 'github\.com[:/]') {
    Add-Check '7' '远端仓库可见性' $true "origin=$remote（公开仓库：文档中一律用占位符，不写真实邮箱/姓名/路径）"
}
else { Add-Check '7' '远端仓库可见性' $true "origin=$remote（非 GitHub 或未配置）" }

# ---------- 8. 历史提交里的真实邮箱（提示项） ----------
$histMailHits = @()
foreach ($m in $Mails) {
    foreach ($rev in $allRevs) {
        $found = git grep -I -F -l -- $m $rev 2>$null
        if ($found) { $histMailHits += $found }
    }
}
$histMailHits = @($histMailHits | Sort-Object -Unique)
if ($histMailHits.Count -eq 0) {
    Add-Result '8' '历史提交中的真实邮箱' 'PASS' '零命中（当前版本与历史都干净）'
}
else {
    $revs = @($histMailHits | ForEach-Object { ($_ -split ':')[0] } | Sort-Object -Unique)
    Add-Result '8' '历史提交中的真实邮箱' 'NOTE' `
        "命中 $($histMailHits.Count) 处／$($revs.Count) 个版本；当前版本已脱敏，历史需重写才能清除（不影响退出码）"
}

# ---------- 9. 环境变量/注册表里的凭据（提示项） ----------
$envTokenHits = @()
foreach ($scope in @('User', 'Machine')) {
    $envValues = [Environment]::GetEnvironmentVariables($scope)
    foreach ($k in $envValues.Keys) {
        if ($k -notmatch 'BIDAGENT_') { continue }
        $v = [string]$envValues[$k]
        foreach ($token in $Tokens) { if ($token -and $v.Contains($token)) { $envTokenHits += "$scope\$k" } }
    }
}
$envTokenHits = @($envTokenHits | Sort-Object -Unique)
if ($envTokenHits.Count -eq 0) {
    Add-Result '9' '环境变量/注册表中的授权码' 'PASS' '零命中（凭据只在受 ACL 保护的文件里）'
}
else {
    Add-Result '9' '环境变量/注册表中的授权码' 'NOTE' `
        "存在于 $($envTokenHits -join ', ')（注册表 HKCU/HKLM Environment，同用户任意进程可读，子进程继承）——安全性与 ACL 文件相当，不更高"
}

# ---------- 汇总 ----------
$failed = @($results | Where-Object { $_.结果 -eq 'FAIL' })
$notes = @($results | Where-Object { $_.结果 -eq 'NOTE' })
$results | Format-Table -AutoSize -Wrap | Out-String | Write-Host
Write-Host ''
foreach ($n in $notes) { Write-Host "提示：$($n.编号) $($n.检查项) —— $($n.说明)" -ForegroundColor Yellow }
if ($notes.Count) { Write-Host '' }
if ($failed.Count -eq 0) {
    Write-Host "审计通过：$($results.Count) 项无阻断（$($notes.Count) 项提示）。" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "审计未通过：$($failed.Count) 项需处置 → $((($failed | ForEach-Object { $_.编号 + ' ' + $_.检查项 }) -join '；'))" -ForegroundColor Red
    exit 1
}
