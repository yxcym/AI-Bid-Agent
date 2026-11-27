# 后台常驻入口：计划任务（登录自启 / 看门狗）调用，也可手动 pwsh 运行。
# 已有实例在跑则直接退出——保证"手动双击"与"自动常驻"两种方式不打架。
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location -LiteralPath $root

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "未找到虚拟环境解释器：$py"
    exit 1
}

# 端口以 config.yaml 为准（app.port），不写死
$port = [int](& $py -c "import sys; sys.path.insert(0, r'$root'); from app.config import load_config; print(load_config().app.port)")

function Test-PortInUse([int]$p) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $t = $c.BeginConnect('127.0.0.1', $p, $null, $null)
        return ($t.AsyncWaitHandle.WaitOne(600) -and $c.Connected)
    } catch {
        return $false
    } finally {
        $c.Close()
    }
}

if (Test-PortInUse $port) {
    Write-Host "端口 $port 已有实例在跑，本次不重复启动。"
    exit 0
}

& $py -m uvicorn app.main:app --host 127.0.0.1 --port $port
