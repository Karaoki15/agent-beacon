param(
    [switch]$Lan,
    [switch]$PersistDevices,
    [switch]$NoPersistDevices,
    [ValidateSet('codex', 'claude', 'antigravity')]
    [string[]]$Integrations = @('codex', 'claude'),
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [string]$Pythonw
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Pythonw) {
    $pythonCommand = Get-Command pythonw.exe -ErrorAction Stop
    $Pythonw = $pythonCommand.Source
}
$Pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
$stateDirectory = Join-Path $projectRoot '.beacon\windows'
$statePath = Join-Path $stateDirectory 'agent-beacon-process.json'
$deviceStore = Join-Path $stateDirectory 'devices.json'
$bindHost = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }

$rememberedPort = 8765
$portPath = Join-Path $stateDirectory 'server-port.json'
if (Test-Path -LiteralPath $portPath -PathType Leaf) {
    try {
        $savedPort = (Get-Content -LiteralPath $portPath -Raw | ConvertFrom-Json).port
        if ($savedPort -ge 1 -and $savedPort -le 65535) { $rememberedPort = [int]$savedPort }
    } catch { Write-Warning 'Saved port is invalid; looking for a free default port.' }
}
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $record = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($record.port -ge 1 -and $record.port -le 65535) { $rememberedPort = [int]$record.port }
        $recordedProcessId = 0
        if ($record.project -eq $projectRoot -and [int]::TryParse([string]$record.pid, [ref]$recordedProcessId) -and $recordedProcessId -gt 0) {
            $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedProcessId" -ErrorAction SilentlyContinue
            $live = Get-Process -Id $recordedProcessId -ErrorAction SilentlyContinue
            $created = if ($record.created_utc -is [DateTime]) { $record.created_utc.ToUniversalTime() } else { [DateTime]::Parse([string]$record.created_utc).ToUniversalTime() }
            $owned = @(Get-NetTCPConnection -LocalPort $rememberedPort -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $recordedProcessId })
            if ($candidate -and $live -and $live.StartTime.ToUniversalTime().Ticks -eq $created.Ticks -and
                [IO.Path]::GetFullPath([string]$candidate.ExecutablePath) -ieq $Pythonw -and
                $candidate.CommandLine -match '(?i)(^|\s)-m\s+agent_beacon(?:\s|$)' -and $owned.Count -gt 0) {
                if ($Port -ne 0 -and $Port -ne $rememberedPort) { throw 'Beacon is already running. Stop it before requesting another port.' }
                $control = Invoke-RestMethod -Uri "http://127.0.0.1:$rememberedPort/api/control" -TimeoutSec 2
                if ($control.instance_id -is [string] -and $control.viewer_token -is [string]) {
                    if ($Lan -and -not $control.lan_enabled) { throw 'Beacon is running in loopback mode. Restart it with -Lan for phone access.' }
                    Write-Output "Agent Beacon already running (PID $recordedProcessId). http://127.0.0.1:$rememberedPort/"
                    return
                }
            }
        }
    } catch {
        if ($_.Exception.Message -like 'Beacon is already running.*' -or $_.Exception.Message -like 'Beacon is running in loopback mode.*') { throw }
        # An invalid/stale record cannot authorize stopping or reusing a process.
        Write-Warning "Could not reuse the recorded Beacon instance: $($_.Exception.Message)"
    }
}
$candidates = if ($Port -ne 0) { @($Port) } else { @($rememberedPort) + @(8765..8775) | Select-Object -Unique }
$selectedPort = 0
foreach ($candidatePort in $candidates) {
    $listeners = @(Get-NetTCPConnection -LocalPort $candidatePort -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { $selectedPort = $candidatePort; break }
}
if ($selectedPort -eq 0) { throw 'No free Beacon port was found; existing services were left running.' }

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$arguments = "-m agent_beacon --tray --host $bindHost --port $selectedPort"
if ($Integrations.Count -gt 0) { $arguments += " --integrations $($Integrations -join ' ')" }
$persist = ($Lan -or $PersistDevices) -and -not $NoPersistDevices
if ($persist) {
    $quotedStore = '"' + $deviceStore.Replace('"', '\"') + '"'
    $arguments += " --device-store $quotedStore"
}
$process = Start-Process -FilePath $Pythonw -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
$verified = $false
$lastFailure = 'process did not open the expected listener'
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 250
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction SilentlyContinue
    if ($null -eq $candidate) { $lastFailure = 'process exited during startup'; break }
    $sameProcess = [IO.Path]::GetFullPath([string]$candidate.ExecutablePath) -ieq $Pythonw -and
        $candidate.CommandLine -match '(?i)(^|\s)-m\s+agent_beacon(?:\s|$)'
    if (-not $sameProcess) { $lastFailure = 'launched process identity did not match'; break }
    $ownedListener = @(Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -eq $process.Id })
    if ($ownedListener.Count -eq 0) { continue }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$selectedPort/api/control" -UseBasicParsing -TimeoutSec 2
        $control = $response.Content | ConvertFrom-Json
        if ($response.StatusCode -eq 200 -and $control.instance_id -is [string] -and
            $control.instance_id.Length -gt 0 -and $control.viewer_token -is [string] -and
            $control.viewer_token.Length -ge 16) {
            $verified = $true
            break
        }
        $lastFailure = 'local API returned an invalid control response'
    } catch {
        $lastFailure = 'local API is not ready'
    }
}
if (-not $verified) {
    throw "Agent Beacon did not pass process, listener, and local API checks ($lastFailure). Existing stop record was preserved."
}

$created = (Get-Process -Id $process.Id).StartTime.ToUniversalTime().ToString('o')
$record = @{ pid = $process.Id; created_utc = $created; executable = $Pythonw; project = $projectRoot; port = $selectedPort }
$temporaryRecord = "$statePath.tmp-$($process.Id)"
try {
    $record | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryRecord -Encoding UTF8
    Move-Item -LiteralPath $temporaryRecord -Destination $statePath -Force
    $portRecord = "$portPath.tmp-$($process.Id)"
    @{port=$selectedPort} | ConvertTo-Json -Compress | Set-Content -LiteralPath $portRecord -Encoding UTF8
    Move-Item -LiteralPath $portRecord -Destination $portPath -Force
} catch {
    Remove-Item -LiteralPath $temporaryRecord -Force -ErrorAction SilentlyContinue
    throw 'Agent Beacon is running, but its stop record could not be saved; the previous record was preserved.'
}
Write-Output "Agent Beacon started (PID $($process.Id), bind $bindHost)."
Write-Output "Open: http://127.0.0.1:$selectedPort/"
Write-Output "Provider integrations: $($Integrations -join ', ')"
if ($persist) { Write-Output 'Paired devices will persist across restarts.' }
Write-Output "Stop it with: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'Stop-AgentBeacon.ps1')`""
