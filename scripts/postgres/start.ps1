$ErrorActionPreference = 'Stop'
$s5Root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$s5EnvPath = Join-Path $s5Root '.env.s5-postgres'
if (-not (Test-Path -LiteralPath $s5EnvPath)) {
    $s5Lines = foreach ($s5Key in @('S5_POSTGRES_ADMIN_PASSWORD','S5_MIGRATOR_PASSWORD','S5_APP_PASSWORD')) {
        $s5Bytes = New-Object byte[] 24
        $s5Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $s5Rng.GetBytes($s5Bytes) } finally { $s5Rng.Dispose() }
        $s5Secret = -join ($s5Bytes | ForEach-Object { $_.ToString('x2') })
        "$s5Key=$s5Secret"
    }
    $s5Lines | Set-Content -LiteralPath $s5EnvPath -Encoding ascii
}
docker compose --env-file $s5EnvPath -f (Join-Path $s5Root 'compose.postgres.yml') up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed. Existing volumes/passwords were preserved.' }
. (Join-Path $PSScriptRoot 'env.ps1')
