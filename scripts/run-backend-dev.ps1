$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$pythonExe = if ($env:LESSON_PLAN_PYTHON_EXE) {
    $env:LESSON_PLAN_PYTHON_EXE
} elseif (Test-Path $venvPython) {
    $venvPython
} else {
    "C:\Python314\python.exe"
}
$pyDeps = Join-Path $repoRoot ".pydeps"

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path $pyDeps)) {
    throw "Python dependency directory not found: $pyDeps"
}

if (-not $env:JWT_SECRET_KEY) {
    $env:JWT_SECRET_KEY = "test-secret-key"
}

if (-not $env:AUTH_COOKIE_SECURE) {
    $env:AUTH_COOKIE_SECURE = "0"
}

if (-not $env:AUTH_COOKIE_SAMESITE) {
    $env:AUTH_COOKIE_SAMESITE = "lax"
}

$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = $pyDeps

Set-Location $repoRoot
& $pythonExe -m uvicorn main:app --host 127.0.0.1 --port 8000
