param(
    [ValidateRange(1024, 65535)][int]$Port = 15179,
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

function Test-Python([string]$Executable, [string[]]$PrefixArgs = @(), [switch]$NeedPackages) {
    if (-not $Executable) { return $false }
    $probe = 'import sys; assert (3,11) <= sys.version_info[:2] < (3,14)'
    if ($NeedPackages) {
        $probe += "; import fastapi, uvicorn, re; from importlib.metadata import version; from pathlib import Path; locked=re.findall(r'^([a-zA-Z0-9_-]+)==([^\s]+)', Path('apps/desktop/requirements.txt').read_text(), re.M); assert locked and all(version(name)==expected for name,expected in locked)"
    }
    try {
        & $Executable @PrefixArgs -c $probe 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

try {
    $pythonExecutable = $null
    $candidates = @('.venv-desktop\Scripts\python.exe', '.venv-ci\Scripts\python.exe', '.venv\Scripts\python.exe')
    foreach ($relative in $candidates) {
        $candidate = Join-Path $repoRoot $relative
        if ((Test-Path -LiteralPath $candidate) -and (Test-Python $candidate -NeedPackages)) {
            $pythonExecutable = $candidate
            break
        }
    }
    if (-not $pythonExecutable) {
        $basePython = $null
        $baseArgs = @()
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) {
            foreach ($version in @('-3.12', '-3.11', '-3.13')) {
                if (Test-Python $py.Source @($version)) {
                    $basePython = $py.Source
                    $baseArgs = @($version)
                    break
                }
            }
        }
        if (-not $basePython) {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($pythonCommand -and (Test-Python $pythonCommand.Source)) { $basePython = $pythonCommand.Source }
        }
        # An existing project interpreter can bootstrap the isolated demo environment.
        if (-not $basePython) {
            foreach ($relative in $candidates) {
                $candidate = Join-Path $repoRoot $relative
                if ((Test-Path -LiteralPath $candidate) -and (Test-Python $candidate)) { $basePython = $candidate; break }
            }
        }
        if (-not $basePython) { throw 'Python 3.11-3.13 is required. Install Python, then run this file again.' }
        if ($CheckOnly) { throw 'Demo Python dependencies are missing. Run without -CheckOnly to set them up.' }
        Write-Host 'Preparing an isolated Python environment (.venv-desktop)...'
        & $basePython @baseArgs -m venv (Join-Path $repoRoot '.venv-desktop')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the demo virtual environment.' }
        $pythonExecutable = Join-Path $repoRoot '.venv-desktop\Scripts\python.exe'
        & $pythonExecutable -m pip install --require-hashes -r apps/desktop/requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Check network access, then retry.' }
    }

    $nodeExecutable = Join-Path $repoRoot '.tmp\node\node-v22.23.1-win-x64\node.exe'
    if (-not (Test-Path -LiteralPath $nodeExecutable)) {
        $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
        if (-not $nodeCommand) { throw 'Node.js 22.23.1 is required for the frontend build.' }
        $nodeExecutable = $nodeCommand.Source
    }
    $nodeVersion = & $nodeExecutable -p 'process.versions.node'
    if ($nodeVersion -ne '22.23.1') { throw "Node.js 22.23.1 is required by the project lockfile. Found: $nodeVersion" }
    $env:PATH = (Split-Path -Parent $nodeExecutable) + ';' + $env:PATH
    $npmCli = Join-Path (Split-Path -Parent $nodeExecutable) 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmCli)) { throw 'npm was not found beside Node.js. Repair the Node.js installation.' }
    $npmVersion = & $nodeExecutable $npmCli --version
    if ($npmVersion -ne '10.9.8') { throw "npm 10.9.8 is required. Found: $npmVersion" }
    $viteCli = Join-Path $repoRoot 'apps\web\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $viteCli)) {
        if ($CheckOnly) { throw 'Frontend dependencies are missing. Run without -CheckOnly to install them.' }
        Push-Location (Join-Path $repoRoot 'apps\web')
        try {
            & $nodeExecutable $npmCli ci --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        } finally { Pop-Location }
    }

    Write-Host "Python: $pythonExecutable"
    Write-Host "Node.js: $nodeVersion / npm: $npmVersion"
    if ($CheckOnly) { Write-Host 'Local demo prerequisites are ready.'; exit 0 }
    Write-Host 'Building the local demo page...'
    Push-Location (Join-Path $repoRoot 'apps\web')
    try {
        & $nodeExecutable $viteCli build --config vite.desktop.config.js
        if ($LASTEXITCODE -ne 0) { throw 'Demo frontend build failed.' }
    } finally { Pop-Location }
    $hostArgs = @('-m', 'apps.desktop.host', '--port', "$Port")
    if ($NoBrowser) { $hostArgs += '--no-browser' }
    & $pythonExecutable @hostArgs
    if ($LASTEXITCODE -ne 0) { throw 'Demo host stopped with an error.' }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
