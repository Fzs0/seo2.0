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
    $state = if ($uvicorn -and $managed) { 'managed' } elseif ($uvicorn) { 'unmanaged' } else { 'occupied' }
    [pscustomobject]@{
        State = $state
        ListenerPid = $listener.OwningProcess
        RootPid = if ($managed) { $managed.ProcessId } else { $null }
        Chain = $chain
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
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
            Write-Host "Health: $($health.ok) ($($health.name), $($health.env))"
        } catch {
            Write-Host "Health: unavailable ($($_.Exception.Message))"
        }
        exit 0
    }
    'stop' {
        if ($info.State -eq 'stopped') {
            Write-Host 'Backend is already stopped.'
            exit 0
        }
        if ($info.State -ne 'managed' -or -not $info.RootPid) {
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
