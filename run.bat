@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match 'cloudflared_quick') -or ($_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'app\.server') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Start-Sleep -Milliseconds 700"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\first_run_setup.ps1"
if errorlevel 1 (
    echo First-run setup encountered an error. Check output above.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%~dp0.venv\Scripts\pythonw.exe' -ArgumentList '-m','app.server' -WorkingDirectory '%~dp0' -WindowStyle Hidden;" ^
  "for ($i = 0; $i -lt 20; $i++) { try { $null = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 1; break } catch { Start-Sleep -Milliseconds 400 } };" ^
  "Start-Process 'http://localhost:8000';" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$null = $ws.Popup('Smart Home AV restarted silently in the background and opened in your browser.' + [Environment]::NewLine + [Environment]::NewLine + 'Verify anytime via:' + [Environment]::NewLine + '  - http://localhost:8000' + [Environment]::NewLine + '  - check_status.bat' + [Environment]::NewLine + '  - logs\server.log', 5, 'Smart Home AV Monitor', 64)"
exit /b 0
