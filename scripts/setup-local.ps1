# 本地裸机部署初始化向导（Windows）
# 自动完成：预检环境 → 生成 .env 与随机密钥 → 生成 admin bcrypt hash
#          → 创建 MySQL 数据库与用户 → 安装 Python/Node 依赖 → 安装 Playwright Chromium
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
#   $env:MYSQL_ROOT_PASSWORD="xxx"; powershell -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
#   $env:ADMIN_PASSWORD="your-pass"; ...   # 自定义 admin 密码（默认 admin123）
# 说明：已存在的 .env / venv / node_modules 不会被重复生成或覆盖。

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvDir = Join-Path $Root '.venv'
$EnvFile = Join-Path $Root '.env'
$EnvTemplate = Join-Path $Root '.env.development.example'
$DefaultAdminPassword = 'admin123'

function Write-Info([string]$Msg) { Write-Host "[*] $Msg" -ForegroundColor Cyan }
function Write-Ok([string]$Msg)   { Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Warn2([string]$Msg) { Write-Host "[!] $Msg" -ForegroundColor Yellow }

# ---------- 1. 前置依赖检查 ----------
Write-Info '检查本地环境依赖...'

$Python = $null
foreach ($candidate in @('python', 'py')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $ver = & $cmd -c 'import sys; print(sys.version_info.major, sys.version_info.minor)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match '^3\s+(1[0-9]|[2-9][0-9])') {
            $Python = $cmd.Source
            break
        }
    } catch {}
}
if (-not $Python) {
    throw "未检测到 Python 3.10+，请先安装：https://www.python.org/downloads/ （安装时勾选 Add python.exe to PATH）"
}
$pythonVer = & $Python --version 2>&1
Write-Ok "Python: $pythonVer"

foreach ($name in @('node', 'npm')) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "未检测到 $name，请先安装 Node.js 22+：https://nodejs.org/"
    }
}
$nodeMajor = [int]((& node -e 'console.log(process.versions.node.split(".")[0])') 2>$null)
if ($nodeMajor -lt 18) { Write-Warn2 "检测到 Node.js v$nodeMajor，推荐 22+（最低 18）" }
$nodeVer = & node --version 2>&1
$npmVer = & npm --version 2>&1
Write-Ok "Node.js: $nodeVer / npm: $npmVer"

if (-not (Get-Command mysql -ErrorAction SilentlyContinue)) {
    Write-Warn2 '未检测到 mysql 客户端，将无法自动创建数据库（可手动创建后重跑本脚本）'
}

# ---------- 2. 生成 .env ----------
function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = Get-Content -LiteralPath $EnvFile -Encoding utf8
    $matched = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($Key))=") {
            $lines[$i] = "$Key=$Value"
            $matched = $true
            break
        }
    }
    if (-not $matched) { $lines += "$Key=$Value" }
    Set-Content -LiteralPath $EnvFile -Value $lines -Encoding utf8
}

function New-RandomSecret([int]$Bytes = 32) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $Bytes
    $rng.GetBytes($buf)
    $rng.Dispose()
    ($buf | ForEach-Object { $_.ToString('x2') }) -join ''
}

if (Test-Path -LiteralPath $EnvFile) {
    Write-Ok '.env 已存在（跳过生成）'
} else {
    if (-not (Test-Path -LiteralPath $EnvTemplate)) { throw "缺少 $EnvTemplate 模板文件" }
    Copy-Item -LiteralPath $EnvTemplate -Destination $EnvFile
    Write-Ok "已从 $EnvTemplate 创建 .env"
    Set-EnvValue 'JWT_SECRET'           (New-RandomSecret 48)
    Set-EnvValue 'COOKIE_CRYPTO_SECRET' (New-RandomSecret 48)
    Set-EnvValue 'INTERNAL_API_TOKEN'   (New-RandomSecret 48)
    Set-EnvValue 'MYSQL_PASSWORD'       (New-RandomSecret 24)
    Write-Info '已自动生成 JWT/COOKIE/INTERNAL/MYSQL 随机密钥'
}

# ---------- 3. 创建 venv 并安装 API 依赖 ----------
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (Test-Path -LiteralPath $VenvPython) {
    Write-Ok 'Python venv 已存在（.venv）'
} else {
    Write-Info '创建 Python 虚拟环境（.venv）...'
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw '创建 venv 失败' }
    Write-Ok 'venv 创建完成'
}

& $VenvPython -c 'import bcrypt, fastapi' 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Ok 'API 依赖已安装（跳过）'
} else {
    Write-Info '安装 API 依赖（首次约 2-5 分钟，国内可设 PIP_INDEX_URL 加速）...'
    & (Join-Path $VenvDir 'Scripts\pip.exe') install -r (Join-Path $Root 'apps\api\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'API 依赖安装失败，请检查网络后重试' }
    Write-Ok 'API 依赖安装完成'
}

# ---------- 4. 生成 admin bcrypt hash ----------
$AdminPassword = if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { $DefaultAdminPassword }
$currentHash = (Select-String -LiteralPath $EnvFile -Pattern '^ADMIN_PASSWORD_HASH=' | Select-Object -First 1).Line
if ($currentHash -match '^\$2[aby]\$') {
    Write-Ok 'admin 密码 hash 已存在（跳过生成）'
} else {
    Write-Info "生成 admin 密码 hash（默认密码：$DefaultAdminPassword，可用 ADMIN_PASSWORD 覆盖）..."
    $env:ADMIN_PASSWORD = $AdminPassword
    $hashValue = & $VenvPython -c 'import os, bcrypt; print(bcrypt.hashpw(os.environ["ADMIN_PASSWORD"].encode(), bcrypt.gensalt(rounds=12)).decode())'
    if ($LASTEXITCODE -ne 0 -or $hashValue -notmatch '^\$2[aby]\$') { throw 'bcrypt hash 生成失败，请检查 .venv 中 bcrypt 是否安装' }
    Set-EnvValue 'ADMIN_PASSWORD_HASH' $hashValue
    Remove-Item Env:\ADMIN_PASSWORD -ErrorAction SilentlyContinue
    Write-Ok 'admin 密码 hash 已写入 .env（登录账号：admin）'
}

# ---------- 5. 创建 MySQL 数据库与用户 ----------
function Get-EnvValue([string]$Key) {
    $line = Select-String -LiteralPath $EnvFile -Pattern "^$([regex]::Escape($Key))=" | Select-Object -First 1
    if ($line) { return ($line.Line -split '=', 2)[1] }
    return ''
}

$mysqlDatabase = Get-EnvValue 'MYSQL_DATABASE'; if (-not $mysqlDatabase) { $mysqlDatabase = 'xianyu_opensource' }
$mysqlUser     = Get-EnvValue 'MYSQL_USER';     if (-not $mysqlUser)     { $mysqlUser = 'xianyu' }
$mysqlPassword = Get-EnvValue 'MYSQL_PASSWORD'

if (Get-Command mysql -ErrorAction SilentlyContinue) {
    $rootArgs = @()
    $rootOk = $false
    & mysql -uroot --skip-password -e 'SELECT 1' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $rootArgs = @('-uroot'); $rootOk = $true }
    if (-not $rootOk -and $env:MYSQL_ROOT_PASSWORD) {
        & mysql -uroot "-p$($env:MYSQL_ROOT_PASSWORD)" -e 'SELECT 1' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $rootArgs = @('-uroot', "-p$($env:MYSQL_ROOT_PASSWORD)"); $rootOk = $true }
    }
    if (-not $rootOk) {
        $rootInput = Read-Host '请输入 MySQL root 密码（本机 root 无密码则直接回车）'
        if ($rootInput) { $rootArgs = @('-uroot', "-p$rootInput") } else { $rootArgs = @('-uroot') }
        & mysql @rootArgs -e 'SELECT 1' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $rootOk = $true }
    }

    if ($rootOk) {
        Write-Info '使用 MySQL root 连接创建数据库与用户...'
        $sql = @(
            "CREATE DATABASE IF NOT EXISTS ``$mysqlDatabase`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            "CREATE USER IF NOT EXISTS '$mysqlUser'@'localhost' IDENTIFIED BY '$mysqlPassword';"
            "CREATE USER IF NOT EXISTS '$mysqlUser'@'127.0.0.1' IDENTIFIED BY '$mysqlPassword';"
            "GRANT ALL PRIVILEGES ON ``$mysqlDatabase``.* TO '$mysqlUser'@'localhost';"
            "GRANT ALL PRIVILEGES ON ``$mysqlDatabase``.* TO '$mysqlUser'@'127.0.0.1';"
            'FLUSH PRIVILEGES;'
        ) -join ' '
        & mysql @rootArgs -e $sql
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "数据库 $mysqlDatabase 与用户 $mysqlUser 创建完成"
        } else {
            Write-Warn2 '建库失败，请手动执行以下 SQL 后重跑本脚本'
            $sql
        }
    } else {
        Write-Warn2 '无法连接 MySQL root，请手动创建数据库与用户后重跑本脚本：'
        "  CREATE DATABASE IF NOT EXISTS ``$mysqlDatabase`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        "  CREATE USER IF NOT EXISTS '$mysqlUser'@'localhost' IDENTIFIED BY '$mysqlPassword';"
        "  CREATE USER IF NOT EXISTS '$mysqlUser'@'127.0.0.1' IDENTIFIED BY '$mysqlPassword';"
        "  GRANT ALL PRIVILEGES ON ``$mysqlDatabase``.* TO '$mysqlUser'@'localhost';"
        "  GRANT ALL PRIVILEGES ON ``$mysqlDatabase``.* TO '$mysqlUser'@'127.0.0.1';"
        '  FLUSH PRIVILEGES;'
    }
} else {
    Write-Warn2 '未安装 mysql 客户端，请手动创建数据库与用户'
}

# ---------- 6. 安装 Node 依赖 ----------
if (Test-Path -LiteralPath (Join-Path $Root 'apps\crawler\node_modules')) {
    Write-Ok 'Crawler 依赖已安装（跳过）'
} else {
    Write-Info '安装 Crawler 依赖（npm install，首次约 1-3 分钟）...'
    Push-Location (Join-Path $Root 'apps\crawler')
    try { & npm install; if ($LASTEXITCODE -ne 0) { Write-Warn2 'Crawler 依赖安装失败' } else { Write-Ok 'Crawler 依赖安装完成' } }
    finally { Pop-Location }
}

if (Test-Path -LiteralPath (Join-Path $Root 'apps\web\node_modules')) {
    Write-Ok 'Web 依赖已安装（跳过）'
} else {
    Write-Info '安装 Web 依赖（npm install，首次约 2-4 分钟）...'
    Push-Location (Join-Path $Root 'apps\web')
    try { & npm install; if ($LASTEXITCODE -ne 0) { Write-Warn2 'Web 依赖安装失败' } else { Write-Ok 'Web 依赖安装完成' } }
    finally { Pop-Location }
}

# ---------- 7. 安装 Playwright Chromium ----------
$playwrightCache = Join-Path $env:LOCALAPPDATA 'ms-playwright'
if (Test-Path -LiteralPath $playwrightCache) {
    Write-Ok 'Playwright Chromium 已安装（跳过）'
} else {
    Write-Info '安装 Playwright Chromium（约 150MB，国内可设 PLAYWRIGHT_DOWNLOAD_HOST 镜像加速）...'
    Push-Location (Join-Path $Root 'apps\crawler')
    try {
        & npx playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'Chromium 下载失败，可设置镜像后重试：'
            Write-Warn2 '  $env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright"; powershell -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1'
        }
    } finally { Pop-Location }
}

# ---------- 8. 完成 ----------
Write-Host ''
Write-Ok '本地初始化完成'
Write-Host ''
Write-Host '默认管理员账号：'
Write-Host '  用户名：admin'
Write-Host "  密码：$AdminPassword（登录后请尽快修改）"
Write-Host ''
Write-Host '下一步：'
Write-Host '  启动服务：.\start-local.bat'
Write-Host ''
Write-Warn2 '提示：本机需已安装并运行 MySQL 8 与 Redis 7（本机无密码 Redis 即可，有密码请编辑 .env 的 REDIS_PASSWORD）'
