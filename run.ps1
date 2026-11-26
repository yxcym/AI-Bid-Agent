# 一键启动 AI-Bid-Agent
# 用法：在项目根目录执行  .\run.ps1
# 或者右键“用 PowerShell 运行”

Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "未找到虚拟环境，正在创建..." -ForegroundColor Yellow
    $sysPython = "D:\Misc\Program Files\Python312\python.exe"
    if (-not (Test-Path -LiteralPath $sysPython)) {
        Write-Host "找不到系统 Python，请先修改本脚本里的 \$sysPython 路径" -ForegroundColor Red
        exit 1
    }
    & $sysPython -m venv (Join-Path $PSScriptRoot ".venv")
    & $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

$env:PYTHONIOENCODING = "utf-8"
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
