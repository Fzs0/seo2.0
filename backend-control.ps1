param(
    [ValidateSet('guard', 'status', 'stop')]
    [string]$Action = 'status',
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path $ProjectRoot).Path.TrimEnd('\')
$healthUrl = "http://127.0.0.1:$Port/api/health"

function Get-ProcessChain([int]$ProcessId) {
    $chain = @()
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
    while ($current) {
        $chain += $current
        if (-not $current.ParentProcessId) { break }
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($current.ParentProcessId)"
    }
    return $chain
}

function Get-BackendInfo {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return [pscustomobject]@{ State = 'stopped' } }

    $chain = Get-ProcessChain $listener.OwningProcess
    $projectPattern = [regex]::Escape($root)
    $uvicorn = $chain | Where-Object {
        $_.CommandLine -match 'app\.main:app' -and $_.CommandLine -match "--port\s+$Port"
    }
    $managed = $chain | Where-Object { $_.CommandLine -match $projectPattern } | Select-Object -First 1
    $health = $null
    if ($uvicorn -and $managed) {
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
        } catch {
            $health = $null
        }
    }
    $hasFingerprint = $health -and $health.PSObject.Properties['startup_source_fingerprint']
    $stale = ($uvicorn -and $managed) -and ((-not $hasFingerprint) -or [bool]$health.source_drift)
    $state = if ($stale) { 'stale' } elseif ($uvicorn -and $managed) { 'managed' } elseif ($uvicorn) { 'unmanaged' } else { 'occupied' }
    [pscustomobject]@{
        State = $state
        ListenerPid = $listener.OwningProcess
        RootPid = if ($managed) { $managed.ProcessId } else { $null }
        Chain = $chain
        Health = $health
    }
}

$info = Get-BackendInfo

switch ($Action) {
    'guard' {
        if ($info.State -eq 'stopped') { exit 0 }
        if ($info.State -eq 'managed') {
            Write-Host "Managed backend already listens on port $Port (root PID $($info.RootPid))."
            exit 1
        }
        if ($info.State -eq 'stale') {
            Write-Host "Backend source is stale or unverifiable on port $Port. Run start-backend.bat restart."
            exit 3
        }
        Write-Host "Port $Port is occupied by an unmanaged process."
        $info.Chain | ForEach-Object { Write-Host "PID $($_.ProcessId): $($_.CommandLine)" }
        exit 2
    }
    'status' {
        if ($info.State -eq 'stopped') {
            Write-Host 'Backend is stopped.'
            exit 0
        }
        Write-Host "State: $($info.State)"
        Write-Host "Listener PID: $($info.ListenerPid)"
        if ($info.RootPid) { Write-Host "Managed root PID: $($info.RootPid)" }
        $info.Chain | ForEach-Object { Write-Host "PID $($_.ProcessId): $($_.CommandLine)" }
        if ($info.Health) {
            Write-Host "Health: $($info.Health.ok) ($($info.Health.name), $($info.Health.env))"
            if ($info.Health.PSObject.Properties['source_drift']) {
                Write-Host "Source drift: $($info.Health.source_drift)"
                Write-Host "Revision: $($info.Health.revision)"
            } else {
                Write-Host "Source drift: unverifiable (restart required)"
            }
        } else {
            Write-Host "Health: unavailable"
        }
        if ($info.State -eq 'stale') { exit 3 }
        exit 0
    }
    'stop' {
        if ($info.State -eq 'stopped') {
            Write-Host 'Backend is already stopped.'
            exit 0
        }
        if ($info.State -notin @('managed', 'stale') -or -not $info.RootPid) {
            Write-Host 'Refusing to stop an unmanaged or unrelated process on port 8000.'
            exit 2
        }
        taskkill /PID $info.RootPid /T /F | Out-Host
        Start-Sleep -Milliseconds 500
        if ((Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
            Write-Host "Backend process tree did not release port $Port."
            exit 3
        }
        Write-Host 'Backend stopped.'
        exit 0
    }
}
