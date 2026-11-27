# 卸载服务化：删除两个计划任务。正在运行的进程不受影响，下面给出停止方法。
$removed = @()
foreach ($tn in "AI-Bid-Agent", "AI-Bid-Agent-Watchdog") {
    $out = schtasks /Delete /TN $tn /F 2>&1
    if ($LASTEXITCODE -eq 0) { $removed += $tn }
    "$tn : $(($out | Select-Object -Last 1))" | Write-Host
}
if ($removed.Count -eq 2) {
    Write-Host "`n已卸载：登录自启与看门狗均不再运行。"
} elseif ($removed.Count -eq 0) {
    Write-Host "`n两个任务都不存在（可能尚未注册或已卸载）。"
}
Write-Host "如需停掉正在常驻的进程："
Write-Host '  Get-NetTCPConnection -LocalPort 8000 -State Listen | Select -ExpandProperty OwningProcess -Unique | % { Stop-Process -Id $_ -Force }'
