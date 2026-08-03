@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_matrix_auto_cutter.ps1"
if errorlevel 1 (
  echo.
  echo Matrix Auto Cutter konnte nicht gestartet werden.
  pause
  exit /b 1
)
exit /b 0
