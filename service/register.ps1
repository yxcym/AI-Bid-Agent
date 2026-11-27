# 注册开机自启 + 看门狗两个计划任务（当前用户上下文，无需管理员）。
# 用 XML 注册而非 schtasks 参数行：因为要 ExecutionTimeLimit=PT0S——
# schtasks 默认"任务最长运行 3 天"，常驻服务会被它掐死，必须显式取消。
$root = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"

$xmlAutostart = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>AI-Bid-Agent 后台常驻：登录自动启动（端口已被占用时自动退出，不双开）</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$user</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$user</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$root\service\run_hidden.ps1"</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$xmlWatchdog = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>AI-Bid-Agent 看门狗：每 5 分钟检查服务端口，不在就拉起</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2020-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$user</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$root\service\watchdog.ps1"</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$f1 = Join-Path $env:TEMP "bidagent_task_autostart.xml"
$f2 = Join-Path $env:TEMP "bidagent_task_watchdog.xml"
$xmlAutostart | Out-File -LiteralPath $f1 -Encoding Unicode
$xmlWatchdog | Out-File -LiteralPath $f2 -Encoding Unicode

schtasks /Create /TN "AI-Bid-Agent" /XML $f1 /F 2>&1 | Out-String | Write-Host
schtasks /Create /TN "AI-Bid-Agent-Watchdog" /XML $f2 /F 2>&1 | Out-String | Write-Host

"`n=== 核验 ==="
schtasks /Query /TN "AI-Bid-Agent" 2>&1 | Select-Object -Last 3 | Out-String | Write-Host
schtasks /Query /TN "AI-Bid-Agent-Watchdog" 2>&1 | Select-Object -Last 3 | Out-String | Write-Host
Remove-Item -LiteralPath $f1, $f2 -Force -ErrorAction SilentlyContinue
Write-Host "`n已注册。卸载：pwsh -File service\unregister.ps1"
