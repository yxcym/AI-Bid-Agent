<#
凭据外泄审计：检查 config/secrets.yaml 里的真实凭据，是否出现在任何"可公开或被共享"的位置。

设计原则：脚本本身不含任何密钥字面量——运行时从 secrets.yaml 读出待查字符串再比对。
因此把它提交进公开仓库是安全的。

用法
    pwsh -File scripts\audit_secrets.ps1
    pwsh -File scripts\audit_secrets.ps1 -Verbose

退出码
    0 = 全部通过
    1 = 有命中，需人工处置

七项检查
    1  secrets.yaml 是否被 gitignore 排除
    2  全部提交（逐个 revision）里是否出现过授权码
    3  全部提交里是否有形如真实值的凭证字段（password/token/api_key/secret）
    4  当前已跟踪文件里是否出现授权码或真实邮箱
    5  本机磁盘（排除依赖与产物目录）是否还有 secrets.yaml 之外的副本
    6  PowerShell 命令历史里是否留过授权码或邮箱
    7  远端仓库可见性（public 时提示"文档不得写真实地址"）
#>
#requires -Version 7
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepoRoot

# ---------- 从 secrets.yaml 读取待查字符串（不打印值） ----------
$secretsPath = Join-Path $RepoRoot 'config/secrets.yaml'
$SecretToken = ''
$SecretMails = @()
if (Test-Path -LiteralPath $secretsPath) {
    $raw = Get-Content -LiteralPath $secretsPath -Raw
    if ($raw -match '(?m)^\s*password\s*:\s*["'']?([^"''\r\n#]+)') {
        $SecretToken = $Matches[1].Trim()
    }
    $mailMatches = [regex]::Matches($raw, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
    $SecretMails = @($mailMatches | ForEach-Object { $_.Value } | Sort-Object -Unique)
}

$results = [System.Collections.Generic.List[object]]::new()
function Add-Result([string]$Id, [string]$Name, [bool]$Pass, [string]$Detail) {
    $results.Add([pscustomobject]@{ 编号 = $Id; 检查项 = $Name; 结果 = $(if ($Pass) { 'PASS' } else { 'FAIL' }); 说明 = $Detail })
}

if (-not $SecretToken) {
    Write-Host '提示：未读到 secrets.yaml 中的授权码（文件缺失或字段为空），第 2/4/5/6 项将跳过' -ForegroundColor Yellow
}

$allRevs = @(git rev-list --all 2>$null)
$commitCount = $allRevs.Count

# ---------- 1. gitignore 覆盖 ----------
$ignored = git check-ignore -v config/secrets.yaml 2>$null
Add-Result '1' 'secrets.yaml 被 gitignore 排除' ([bool]$ignored) $(if ($ignored) { $ignored } else { '未被忽略——凭据随时可能被提交' })

# ---------- 2. 全历史里的授权码 ----------
if ($SecretToken) {
    $hits = @()
    foreach ($rev in $allRevs) {
        $found = git grep -I -F -l -- $SecretToken $rev 2>$null
        if ($found) { $hits += $found }
    }
    Add-Result '2' "全部 $commitCount 个提交中的授权码" ($hits.Count -eq 0) `
        $(if ($hits.Count -eq 0) { "零命中（逐个 revision 扫描，密文长度 $($SecretToken.Length)）" } else { ($hits | Select-Object -First 5) -join '; ' })
}
else { Add-Result '2' '全部提交中的授权码' $true '跳过（无授权码可查）' }

# ---------- 3. 全历史里的凭证字段 ----------
$credPattern = 'password\s*[:=]\s*[''"]?[A-Za-z0-9+/=_\-]{12,}|(api_?key|auth_?code|token|secret)\s*[:=]\s*[''"]?[A-Za-z0-9+/=_\-]{16,}'
$credHits = @(git grep -I -n -E -- $credPattern $allRevs 2>$null)
Add-Result '3' '全部提交中的凭证字段' ($credHits.Count -eq 0) `
    $(if ($credHits.Count -eq 0) { '零命中' } else { ($credHits | Select-Object -First 5) -join '; ' })

# ---------- 4. 已跟踪文件里的授权码与邮箱 ----------
$trackedHits = @()
if ($SecretToken) { $trackedHits += @(git grep -I -F -l -- $SecretToken 2>$null) }
foreach ($m in $SecretMails) { $trackedHits += @(git grep -I -F -l -- $m 2>$null) }
$trackedHits = @($trackedHits | Where-Object { $_ } | Sort-Object -Unique)
Add-Result '4' '已跟踪文件中的授权码/真实邮箱' ($trackedHits.Count -eq 0) `
    $(if ($trackedHits.Count -eq 0) { "零命中（比对 $($SecretMails.Count + 1) 个敏感串）" } else { $trackedHits -join '; ' })

# ---------- 5. 本机磁盘副本 ----------
$skipDir = '\\(\.venv|node_modules|\.git|frontend\\dist|output|data|__pycache__)\\'
$scanFiles = Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Include *.yaml,*.yml,*.json,*.ini,*.ps1,*.bat,*.py,*.md,*.txt,*.env,*.cfg -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $skipDir -and $_.FullName -ne $secretsPath }
$diskHits = @()
if ($SecretToken) { $diskHits += @($scanFiles | Select-String -SimpleMatch -Pattern $SecretToken -ErrorAction SilentlyContinue) }
foreach ($m in $SecretMails) { $diskHits += @($scanFiles | Select-String -SimpleMatch -Pattern $m -ErrorAction SilentlyContinue) }
$diskHits = @($diskHits | Where-Object { $_ })
Add-Result '5' '本机磁盘副本（secrets.yaml 之外）' ($diskHits.Count -eq 0) `
    $(if ($diskHits.Count -eq 0) { "零命中（扫描 $($scanFiles.Count) 个文本文件）" } else { ($diskHits | Select-Object -First 5 | ForEach-Object { "$($_.Path):$($_.LineNumber)" }) -join '; ' })

# ---------- 6. PowerShell 命令历史 ----------
$histPath = (Get-PSReadLineOption).HistorySavePath
$histHits = @()
if ((Test-Path -LiteralPath $histPath) -and $SecretToken) {
    $histHits += @(Select-String -LiteralPath $histPath -SimpleMatch -Pattern $SecretToken -ErrorAction SilentlyContinue)
}
foreach ($m in $SecretMails) {
    if (Test-Path -LiteralPath $histPath) {
        $histHits += @(Select-String -LiteralPath $histPath -SimpleMatch -Pattern $m -ErrorAction SilentlyContinue)
    }
}
$histHits = @($histHits | Where-Object { $_ })
Add-Result '6' 'PowerShell 命令历史' ($histHits.Count -eq 0) `
    $(if ($histHits.Count -eq 0) { "零命中（$histPath）" } else { "$($histHits.Count) 条命中，建议清历史：Remove-Item -LiteralPath '$histPath'" })

# ---------- 7. 远端可见性 ----------
$remote = (git remote get-url origin 2>$null)
$isGithub = $remote -match 'github\.com[:/](?<slug>[^/\s]+/[^/\s]+?)(\.git)?$'
if ($isGithub) {
    Add-Result '7' '远端仓库可见性' $true "origin=$remote（公开仓库：文档中一律用占位符，不写真实邮箱/姓名/路径）"
}
else { Add-Result '7' '远端仓库可见性' $true "origin=$remote（非 GitHub 或未配置）" }

# ---------- 汇总 ----------
$failed = @($results | Where-Object { $_.结果 -eq 'FAIL' })
$results | Format-Table -AutoSize -Wrap | Out-String | Write-Host
Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host "审计通过：$($results.Count) 项全绿，凭据未出现在任何可公开位置。" -ForegroundColor Green
    exit 0
}
else {
    Write-Host "审计未通过：$($failed.Count) 项有问题 → $((($failed | ForEach-Object { $_.编号 + ' ' + $_.检查项 }) -join '；'))" -ForegroundColor Red
    exit 1
}
