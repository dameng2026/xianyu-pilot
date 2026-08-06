@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

REM ---------- 首次部署自动初始化 ----------
REM 缺少 .env / venv / node_modules 时，自动运行本地初始化向导
REM （生成 .env 与随机密钥、bcrypt hash、创建 MySQL 库与用户、安装全部依赖）
set NEED_INIT=0
if not exist ".env" set NEED_INIT=1
if not exist ".venv\Scripts\python.exe" set NEED_INIT=1
if not exist "apps\crawler\node_modules" set NEED_INIT=1
if not exist "apps\web\node_modules" set NEED_INIT=1

if "%NEED_INIT%"=="1" (
    echo [*] 首次部署，运行本地初始化向导（自动生成配置、建库、安装依赖）...
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-local.ps1
    if errorlevel 1 (
        echo [X] 本地初始化向导执行失败 1>&2
        exit /b 1
    )
) else (
    echo [OK] 环境已就绪（.env / .venv / node_modules 均存在）
)

where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python was not found in PATH. 请安装 Python 3.10+：https://www.python.org/downloads/ 1>&2
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [X] npm was not found in PATH. 请安装 Node.js 22+：https://nodejs.org/ 1>&2
    exit /b 1
)

rem Keep the open-source development stack isolated from other local products.
set "XYA_WEB_PORT=15176"
set "XYA_WEB_HOST=127.0.0.1"
set "SERVER_HOST=127.0.0.1"
set "SERVER_PORT=15177"
set "CRAWLER_PORT=15178"
set "PORT=%CRAWLER_PORT%"
set "HOST=127.0.0.1"
set "CRAWLER_BASE_URL=http://127.0.0.1:%CRAWLER_PORT%"
set "CRAWLER_SERVICE_URL=http://127.0.0.1:%CRAWLER_PORT%"
set "VITE_API_PROXY_TARGET=http://127.0.0.1:%SERVER_PORT%"
set "VITE_UPLOAD_PROXY_TARGET=http://127.0.0.1:%SERVER_PORT%"
set "CORS_ALLOWED_ORIGINS=http://127.0.0.1:%XYA_WEB_PORT%,http://localhost:%XYA_WEB_PORT%"
set "CRAWLER_ALLOWED_ORIGINS=http://127.0.0.1:%XYA_WEB_PORT%,http://localhost:%XYA_WEB_PORT%"

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\local-dev.ps1 preflight
if errorlevel 1 (
    echo One or more isolated local ports are already in use. Nothing was started.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\local-dev.ps1 start
set "START_EXIT=%ERRORLEVEL%"
if not "%START_EXIT%"=="0" (
    echo Local stack startup failed. See output\local-dev for service logs.
    exit /b %START_EXIT%
)

echo Local stack is ready. Use status-local.bat or stop-local.bat to manage it.
exit /b 0
