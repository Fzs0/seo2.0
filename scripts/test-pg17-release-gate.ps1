param(
    [string]$Dsn = $env:SEO_PG17_TEST_DSN
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Dsn)) {
    throw "PG17 release gate failed: set SEO_PG17_TEST_DSN to a disposable PostgreSQL 17 database."
}

$databaseName = ([Uri]$Dsn).AbsolutePath.TrimStart("/").ToLowerInvariant()
if (-not ($databaseName.Contains("test") -or $databaseName.Contains("temp") -or $databaseName.Contains("tmp"))) {
    throw "PG17 release gate failed: database name must contain test, temp, or tmp."
}

$env:SEO_PG17_GATE_REQUIRED = "1"
$env:SEO_PG17_TEST_DSN = $Dsn
$env:SEO_MIGRATION_TEST_POSTGRES_DSN = $Dsn
$env:SEO_HOLD_TEST_POSTGRES_DSN = $Dsn

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python -m pytest `
    tests/test_pg17_release_gate.py `
    tests/test_strategy_effect_migrations_postgres.py `
    tests/test_strategy_hold_concurrency_postgres.py `
    -q

if ($LASTEXITCODE -ne 0) {
    throw "PG17 release gate failed with exit code $LASTEXITCODE."
}
