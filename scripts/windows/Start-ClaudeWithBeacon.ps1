param(
    [string]$ProjectPath = (Get-Location).Path,
    [string]$ClaudePath,
    [switch]$Login,
    [switch]$PrepareOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClaudeArguments
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$targetProject = (Resolve-Path -LiteralPath $ProjectPath).Path
if (-not (Test-Path -LiteralPath $targetProject -PathType Container)) {
    throw 'ProjectPath must be an existing project directory.'
}
if (-not $ClaudePath) {
    # This existing portable build emits the native prompt_id required by Beacon.
    # Do not replace or upgrade the user's globally installed Claude executable.
    $ClaudePath = Join-Path $projectRoot '.tools\claude\2.1.301\claude.exe'
}
$ClaudePath = (Resolve-Path -LiteralPath $ClaudePath).Path
if ($Login) {
    if ($PrepareOnly) { Write-Output "Prepared login command: & '$ClaudePath' auth login"; return }
    & $ClaudePath auth login
    if ($LASTEXITCODE -ne 0) { throw 'Claude sign-in did not complete. Try again in an interactive terminal.' }
    return
}

$stateDirectory = Join-Path $projectRoot '.beacon'
$outputDirectory = Join-Path $stateDirectory 'generated\claude-session'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m agent_beacon.adapters.configure claude --output-dir $outputDirectory --state-dir $stateDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Beacon hook settings.' }
} finally { Pop-Location }
$settingsPath = Join-Path $outputDirectory 'claude-settings-hooks.json'
if ($PrepareOnly) {
    Write-Output "Project: $targetProject"
    Write-Output "Claude executable: $ClaudePath"
    Write-Output "Session hook settings: $settingsPath"
    Write-Output 'Prepared only. No Claude chat was started and no global settings were modified.'
    return
}

# Read only the login flag; do not print account details or authentication data.
$authLines = & $ClaudePath auth status 2>$null
$authExit = $LASTEXITCODE
try { $auth = ($authLines -join "`n") | ConvertFrom-Json }
catch { throw 'Could not check Claude sign-in. Run this script with -Login first.' }
if ($authExit -ne 0 -or $auth.loggedIn -ne $true) {
    throw 'Claude is not signed in. Run Start-ClaudeWithBeacon.ps1 -Login, then launch your project again.'
}
Push-Location -LiteralPath $targetProject
try {
    & $ClaudePath --settings $settingsPath @ClaudeArguments
} finally { Pop-Location }
