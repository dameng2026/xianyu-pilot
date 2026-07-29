@echo off
REM 一键启动脚本（Windows）：自动初始化 secrets、拉取镜像、启动服务、等待健康
REM 用法：
REM   .\start.bat              拉取预构建镜像并启动（推荐）
REM   .\start.bat --build      本地源码构建并启动
REM   .\start.bat --no-pull    跳过镜像拉取（用本地已有的镜像）
REM
REM 错误兜底策略：
REM   - Docker Desktop 未运行 → 自动启动并等待就绪
REM   - secrets 生成失败 → setup-wizard.ps1 只需系统内置 RNG
REM   - bcrypt hash 生成 → 统一由 api 镜像生成（零额外下载，必定成功）
REM   - 镜像拉取失败 → 自动回退到本地源码构建
REM   - 端口被占用 → 自动查找可用端口并更新 .env
REM   - 磁盘空间不足 → 提前警告并给出清理建议
REM   - 容器启动失败 → 自动诊断并显示异常服务日志
REM   - 健康检查超时 → 分阶段显示进度，失败时自动诊断
setlocal enabledelayedexpansion

set DO_BUILD=0
set DO_PULL=1
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--build" (set DO_BUILD=1 & set DO_PULL=0 & shift & goto parse_args)
if /i "%~1"=="--no-pull" (set DO_PULL=0 & shift & goto parse_args)
echo 未知参数：%~1（支持：--build 本地构建 / --no-pull 跳过拉取） 1>&2
exit /b 1
:args_done

cd /d "%~dp0"

REM ---------- 1. 前置依赖检查 + 自动启动 Docker Desktop ----------
where docker >nul 2>&1
if errorlevel 1 (
  echo [X] 未检测到 Docker，请先安装 Docker Desktop：https://docs.docker.com/desktop/install/windows-install/ 1>&2
  echo     安装后重启电脑，再运行本脚本 1>&2
  exit /b 1
)
docker compose version >nul 2>&1
if errorlevel 1 (
  echo [X] 未检测到 Docker Compose v2，请升级 Docker Desktop 到最新版 1>&2
  exit /b 1
)

REM 检测 Docker 引擎是否运行，未运行则自动启动 Docker Desktop
docker info >nul 2>&1
if errorlevel 1 (
  echo [!] Docker 引擎未运行，尝试自动启动 Docker Desktop...
  REM 常见安装路径
  set "DOCKER_DESKTOP_PATH="
  if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
    set "DOCKER_DESKTOP_PATH=C:\Program Files\Docker\Docker\Docker Desktop.exe"
  ) else if exist "C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe" (
    set "DOCKER_DESKTOP_PATH=C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"
  )

  if "!DOCKER_DESKTOP_PATH!"=="" (
    echo [X] 未找到 Docker Desktop，请手动启动后重试 1>&2
    exit /b 1
  )

  echo [*] 正在启动 Docker Desktop...
  start "" "!DOCKER_DESKTOP_PATH!"
  echo [*] 等待 Docker 引擎就绪（最长 90 秒）...
  set /a WAIT_COUNT=0
  :wait_docker
  timeout /t 3 /nobreak >nul
  set /a WAIT_COUNT+=3
  docker info >nul 2>&1
  if not errorlevel 1 goto docker_ready
  if !WAIT_COUNT! GEQ 90 (
    echo [X] Docker Desktop 启动超时，请手动启动后重试 1>&2
    echo     提示：检查右下角 Docker 图标是否变绿 1>&2
    exit /b 1
  )
  printf .
  goto wait_docker
  :docker_ready
  echo.
  echo [OK] Docker 引擎已就绪
)

REM ---------- 1.5 磁盘空间预检查 ----------
for /f "usebackq tokens=3" %%a in (`dir /-c "%CD%" 2^>nul ^| findstr /r /c:"^[0-9].*可用"`) do set "AVAIL_BYTES=%%a"
if defined AVAIL_BYTES (
  REM 字节数转 GB（批处理不支持浮点，用整数除法近似）
  set /a AVAIL_GB=!AVAIL_BYTES! / 1073741824
  if !AVAIL_GB! LSS 5 (
    echo [!] 磁盘可用空间不足：!AVAIL_GB!GB（建议 ≥10GB） 1>&2
    echo     镜像构建 + 数据可能需要 5-10GB，空间不足会导致构建中途失败 1>&2
    echo     清理建议：docker system prune -a --volumes 1>&2
  ) else if !AVAIL_GB! LSS 10 (
    echo [*] 磁盘可用空间：!AVAIL_GB!GB（建议 ≥10GB，首次构建可能紧张）
  ) else (
    echo [OK] 磁盘可用空间：!AVAIL_GB!GB
  )
)

REM ---------- 2. 首次启动初始化 ----------
set NEED_INIT=0
if not exist ".env" set NEED_INIT=1
if not exist "secrets" set NEED_INIT=1
if exist "secrets" (
  dir /b "secrets" 2>nul | findstr "." >nul
  if errorlevel 1 set NEED_INIT=1
)
REM 检查关键 secrets 文件（admin-password-hash 允许为空，由 api 镜像生成）
for %%f in (mysql-root-password mysql-app-password mysql-migration-password redis-password jwt-secret cookie-crypto-secret internal-api-token) do (
  if not exist "secrets\%%f" set NEED_INIT=1
)
if not exist "secrets\admin-password-hash" set NEED_INIT=1

if "%NEED_INIT%"=="1" (
  echo [*] 首次启动，运行初始化向导...
  powershell -ExecutionPolicy Bypass -File "scripts\setup-wizard.ps1"
  if errorlevel 1 (
    echo [X] 初始化向导执行失败 1>&2
    exit /b 1
  )
)

REM ---------- 3. 读取 .env 中的 WEB_PORT ----------
set WEB_PORT=8080
set WEB_BIND=0.0.0.0
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    set "key=%%a"
    set "value=%%b"
    if /i "!key!"=="WEB_PORT" set "WEB_PORT=!value!"
    if /i "!key!"=="WEB_BIND_ADDRESS" set "WEB_BIND=!value!"
  )
)

REM ---------- 3.5 端口占用检查 + 自动选择可用端口 ----------
:check_port
netstat -ano | findstr ":!WEB_PORT! " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [!] 端口 !WEB_PORT! 已被占用，自动查找可用端口...
  set /a TRY_PORT=!WEB_PORT!+1
  set PORT_FOUND=0
  set /a MAX_PORT=!WEB_PORT!+9
  :find_port_loop
  if !TRY_PORT! GTR !MAX_PORT! goto port_not_found
  netstat -ano | findstr ":!TRY_PORT! " | findstr "LISTENING" >nul 2>&1
  if errorlevel 1 (
    set PORT_FOUND=1
    set WEB_PORT=!TRY_PORT!
    goto port_found
  )
  set /a TRY_PORT+=1
  goto find_port_loop
  :port_not_found
  echo [!] 端口 !WEB_PORT! ~ !WEB_PORT!+9 均被占用 1>&2
  echo     解决方法：修改 .env 中的 WEB_PORT=其他端口（如 8090），然后重新运行 .\start.bat 1>&2
  exit /b 1
  :port_found
  echo [*] 已自动切换到可用端口：!WEB_PORT!
  REM 更新 .env 中的 WEB_PORT
  if exist ".env" (
    powershell -Command "(Get-Content .env) -replace '^WEB_PORT=.*', 'WEB_PORT=!WEB_PORT!' | Set-Content .env"
    echo [OK] 已将 .env 中的 WEB_PORT 更新为 !WEB_PORT!
  )
)

REM ---------- 4. 拉取镜像或本地构建 ----------
set BUILD_OK=0
if "%DO_BUILD%"=="1" (
  echo [*] 本地源码构建镜像（首次约 5-10 分钟）...
  docker compose build
  if errorlevel 1 (
    echo [X] 镜像构建失败。查看上方错误信息，或检查网络和磁盘空间 1>&2
    exit /b 1
  )
  set BUILD_OK=1
) else (
  if "%DO_PULL%"=="1" (
    REM 检测镜像源连通性（5秒超时，401/403 也算 registry 可达）
    echo [*] 检测镜像源连通性（阿里云 ACR）...
    set HTTP_CODE=000
    for /f "delims=" %%i in ('curl -sS --max-time 5 -o nul -w "%%{http_code}" https://registry.cn-hangzhou.aliyuncs.com/v2/ 2^>nul') do set HTTP_CODE=%%i
    echo !HTTP_CODE! | findstr "200 401 403" >nul
    if errorlevel 1 (
      echo [!] 镜像源不可达（网络或防火墙限制）
      echo     建议直接本地构建（避免拉取超时）...
      echo     如需使用预构建镜像，可在 .env 中切换 GHCR 镜像源或配置代理后重试
      echo     正在尝试本地构建...
      set DO_BUILD=1
    ) else (
      echo [OK] 镜像源可达
      echo [*] 拉取最新镜像（首次约 3-5 分钟）...
      docker compose pull
      if errorlevel 1 (
        echo [!] 镜像拉取失败（可能是镜像未发布或命名空间不匹配）
        echo     自动回退到本地源码构建（首次约 5-10 分钟）...
        set DO_BUILD=1
      ) else (
        set BUILD_OK=1
      )
    )
  )
  if "!BUILD_OK!"=="0" (
    docker compose build
    if errorlevel 1 (
      echo [X] 镜像构建失败。查看上方错误信息，或检查网络和磁盘空间 1>&2
      exit /b 1
    )
    set BUILD_OK=1
  )
)

REM ---------- 4.5 兜底生成 admin bcrypt hash（用 api 镜像） ----------
if exist "secrets\admin-password-hash" (
  for %%A in ("secrets\admin-password-hash") do if %%~zA EQU 0 (
    echo [*] 用 api 镜像生成 admin bcrypt hash（零额外下载）...
    set ADMIN_PASSWORD=admin123
    for /f "delims=" %%h in ('docker run --rm -e ADMIN_PASSWORD=admin123 xianyu-assistant-api python -c "import os, bcrypt; pw=os.environ[\"ADMIN_PASSWORD\"].encode(); print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode())" 2^>nul') do set HASH_VALUE=%%h
    if "!HASH_VALUE!"=="" (
      echo [X] admin bcrypt hash 生成失败 1>&2
      echo     手动生成方法： 1>&2
      echo       1^) 用 api 镜像：docker run --rm xianyu-assistant-api python -c "import bcrypt; print(bcrypt.hashpw(b'admin123', bcrypt.gensalt(rounds=12)).decode())" ^> secrets\admin-password-hash 1>&2
      echo       2^) 重新运行：.\start.bat --no-pull 1>&2
      exit /b 1
    )
    echo !HASH_VALUE!> "secrets\admin-password-hash"
    echo [OK] admin bcrypt hash 生成成功
  )
)

REM ---------- 5. 启动服务 ----------
echo [*] 启动服务...
docker compose up -d
if errorlevel 1 (
  echo [X] 服务启动失败 1>&2
  echo     查看日志：docker compose logs 1>&2
  exit /b 1
)

REM ---------- 6. 分阶段等待健康检查 ----------
echo [*] 等待服务就绪（分阶段，最长 5 分钟）...
set /a TOTAL_ATTEMPT=0

REM 阶段 1：MySQL
echo   [1/5] MySQL...
set /a ATTEMPT=0
:mysql_loop
set /a ATTEMPT+=1
if !ATTEMPT! GTR 60 (
  echo     ✗ 失败
  echo [!] MySQL 启动失败，可能是磁盘空间不足或 secrets 权限问题 1>&2
  call :diagnose
  exit /b 1
)
docker compose ps --format "{{.Health}}" mysql 2>nul | findstr "healthy" >nul
if not errorlevel 1 (
  echo     ✓ 就绪
  goto mysql_done
)
docker compose ps --format "{{.Status}}" mysql 2>nul | findstr /i "exited failed" >nul
if not errorlevel 1 (
  echo     ✗ 失败
  echo [!] MySQL 容器异常退出 1>&2
  call :diagnose
  exit /b 1
)
<nul set /p =.
timeout /t 2 /nobreak >nul
goto mysql_loop
:mysql_done

REM 阶段 2：migrate
echo   [2/5] 数据库迁移...
set /a ATTEMPT=0
:migrate_loop
set /a ATTEMPT+=1
if !ATTEMPT! GTR 60 (
  echo     ✗ 超时
  echo [!] 数据库迁移超时 1>&2
  call :diagnose
  exit /b 1
)
for /f "delims=" %%s in ('docker compose ps --format "{{.Status}}" migrate 2^>nul') do set MIGRATE_STATUS=%%s
echo !MIGRATE_STATUS! | findstr /i "exited.*0" >nul
if not errorlevel 1 (
  echo     ✓ 完成
  goto migrate_done
)
echo !MIGRATE_STATUS! | findstr /r /i "exited.*[1-9] failed" >nul
if not errorlevel 1 (
  echo     ✗ 失败
  echo [!] 数据库迁移失败，可能是密码不匹配或迁移脚本错误 1>&2
  echo     查看 migrate 日志：docker compose logs --tail 100 migrate 1>&2
  call :diagnose
  exit /b 1
)
<nul set /p =.
timeout /t 2 /nobreak >nul
goto migrate_loop
:migrate_done

REM 阶段 3：Redis
echo   [3/5] Redis...
set /a ATTEMPT=0
:redis_loop
set /a ATTEMPT+=1
if !ATTEMPT! GTR 30 (
  echo     ✗ 失败
  echo [!] Redis 启动失败 1>&2
  call :diagnose
  exit /b 1
)
docker compose ps --format "{{.Health}}" redis 2>nul | findstr "healthy" >nul
if not errorlevel 1 (
  echo     ✓ 就绪
  goto redis_done
)
<nul set /p =.
timeout /t 2 /nobreak >nul
goto redis_loop
:redis_done

REM 阶段 4：API
echo   [4/5] API...
set /a ATTEMPT=0
:api_loop
set /a ATTEMPT+=1
if !ATTEMPT! GTR 60 (
  echo     ✗ 失败
  echo [!] API 启动失败 1>&2
  echo     查看 API 日志：docker compose logs --tail 100 api 1>&2
  call :diagnose
  exit /b 1
)
docker compose ps --format "{{.Health}}" api 2>nul | findstr "healthy" >nul
if not errorlevel 1 (
  echo     ✓ 就绪
  goto api_done
)
docker compose ps --format "{{.Status}}" api 2>nul | findstr /i "exited failed" >nul
if not errorlevel 1 (
  echo     ✗ 失败
  echo [!] API 容器异常退出 1>&2
  echo     查看 API 日志：docker compose logs --tail 100 api 1>&2
  call :diagnose
  exit /b 1
)
<nul set /p =.
timeout /t 2 /nobreak >nul
goto api_loop
:api_done

REM 阶段 5：Web + /readyz
echo   [5/5] Web...
set /a ATTEMPT=0
:web_loop
set /a ATTEMPT+=1
if !ATTEMPT! GTR 30 (
  echo     ✗ 超时
  echo [!] 服务启动超时（Web 健康检查未通过） 1>&2
  call :diagnose
  exit /b 1
)
docker compose ps --format "{{.Health}}" web 2>nul | findstr "healthy" >nul
if errorlevel 1 (
  <nul set /p =.
  timeout /t 2 /nobreak >nul
  goto web_loop
)
REM Web 容器健康，再检查 /readyz
where curl >nul 2>&1
if not errorlevel 1 (
  curl -fsS --max-time 3 "http://127.0.0.1:!WEB_PORT!/readyz" >nul 2>&1
  if not errorlevel 1 goto ready
)
echo     ✓ 就绪
goto ready

:ready
echo.
echo [OK] 服务已就绪
echo.
echo 访问地址：http://localhost:!WEB_PORT!
echo.
echo 默认账号：admin
echo 默认密码：admin123（首次启动时由脚本生成，请尽快修改）
echo.
echo 常用命令：
echo   查看状态：python scripts\production_ops.py --env-file .env status
echo   查看日志：python scripts\production_ops.py --env-file .env logs --tail 200
echo   停止服务：python scripts\production_ops.py --env-file .env stop
echo.
if /i "!WEB_BIND!"=="0.0.0.0" (
  echo [!] 默认绑定 0.0.0.0:!WEB_PORT!，暴露到公网前请在 .env 中配置 TRUSTED_HOSTS 和反向代理 TLS
)
exit /b 0

REM ---------- 失败自动诊断函数 ----------
:diagnose
echo.
echo [!] ========== 自动诊断 ==========
echo [1/4] 容器状态：
docker compose ps --format "table {{.Service}}\t{{.Status}}" 2>nul
if errorlevel 1 docker compose ps 2>nul
echo.
echo [2/4] 异常服务最近日志（各 30 行）：
for %%s in (mysql redis migrate api worker crawler web) do (
  for /f "delims=" %%t in ('docker compose ps --format "{{.Status}}" %%s 2^>nul') do set SVC_STATUS=%%t
  echo !SVC_STATUS! | findstr /i "exited failed unhealthy restarting" >nul
  if not errorlevel 1 (
    echo     --- %%s ^(!SVC_STATUS!^) ---
    docker compose logs --tail 30 %%s 2>nul
    echo.
  )
)
echo [3/4] 磁盘空间：
for /f "usebackq tokens=3" %%a in (`dir /-c "%CD%" 2^>nul ^| findstr /r /c:"^[0-9].*可用"`) do set "DIAG_BYTES=%%a"
if defined DIAG_BYTES (
  set /a DIAG_GB=!DIAG_BYTES! / 1073741824
  echo     可用空间：!DIAG_GB!GB
)
echo.
echo [4/4] 端口占用（WEB_PORT=!WEB_PORT!）：
netstat -ano | findstr ":!WEB_PORT! " | findstr "LISTENING"
echo.
echo 完整诊断命令：
echo   全部日志：docker compose logs --tail 200
echo   单服务：  docker compose logs --tail 200 ^<服务名^>
echo   完全重置：docker compose down -v ^&^& rmdir /s /q secrets ^&^& del .env ^&^& .\start.bat
echo [!] ==============================
goto :eof
