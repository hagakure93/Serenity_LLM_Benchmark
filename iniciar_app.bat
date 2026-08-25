@echo off
cd /d "%~dp0"
echo Liberando el puerto 5000 si estuviera ocupado...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -Expand OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo Arrancando la app...
start "Prueba de carga - Chatbot" "%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe" app.py
powershell -NoProfile -Command "for($i=0;$i -lt 20;$i++){ try { Invoke-WebRequest http://127.0.0.1:5000 -UseBasicParsing -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }"
echo.
echo ============================================================
echo   App corriendo en:  http://127.0.0.1:5000
echo   Se abrio una ventana aparte con el log.
echo   Para PARARLA: ejecuta  parar_app.bat  (o cierra esa ventana)
echo ============================================================
start "" "http://127.0.0.1:5000"
