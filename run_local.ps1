# Steltic CFS — local server launcher (Windows PowerShell).
#   .\run_local.ps1                 -> full local app on http://127.0.0.1:8000
#   .\run_local.ps1 -Demo           -> DEMO mode (example briefs only, uploads off)
#   .\run_local.ps1 -Demo -Port 8010
# Uses .\.venv if present, else the python on PATH. .env at the repo root is loaded
# by the app itself (real environment variables win over .env entries).
param(
    [switch]$Demo,
    [int]$Port = 8000
)
Set-Location -Path $PSScriptRoot

if ($Demo) {
    $env:DEMO = "1"
    Write-Host "DEMO mode: examples only, brief locked, uploads off" -ForegroundColor Yellow
} else {
    $env:DEMO = "0"
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m uvicorn steltic.main:app --host 127.0.0.1 --port $Port
