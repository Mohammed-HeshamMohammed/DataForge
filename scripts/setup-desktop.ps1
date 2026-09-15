# Install desktop frontend dependencies and verify the Tauri toolchain.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing desktop npm dependencies..."
npm run setup:desktop

Write-Host "Checking Rust / Cargo..."
rustc -V
cargo -V

Write-Host "Checking Tauri CLI via local package..."
npm exec --prefix apps/desktop -- tauri --version

Write-Host ""
Write-Host "Setup complete. From the repository root run:"
Write-Host "  npm run dev:tauri"
