@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-desktop-demo.ps1" %*
if errorlevel 1 (
  echo.
  echo Local demo could not start. See the error above.
  pause
  exit /b 1
)
endlocal
