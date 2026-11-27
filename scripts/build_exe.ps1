# 一键构建单文件 exe：AI-Bid-Agent.exe（双击即用，无需 Python/Docker）
# 用法：pwsh -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 产物：dist\AI-Bid-Agent.exe（约 40-60MB）；build\、*.spec 为中间产物可删
# 注意：构建前确保 frontend/dist 已存在（cd frontend; npm install; npm run build）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

if (-not (Test-Path "$root\frontend\dist\index.html")) {
    Write-Error "frontend/dist 不存在——先构建前端：cd frontend; npm install; npm run build"
}

& "$root\.venv\Scripts\python.exe" -m PyInstaller `
    --noconfirm --clean `
    --onefile --console `
    --name AI-Bid-Agent `
    --paths $root `
    --add-data "$root\frontend\dist;frontend/dist" `
    --add-data "$root\config\config.yaml;config" `
    --add-data "$root\config\secrets.yaml.example;config" `
    --hidden-import app.main `
    --collect-all uvicorn `
    "$root\launcher.py"

if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller 构建失败（exit=$LASTEXITCODE）" }

$exe = "$root\dist\AI-Bid-Agent.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "`n构建完成: $exe（$mb MB）"
    Write-Host "首次运行会在 exe 旁生成 config\（可编辑）、data\、output\"
} else {
    Write-Error "未找到产物 $exe"
}
