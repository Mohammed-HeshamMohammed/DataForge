# Run the application service as a loopback HTTP bridge for browser-only UI development.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = @("services/application/src", "workers/matching/src", "workers/scraping/src" | ForEach-Object { Join-Path $root $_ }) -join [IO.Path]::PathSeparator
Set-Location $root
python -m dataforge_application.server --http 8765
