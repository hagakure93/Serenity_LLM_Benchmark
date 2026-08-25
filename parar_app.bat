@echo off
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -Expand OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }; Write-Host 'App detenida.' } else { Write-Host 'No habia nada corriendo en el puerto 5000.' }"
