# Dot-source in the current PowerShell. Secrets remain in ignored local files.
$s5EnvPath = Join-Path $PSScriptRoot '../../.env.s5-postgres'
if (-not (Test-Path -LiteralPath $s5EnvPath)) { throw 'Run scripts/postgres/start.ps1 first.' }
$s5Values = @{}
Get-Content -LiteralPath $s5EnvPath | ForEach-Object {
    if ($_ -match '^([A-Z0-9_]+)=(.+)$') { $s5Values[$matches[1]] = $matches[2] }
}
$s5Port = if ($s5Values['S5_POSTGRES_PORT']) { $s5Values['S5_POSTGRES_PORT'] } else { '55432' }
$env:OFFERPILOT_POSTGRES_ADMIN_URL = "postgresql+psycopg://s5_migrator:$($s5Values['S5_MIGRATOR_PASSWORD'])@127.0.0.1:$s5Port/offerpilot_s5_test"
$env:OFFERPILOT_POSTGRES_APP_URL = "postgresql+psycopg://s5_app:$($s5Values['S5_APP_PASSWORD'])@127.0.0.1:$s5Port/offerpilot_s5_test"
$env:OFFERPILOT_DATABASE_URL = 'sqlite:///:memory:'
$env:OFFERPILOT_ENVIRONMENT = 'development'
