# Smoke test for a packaged service: runs without any Python on PATH, creates a project, imports a fixture,
# runs preview + full matching, and checks bundled presets resolve through the resources directory.
param([Parameter(Mandatory)][string]$ServiceDir, [Parameter(Mandatory)][string]$Resources)
$ErrorActionPreference = "Stop"
$exe = Join-Path $ServiceDir "dataforge-service.exe"
if (-not (Test-Path $exe)) { throw "Service executable not found: $exe" }

$work = Join-Path ([IO.Path]::GetTempPath()) ("dataforge-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $work | Out-Null
$csv = Join-Path $work "fixture.csv"
"Name,Phone,Address,Zip`nAda Lovelace,5125550182,123 Main St,78701`nAda Lovelace,512-555-0182,123 Main Street,78701`nBob Stone,2125550100,9 Oak Ave,10001" | Set-Content -Encoding utf8 $csv

$psi = New-Object System.Diagnostics.ProcessStartInfo $exe
$psi.Arguments = "--resources `"$Resources`""
$psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
$psi.Environment["PATH"] = "$env:SystemRoot\System32"  # prove no Python installation is needed
$psi.Environment["DATAFORGE_APP_DATA"] = $work
$process = [System.Diagnostics.Process]::Start($psi)
$script:id = 0
function Invoke-Service([string]$command, [hashtable]$payload = @{}) {
  $script:id++
  $process.StandardInput.WriteLine((@{ id = $script:id; schema_version = 1; command = $command; payload = $payload } | ConvertTo-Json -Compress -Depth 10))
  $response = $process.StandardOutput.ReadLine() | ConvertFrom-Json
  if (-not $response.ok) { throw "$command failed: $($response.error.message)" }
  return $response.result
}
function Wait-Job([string]$jobId) {
  for ($i = 0; $i -lt 300; $i++) {
    $job = Invoke-Service "job.get" @{ job_id = $jobId }
    if ($job.state -in "completed", "failed", "cancelled") { if ($job.state -ne "completed") { throw "job $jobId $($job.state): $($job.error)" }; return $job }
    Start-Sleep -Milliseconds 200
  }
  throw "job $jobId timed out"
}
try {
  $health = Invoke-Service "health.check"
  Invoke-Service "project.create" @{ path = (Join-Path $work "project"); name = "Smoke" } | Out-Null
  $import = Wait-Job (Invoke-Service "dataset.import" @{ path = $csv }).job_id
  $datasetId = $import.result.dataset_id
  Invoke-Service "dataset.confirm_mapping" @{ dataset_id = $datasetId; entity_type = "person"; mapping = @{ Name = "name"; Phone = "phone"; Address = "address"; Zip = "postal_code" } } | Out-Null
  Wait-Job (Invoke-Service "match.create_job" @{ dataset_id = $datasetId; run_mode = "preview" }).job_id | Out-Null
  $full = Wait-Job (Invoke-Service "match.create_job" @{ dataset_id = $datasetId; run_mode = "full" }).job_id
  $results = Invoke-Service "match.results" @{ job_id = $full.id }
  $presets = Invoke-Service "preset.list"
  $healthChecks = Invoke-Service "preset.health_check"
  $detected = Invoke-Service "scrape.detect_structured" @{ url = "https://shop.example/p"; html = '<script type="application/ld+json">{"@type":"Product","name":"Widget","sku":"W-1","offers":{"price":"9.99"}}</script>' }
  if ($detected.suggested_type -ne "Product") { throw "structured data detection failed in the packaged build" }
  $proposals = Invoke-Service "scrape.propose_presets" @{ url = "https://shop.example/"; html = ("<ul>" + ((1..4) | ForEach-Object { "<li class='card'><h3><a href='/p/$_'>Item $_ name</a></h3><span class='price'>`$$_.99</span></li>" }) -join "" + "</ul>") }
  if (-not @($proposals.proposals).Count) { throw "preset proposals failed in the packaged build" }
  if ($results.canonical_records -ne 2) { throw "expected 2 canonical records, got $($results.canonical_records)" }
  if (@($healthChecks | Where-Object { $_.status -ne "passed" }).Count) { throw "bundled preset health checks failed" }
  "Smoke test passed: service $($health.status), $($import.result.row_count) rows imported, $($results.canonical_records) canonical records, $(@($presets).Count) presets, all fixture health checks passed, structured data and proposals work"
}
finally {
  $process.StandardInput.Close()
  if (-not $process.WaitForExit(5000)) { $process.Kill() }
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
