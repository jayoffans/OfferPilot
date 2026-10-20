param([string]$EvidenceDirectory = '')
$ErrorActionPreference = 'Stop'
$s5Root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $s5Root
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $s5Root ('frontend/artifacts/s5-1/run-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
$s5Python = Join-Path $s5Root 'backend/.venv/Scripts/python.exe'
function Invoke-S5Check([string]$Name, [scriptblock]$Action) {
    Write-Host "Running $Name"
    $s5Log = Join-Path $EvidenceDirectory "$Name.log"
    & $Action *> $s5Log
    $s5Result = $LASTEXITCODE
    Add-Content -LiteralPath $s5Log -Value "exit_code=$s5Result"
    if ($s5Result -ne 0) { throw "$Name failed; see $s5Log" }
}
& (Join-Path $PSScriptRoot 'start.ps1')
Invoke-S5Check 'pg-specific' { & $s5Python -B -m unittest discover -s tests/postgres -t . -v }
Invoke-S5Check 'pg-regression' { & $s5Python -B scripts/postgres/regression.py --output (Join-Path $EvidenceDirectory 'pg-regression.json') }
Invoke-S5Check 'quality' { & $s5Python -B scripts/postgres/quality.py --users 100 --backup --output (Join-Path $EvidenceDirectory 'quality') }
# Deliberately remove PG opt-in for the original SQLite regression. PG-only
# cases report explicit skips; the dedicated real PG suite above is mandatory.
Remove-Item Env:OFFERPILOT_POSTGRES_ADMIN_URL, Env:OFFERPILOT_POSTGRES_APP_URL
Invoke-S5Check 'sqlite' { & $s5Python -B -m unittest discover -s tests -v }
Invoke-S5Check 'http-files' { & $s5Python -B tests/http_file_lifecycle_smoke.py }
Invoke-S5Check 'frontend-unit' { npm.cmd --prefix frontend test }
Invoke-S5Check 'contract' { npm.cmd --prefix frontend run api:check }
Invoke-S5Check 'lint' { npm.cmd --prefix frontend run lint }
. (Join-Path $PSScriptRoot 'env.ps1')
$env:OFFERPILOT_E2E_POSTGRES = 'true'
Invoke-S5Check 'chromium-pg' { npm.cmd --prefix frontend run test:e2e }
Remove-Item Env:OFFERPILOT_E2E_POSTGRES
# Build and E2E share .next: keep them sequential.
Invoke-S5Check 'build' { npm.cmd --prefix frontend run build }
Invoke-S5Check 'typecheck' { npm.cmd --prefix frontend run typecheck }
Invoke-S5Check 'diff' { git diff --check }
Write-Host "Completed; evidence: $EvidenceDirectory. Database volume preserved. No deployment or push."
