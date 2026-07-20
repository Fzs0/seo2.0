param(
    [switch]$NoReload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Import-LocalEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }

        $parts = $trimmed.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$' -and -not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Import-LocalEnvironment (Join-Path $PSScriptRoot '.env')

$backend = Join-Path $PSScriptRoot 'backend'
if (-not (Test-Path -LiteralPath (Join-Path $backend 'app\main.py'))) {
    throw "Backend entry point not found: $backend\app\main.py"
}

$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python' }
$arguments = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8010')
if (-not $NoReload) {
    $arguments += '--reload'
}

Push-Location $backend
try {
    & $python @arguments
}
finally {
    Pop-Location
}
