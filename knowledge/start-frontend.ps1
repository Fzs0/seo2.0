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

$frontend = Join-Path $PSScriptRoot 'frontend'
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package.json'))) {
    throw "Frontend package file not found: $frontend\package.json"
}
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
    throw "Frontend dependencies are missing. Run npm install once in $frontend."
}

Push-Location $frontend
try {
    & npm.cmd run dev -- --host 127.0.0.1 --port 5174
}
finally {
    Pop-Location
}
