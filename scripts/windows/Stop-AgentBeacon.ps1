$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$statePath = Join-Path $projectRoot '.beacon\windows\agent-beacon-process.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Write-Output 'No Agent Beacon launcher record exists.'
    exit 0
}
$record = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$processId = 0
if ($record.project -ne $projectRoot -or
    -not [int]::TryParse([string]$record.pid, [ref]$processId) -or $processId -le 0 -or
    -not $record.created_utc -or -not $record.executable) {
    throw 'Launcher record is invalid; refusing to stop any process.'
}
$processHandle = $null
try { $processHandle = [System.Diagnostics.Process]::GetProcessById($processId) }
catch [System.ArgumentException] { $processHandle = $null }
if ($null -eq $processHandle) {
    Remove-Item -LiteralPath $statePath -Force
    Write-Output 'The recorded Agent Beacon process is already stopped.'
    exit 0
}
$expectedExecutable = [IO.Path]::GetFullPath([string]$record.executable)
$recordedCreatedValue = $record.created_utc
if ($recordedCreatedValue -is [DateTime]) {
    $recordedCreatedUtc = ([DateTime]$recordedCreatedValue).ToUniversalTime()
} else {
    $recordedCreatedUtc = [DateTime]::MinValue
    $dateStyles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    if (-not [DateTime]::TryParse([string]$recordedCreatedValue, [Globalization.CultureInfo]::InvariantCulture,
            $dateStyles, [ref]$recordedCreatedUtc)) {
        throw 'Launcher record has an invalid process creation time; refusing to stop any process.'
    }
}
$retainedNativeHandle = $null
try {
    # Open and retain the OS handle before checking PID metadata. Kill() then
    # targets this same process object even if Windows later reuses its PID.
    $retainedNativeHandle = $processHandle.Handle
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        if ($processHandle.HasExited) {
            Remove-Item -LiteralPath $statePath -Force
            Write-Output 'The recorded Agent Beacon process is already stopped.'
            exit 0
        }
        throw 'The process identity could not be verified; refusing to stop any process.'
    }
    $actualExecutable = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $commandMatches = $process.CommandLine -match '(?i)(^|\s)-m\s+agent_beacon(?:\s|$)'
    $actualCreatedUtc = $processHandle.StartTime.ToUniversalTime()
    if ($actualExecutable -ine $expectedExecutable -or -not $commandMatches -or
        $actualCreatedUtc.Ticks -ne $recordedCreatedUtc.Ticks) {
        throw 'PID identity no longer matches the recorded Agent Beacon process; refusing to stop it.'
    }
    $processHandle.Refresh()
    if (-not $processHandle.HasExited) { $processHandle.Kill(); $processHandle.WaitForExit() }
    Remove-Item -LiteralPath $statePath -Force
    Write-Output "Stopped Agent Beacon (PID $processId)."
} finally {
    if ($null -ne $processHandle) { $processHandle.Dispose() }
}
