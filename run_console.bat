@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\first_run_setup.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$running = Get-CimInstance Win32_Process | Where-Object { ($_.Name -in @('python.exe','pythonw.exe')) -and ($_.CommandLine -match 'app\.server') };" ^
  "if ($running) {" ^
  "  Write-Host 'Silent background server is ALREADY running (keeping it alive). Showing live logs... (Press Ctrl+C to close console safely)' -ForegroundColor Cyan;" ^
  "  & '%~dp0scripts\check_status.ps1';" ^
  "  if (Test-Path '%~dp0logs\server.log') { Get-Content '%~dp0logs\server.log' -Tail 30 -Wait };" ^
  "} else {" ^
  "  & '%~dp0.venv\Scripts\python.exe' -m app.server;" ^
  "}"
pause
