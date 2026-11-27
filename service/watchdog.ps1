# 看门狗：服务不在就拉起，在就秒退。计划任务每 5 分钟调用一次。
# 覆盖场景：进程崩溃、被误杀、登录自启任务失败——任何"端口没人听"的情况。
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $py)) { exit 1 }

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

if (Test-PortInUse $port) { exit 0 }

Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
    "-NoProfile",
    "-WindowStyle", "Hidden",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$root\service\run_hidden.ps1`""
)
