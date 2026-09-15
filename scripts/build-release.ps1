# Build a Windows installer: packaged Python service + React UI + Tauri host + bundled resources.
# Signing the updater artifacts needs TAURI_SIGNING_PRIVATE_KEY (and _PASSWORD if set); without it the
# installer still builds but the update signature step is skipped with a warning.
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipTests) {
  npm run test:python
  if ($LASTEXITCODE) { throw "Python tests failed" }
}

python -m pip install --quiet "pyinstaller>=6" openpyxl httpx beautifulsoup4 rapidfuzz cryptography
python -m PyInstaller packaging/dataforge-service.spec --noconfirm --distpath build/service --workpath build/pyinstaller
if ($LASTEXITCODE) { throw "PyInstaller build failed" }

& "$PSScriptRoot\smoke-test-service.ps1" -ServiceDir "$root\build\service\dataforge-service" -Resources $root
if ($LASTEXITCODE) { throw "Packaged service smoke test failed" }

$keyFile = Join-Path $env:USERPROFILE ".dataforge\keys\updater.key"
if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and (Test-Path $keyFile)) {
  $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $keyFile -Raw
}
# PowerShell cannot hold an empty environment variable, so a passwordless key would trigger an
# interactive prompt. CI mode makes the Tauri CLI use the (empty) password without prompting.
if ($env:TAURI_SIGNING_PRIVATE_KEY -and -not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
  $env:CI = "true"
}

Set-Location "$root\apps\desktop"
npm run typecheck
npx tauri build --config src-tauri/tauri.release.conf.json
if ($LASTEXITCODE) { throw "Tauri build failed" }

$bundle = Get-ChildItem "$root\apps\desktop\src-tauri\target\release\bundle\nsis" -File
$bundle | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name } | Set-Content "$root\apps\desktop\src-tauri\target\release\bundle\nsis\SHA256SUMS.txt"
$bundle | Select-Object Name, Length
