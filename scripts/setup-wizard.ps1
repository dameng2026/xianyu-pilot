# 首次启动初始化向导（Windows PowerShell 版本）
# 自动生成所有 secrets 文件和 .env。
# admin bcrypt hash 不在此处生成（避免主机 Python/pip/Docker 临时容器的多轮耗时下载），
# 而是由 start.bat 在 api 镜像就绪后用 api 容器统一兜底生成（api 镜像内一定有 bcrypt）。
# 用法：在仓库根目录执行 .\scripts\setup-wizard.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
  [string]$AdminPassword = "admin123"
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectDir

$SecretsDir = Join-Path $ProjectDir "secrets"
$DefaultAdminPassword = $AdminPassword

function Write-Info { param([string]$Msg) Write-Host "• $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "✓ $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "! $Msg" -ForegroundColor Yellow }
function Write-Die  { param([string]$Msg) Write-Host "✗ $Msg" -ForegroundColor Red; exit 1 }

# ---------- 1. 创建 secrets 目录 ----------
# 注意：docker-compose secrets file 模式下，容器内进程（如 mysql 用户）需要读取权限
# 因此 secrets 文件权限不能是 0600（仅 owner 可读），Windows 不强制 chmod 但容器会继承 ACL
if (-not (Test-Path $SecretsDir)) {
  New-Item -ItemType Directory -Path $SecretsDir -Force | Out-Null
}

# 生成 base64url 随机字符串
function New-RandomSecret {
  param([int]$Bytes = 32)
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($buffer)
  $base64 = [Convert]::ToBase64String($buffer)
  # 转 base64url：去掉 = padding，+→-，/→_
  return $base64.TrimEnd('=').Replace('+','-').Replace('/','_')
}

# 写入 secret 文件（已存在且非空则跳过）
function Write-Secret {
  param([string]$File, [string]$Content)
  if (Test-Path $File) {
    $existing = Get-Content $File -Raw -ErrorAction SilentlyContinue
    if ($existing -and $existing.Trim().Length -gt 0) { return }
  }
  [System.IO.File]::WriteAllText($File, $Content)
}

# 生成空的可选 secret 文件
function Touch-Optional {
  param([string]$File)
  if (-not (Test-Path $File)) {
    [System.IO.File]::WriteAllText($File, "")
  }
}

# ---------- 2. 生成必需的随机 secrets ----------
Write-Info "生成随机 secrets（如已存在则跳过）..."

Write-Secret (Join-Path $SecretsDir "mysql-root-password")      (New-RandomSecret 32)
Write-Secret (Join-Path $SecretsDir "mysql-app-password")       (New-RandomSecret 32)
Write-Secret (Join-Path $SecretsDir "mysql-migration-password") (New-RandomSecret 32)
Write-Secret (Join-Path $SecretsDir "redis-password")           (New-RandomSecret 32)
Write-Secret (Join-Path $SecretsDir "jwt-secret")               (New-RandomSecret 64)
Write-Secret (Join-Path $SecretsDir "cookie-crypto-secret")     (New-RandomSecret 64)
Write-Secret (Join-Path $SecretsDir "internal-api-token")       (New-RandomSecret 64)

# ---------- 3. 生成空的可选 secrets ----------
Touch-Optional (Join-Path $SecretsDir "commercial-backend-access-token")
Touch-Optional (Join-Path $SecretsDir "embedding-api-key")
Touch-Optional (Join-Path $SecretsDir "ai-provider-api-key")

# ---------- 4. admin bcrypt 密码 hash（由 start.bat 用 api 镜像统一生成） ----------
# 不在主机生成：避免主机无 Python、pip install 超时、Docker 临时镜像拉取慢等耗时环节。
# 此处只创建空文件作为标记，start.bat 检测到为空时会在 api 镜像就绪后自动生成。
$hashFile = Join-Path $SecretsDir "admin-password-hash"
if (Test-Path $hashFile) {
  $existingHash = Get-Content $hashFile -Raw -ErrorAction SilentlyContinue
  if ($existingHash -and $existingHash.Trim().Length -gt 0) {
    Write-Ok "admin-password-hash 已存在（跳过生成）"
  } else {
    [System.IO.File]::WriteAllText($hashFile, "")
    Write-Info "admin-password-hash 将由 api 镜像在启动前自动生成（默认密码：$DefaultAdminPassword）"
  }
} else {
  [System.IO.File]::WriteAllText($hashFile, "")
  Write-Info "admin-password-hash 将由 api 镜像在启动前自动生成（默认密码：$DefaultAdminPassword）"
}

# ---------- 5. 创建 .env ----------
$envFile = Join-Path $ProjectDir ".env"
$envExample = Join-Path $ProjectDir ".env.example"
if (-not (Test-Path $envFile)) {
  if (Test-Path $envExample) {
    Copy-Item $envExample $envFile
    Write-Ok "已从 .env.example 创建 .env"
  } else {
    Write-Die ".env.example 不存在，无法创建 .env"
  }
} else {
  Write-Ok ".env 已存在（跳过创建）"
}

# ---------- 6. 完成 ----------
Write-Host ""
Write-Ok "初始化完成"
Write-Host ""
Write-Host "默认管理员账号："
Write-Host "  用户名：admin"
Write-Host "  密码：$DefaultAdminPassword"
Write-Host ""
Write-Warn "请尽快登录后修改默认密码！"
Write-Host ""
Write-Host "下一步："
Write-Host "  启动服务：.\start.bat"
Write-Host "  或手动：docker compose pull ; docker compose up -d"
Write-Host ""
