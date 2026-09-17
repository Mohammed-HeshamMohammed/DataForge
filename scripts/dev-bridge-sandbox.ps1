# Starts the loopback dev bridge with an empty app-data folder, so UI work and screenshots never open
# your real recent projects. Usage: powershell -File scripts/dev-bridge-sandbox.ps1
$sandbox = Join-Path $env:TEMP "dataforge-ui-sandbox"
New-Item -ItemType Directory -Force $sandbox | Out-Null
$env:DATAFORGE_APP_DATA = $sandbox
$env:DATAFORGE_LOG_FILE = "0"
& (Join-Path $PSScriptRoot "dev-bridge.ps1")
