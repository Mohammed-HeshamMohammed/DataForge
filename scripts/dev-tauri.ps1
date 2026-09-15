# Start the DataForge Tauri desktop shell in development mode.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "apps/desktop/node_modules")) {
  Write-Host "Desktop dependencies missing. Running setup..."
  & "$PSScriptRoot\setup-desktop.ps1"
}

npm run dev:tauri
