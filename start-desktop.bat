@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" apps\api\run_desktop.py %*
) else if exist ".venv-ci\Scripts\python.exe" (
  ".venv-ci\Scripts\python.exe" apps\api\run_desktop.py %*
) else (
  echo Install Python dependencies into .venv first. See docs\desktop-sqlite-foundation.md
  exit /b 1
)
exit /b %errorlevel%
