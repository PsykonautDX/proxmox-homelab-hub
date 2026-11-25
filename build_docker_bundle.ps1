param(
    [switch]$BuildImage
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist = Join-Path $Root "dist"
$Stage = Join-Path $Dist "stage"

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

# Stage clean production files (NEVER includes .env, data/, .venv/, or _rollback/)
Copy-Item (Join-Path $Root "proxmox_hub") (Join-Path $Stage "proxmox_hub") -Recurse -Force
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

foreach ($f in @("Dockerfile", ".dockerignore", "docker-compose.yml", "docker-compose.portainer.yml", "requirements.txt", ".env.example", "PORTAINER_DEPLOY.md")) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $Stage $f) -Force }
}

$ZipPath = Join-Path $Dist "proxmox-homelab-hub-portainer.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force

Copy-Item (Join-Path $Root "docker-compose.portainer.yml") (Join-Path $Dist "docker-compose.portainer.yml") -Force
Remove-Item $Stage -Recurse -Force

Write-Host "Created clean Portainer source bundle: $ZipPath" -ForegroundColor Green
Write-Host "Created Portainer compose stack      : $(Join-Path $Dist 'docker-compose.portainer.yml')" -ForegroundColor Green

if ($BuildImage) {
    $TarPath = Join-Path $Dist "proxmox-homelab-hub-image.tar"
    Write-Host "Building Docker image proxmox-homelab-hub:latest..." -ForegroundColor Cyan
    docker build -t proxmox-homelab-hub:latest $Root
    Write-Host "Exporting portable Docker image tar to $TarPath..." -ForegroundColor Cyan
    docker save -o $TarPath proxmox-homelab-hub:latest
    Write-Host "Image tar ready for Portainer -> Images -> Import: $TarPath" -ForegroundColor Green
}
