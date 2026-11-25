param(
    [switch]$Demo,
    [int]$Port = 8806
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment and installing dependencies..." -ForegroundColor Cyan
    python -m venv (Join-Path $Root ".venv")
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
}

$ArgsList = @("-m", "proxmox_hub", "web", "--port", "$Port")
if ($Demo) { $ArgsList += "--demo" }

Write-Host "Starting Proxmox Homelab Hub on http://127.0.0.1:$Port ..." -ForegroundColor Green
& $Python @ArgsList
