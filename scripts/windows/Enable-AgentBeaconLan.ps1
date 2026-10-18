param(
    [ValidateSet('Ethernet')]
    [string]$InterfaceAlias = 'Ethernet',
    [ValidateSet('Setup', 'Rollback')]
    [string]$Action = 'Setup'
)

$ErrorActionPreference = 'Stop'
$port = 8765
$expectedIPv4Address = '192.168.0.156'
$ruleName = 'AgentBeacon-LAN-8765'
$ruleDescription = 'Managed by Agent Beacon Enable-AgentBeaconLan.ps1; TCP 8765 on Ethernet, Private profile, LocalSubnet only.'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateDirectory = Join-Path $projectRoot '.beacon\windows'
$statePath = Join-Path $stateDirectory 'lan-setup-state.json'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-FirewallRulesByName {
    param([Parameter(Mandatory)][string]$PolicyStore)

    # NetSecurity versions differ in the CIM error they return for an exact
    # -Name miss (NotFound_Name vs NotFound_InstanceID). Enumerating the chosen
    # store avoids interpreting either error as absence; real query errors still
    # terminate because ErrorAction is Stop.
    return @(Get-NetFirewallRule -PolicyStore $PolicyStore -ErrorAction Stop |
        Where-Object { $_.Name -ieq $ruleName })
}

function Get-ManagedRule {
    # Confirm ownership in the exact store used for create/remove, then inspect
    # the effective view for collisions with policy-managed or dynamic rules.
    $persistentMatches = @(Get-FirewallRulesByName -PolicyStore PersistentStore)
    $activeMatches = @(Get-FirewallRulesByName -PolicyStore ActiveStore)
    if ($persistentMatches.Count -gt 1 -or $activeMatches.Count -gt 1) {
        throw "Multiple firewall rules named '$ruleName' exist; refusing to choose or modify one."
    }
    if ($activeMatches.Count -eq 0 -and $persistentMatches.Count -eq 0) { return $null }
    if ($persistentMatches.Count -ne 1 -or $activeMatches.Count -ne 1) {
        throw "A firewall rule named '$ruleName' exists outside the expected local persistent/effective views; refusing to modify it."
    }
    if (($persistentMatches[0].PolicyStoreSourceType -and $persistentMatches[0].PolicyStoreSourceType -ne 'Local') -or
        ($activeMatches[0].PolicyStoreSourceType -and $activeMatches[0].PolicyStoreSourceType -ne 'Local')) {
        throw "A non-local firewall policy already uses '$ruleName'; refusing to modify it."
    }
    return $persistentMatches[0]
}

function Assert-ManagedRuleExact {
    param([Parameter(Mandatory)]$Rule, [Parameter(Mandatory)][string]$ExpectedProgram)

    if ($Rule.Description -cne $ruleDescription -or $Rule.Enabled -ne 'True' -or
        $Rule.Direction -ne 'Inbound' -or $Rule.Action -ne 'Allow' -or
        $Rule.Profile -ne 'Private') {
        throw "Firewall rule '$ruleName' is not the exact Agent Beacon rule; refusing to rely on or remove it."
    }

    $portFilters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $Rule)
    $addressFilters = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $Rule)
    $interfaceFilters = @(Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $Rule)
    $applicationFilters = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $Rule)
    if ($portFilters.Count -ne 1 -or $portFilters[0].Protocol -ne 'TCP' -or
        [string]$portFilters[0].LocalPort -ne [string]$port) {
        throw "Firewall rule '$ruleName' has an unexpected protocol or port."
    }
    if ($addressFilters.Count -ne 1 -or @($addressFilters[0].RemoteAddress).Count -ne 1 -or
        [string]$addressFilters[0].RemoteAddress[0] -ne 'LocalSubnet') {
        throw "Firewall rule '$ruleName' is not restricted to LocalSubnet."
    }
    if ($interfaceFilters.Count -ne 1 -or @($interfaceFilters[0].InterfaceAlias).Count -ne 1 -or
        [string]$interfaceFilters[0].InterfaceAlias[0] -ne $InterfaceAlias) {
        throw "Firewall rule '$ruleName' is not restricted to $InterfaceAlias."
    }
    if ($applicationFilters.Count -ne 1 -or
        [IO.Path]::GetFullPath([string]$applicationFilters[0].Program) -ine $ExpectedProgram) {
        throw "Firewall rule '$ruleName' does not target the current pythonw.exe path."
    }
}

function Get-TargetNetwork {
    $adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction Stop
    if ($adapter.Name -cne 'Ethernet' -or $adapter.Status -ne 'Up' -or -not $adapter.HardwareInterface) {
        throw "The target must be the active physical Ethernet adapter named '$InterfaceAlias'. No other adapter will be changed."
    }

    $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -eq $expectedIPv4Address -and $_.AddressState -eq 'Preferred' })
    if ($addresses.Count -ne 1) {
        throw "Expected $InterfaceAlias to have preferred IPv4 $expectedIPv4Address; found $($addresses.Count) matches. No network settings were changed."
    }

    $profiles = @(Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex -ErrorAction Stop)
    if ($profiles.Count -ne 1 -or $profiles[0].NetworkCategory -notin @('Public', 'Private')) {
        throw "Could not confirm a single Public or Private network profile for $InterfaceAlias. Domain and ambiguous profiles are not changed."
    }
    if ([string]::IsNullOrWhiteSpace([string]$profiles[0].Name)) {
        throw "Could not confirm the Windows network profile name for $InterfaceAlias; refusing to change an unidentified profile."
    }
    return [pscustomobject]@{ Adapter = $adapter; Profile = $profiles[0] }
}

function Test-PortFilterMayMatchTarget {
    param([Parameter(Mandatory)]$Filter)

    $protocol = [string]$Filter.Protocol
    if ($protocol -in @('UDP', '17', 'ICMPv4', '1', 'ICMPv6', '58')) { return $false }
    # Treat unrecognized protocol values conservatively because they may be Any.
    if ($protocol -notin @('TCP', 'Any', '6', '256')) { return $true }
    $localPorts = @($Filter.LocalPort)
    if ($localPorts.Count -eq 0 -or $localPorts -contains 'Any') { return $true }
    foreach ($localPort in $localPorts) {
        foreach ($part in ([string]$localPort -split ',')) {
            if ($part -eq '8765') { return $true }
            if ($part -match '^\s*(\d+)\s*-\s*(\d+)\s*$' -and
                [int]$Matches[1] -le $port -and [int]$Matches[2] -ge $port) { return $true }
            # Unknown port expressions are treated as potentially matching.
            if ($part -notmatch '^\s*\d+\s*$') { return $true }
        }
    }
    return $false
}

function Get-VerifiedRuntimeOwnerSid {
    param([Parameter(Mandatory)][string]$ExpectedProgram)

    $recordPath = Join-Path $stateDirectory 'agent-beacon-process.json'
    try {
        if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { return $null }
        $record = Get-Content -LiteralPath $recordPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $recordPid = 0
        if (-not [int]::TryParse([string]$record.pid, [ref]$recordPid) -or $recordPid -le 0 -or
            [string]$record.project -ine $projectRoot -or
            [string]$record.executable -ine $ExpectedProgram -or
            [string]::IsNullOrWhiteSpace([string]$record.created_utc)) {
            return $null
        }

        # Verify the recorded PID is still the same Agent Beacon pythonw process.
        # Do not query or log its command line; the executable path, creation time,
        # and owner SID are sufficient for this narrowly scoped identity check.
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $recordPid" -ErrorAction Stop
        if ($null -eq $process -or [string]$process.Name -ine 'pythonw.exe' -or
            [string]$process.ExecutablePath -ine $ExpectedProgram) {
            return $null
        }
        if ($record.created_utc -is [DateTimeOffset]) {
            $recordCreated = $record.created_utc.UtcDateTime
        } elseif ($record.created_utc -is [DateTime]) {
            $recordCreated = $record.created_utc.ToUniversalTime()
        } else {
            $recordCreated = [DateTimeOffset]::Parse(
                [string]$record.created_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal
            ).UtcDateTime
        }
        $processCreated = [DateTime]$process.CreationDate
        if ([Math]::Abs(($processCreated.ToUniversalTime() - $recordCreated).TotalSeconds) -gt 5) {
            return $null
        }
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or [string]::IsNullOrWhiteSpace([string]$owner.Sid)) { return $null }
        return ([Security.Principal.SecurityIdentifier]::new([string]$owner.Sid)).Value
    } catch {
        Write-Warning 'Could not verify the recorded Agent Beacon process owner; firewall overlap checks will remain conservative.'
        return $null
    }
}

function Test-RuleBoundaryIsBroader {
    param(
        [Parameter(Mandatory)][string]$Profile,
        [Parameter(Mandatory)][string[]]$InterfaceAliases,
        [Parameter(Mandatory)][string[]]$RemoteAddresses
    )

    $profiles = @($Profile -split ',\s*' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($profiles.Count -eq 0 -or @($profiles | Where-Object { $_ -notin @('Any', 'Domain', 'Private', 'Public') }).Count -gt 0) {
        throw 'Could not safely inspect an enabled inbound allow rule profile.'
    }
    if ($profiles -notcontains 'Any' -and $profiles -notcontains 'Private') {
        return [pscustomobject]@{ CanMatchTarget = $false; IsBroader = $false }
    }

    if ($InterfaceAliases.Count -gt 0 -and $InterfaceAliases -notcontains 'Any' -and
        $InterfaceAliases -notcontains '*' -and $InterfaceAliases -notcontains $InterfaceAlias) {
        return [pscustomobject]@{ CanMatchTarget = $false; IsBroader = $false }
    }
    if ($RemoteAddresses.Count -eq 0) {
        throw 'Could not safely inspect an enabled inbound allow rule remote-address scope.'
    }

    $profileIsBroader = $profiles -contains 'Any' -or $profiles.Count -ne 1
    $interfaceIsBroader = $InterfaceAliases.Count -ne 1 -or $InterfaceAliases[0] -ne $InterfaceAlias
    $addressIsBroader = $RemoteAddresses.Count -ne 1 -or $RemoteAddresses[0] -ne 'LocalSubnet'
    return [pscustomobject]@{
        CanMatchTarget = $true
        IsBroader = ($profileIsBroader -or $interfaceIsBroader -or $addressIsBroader)
    }
}

function Test-RuleOwnerMayMatchTarget {
    param(
        [AllowNull()][string]$RuleOwnerSid,
        [AllowNull()][string]$LocalUser,
        [AllowNull()][string]$RuntimeOwnerSid
    )

    # Windows Firewall's Owner condition is a packet-identity condition when no
    # LocalUser condition is specified. Skip a SYSTEM-owned rule only when the
    # running Agent Beacon process identity was independently verified.
    if ($RuleOwnerSid -ieq 'S-1-5-18' -and $LocalUser -ieq 'Any' -and
        -not [string]::IsNullOrWhiteSpace($RuntimeOwnerSid) -and $RuntimeOwnerSid -ine $RuleOwnerSid) {
        return $false
    }
    return $true
}

function Assert-NoBroaderAllowRule {
    param([Parameter(Mandatory)][string]$ExpectedProgram)

    # Protocol, port, and program filters establish whether this rule could
    # match the fixed listener. Profile, interface, and remote address define
    # whether that matching traffic can escape the intended LAN boundary.
    $runtimeOwnerSid = Get-VerifiedRuntimeOwnerSid -ExpectedProgram $ExpectedProgram
    $rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Inbound -Action Allow -Enabled True -ErrorAction Stop)
    foreach ($rule in $rules) {
        $profiles = @(([string]$rule.Profile -split ',\s*') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($profiles.Count -eq 0 -or @($profiles | Where-Object { $_ -notin @('Any', 'Domain', 'Private', 'Public') }).Count -gt 0) {
            throw "Could not safely inspect the profile scope of enabled inbound allow rule '$($rule.Name)'."
        }
        if ($profiles -notcontains 'Any' -and $profiles -notcontains 'Private') { continue }

        $portFilters = @(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        if ($portFilters.Count -ne 1) { throw "Could not safely inspect enabled inbound allow rule '$($rule.Name)' for TCP 8765." }
        if (-not (Test-PortFilterMayMatchTarget -Filter $portFilters[0])) { continue }

        $applicationFilters = @(Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        if ($applicationFilters.Count -ne 1) { throw "Could not safely inspect the application scope of inbound rule '$($rule.Name)'." }
        $program = [string]$applicationFilters[0].Program
        if (-not [string]::IsNullOrWhiteSpace($program) -and $program -ne 'Any') {
            try { if ([IO.Path]::GetFullPath($program) -ine $ExpectedProgram) { continue } }
            catch { throw "Could not safely inspect the program path in inbound rule '$($rule.Name)'." }
        }

        $interfaceFilters = @(Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        if ($interfaceFilters.Count -ne 1) { throw "Could not safely inspect the interface scope of inbound rule '$($rule.Name)'." }
        $aliases = @($interfaceFilters[0].InterfaceAlias)

        $addressFilters = @(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
        if ($addressFilters.Count -ne 1) { throw "Could not safely inspect the remote-address scope of inbound rule '$($rule.Name)'." }
        $remoteAddresses = @($addressFilters[0].RemoteAddress)
        $boundary = Test-RuleBoundaryIsBroader -Profile ([string]$rule.Profile) -InterfaceAliases $aliases -RemoteAddresses $remoteAddresses
        if (-not $boundary.CanMatchTarget -or -not $boundary.IsBroader) { continue }

        if ([string]$rule.Owner -ieq 'S-1-5-18') {
            $securityFilters = @(Get-NetFirewallSecurityFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop)
            if ($securityFilters.Count -ne 1) { throw "Could not safely inspect the owner scope of enabled inbound rule '$($rule.Name)'." }
            if (-not (Test-RuleOwnerMayMatchTarget -RuleOwnerSid ([string]$rule.Owner) -LocalUser ([string]$securityFilters[0].LocalUser) -RuntimeOwnerSid $runtimeOwnerSid)) {
                continue
            }
        }

        if ($boundary.IsBroader) {
            throw "Enabled inbound allow rule '$($rule.Name)' may also allow Python traffic on TCP 8765 beyond this script's Private/Ethernet/LocalSubnet scope. Review that rule before setup; it was left unchanged."
        }
    }
}

function Assert-SetupRecord {
    param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)]$Network)

    if ($Record.owner -ne 'AgentBeacon.Enable-AgentBeaconLan.v1' -or
        $Record.interfaceAlias -cne $InterfaceAlias -or
        $Record.interfaceGuid -ine [string]$Network.Adapter.InterfaceGuid -or
        $Record.networkProfileName -cne [string]$Network.Profile.Name -or
        $Record.ipv4Address -cne $expectedIPv4Address -or
        $Record.ruleName -cne $ruleName -or
        $Record.previousNetworkCategory -notin @('Public', 'Private') -or
        $Record.changedNetworkCategory -isnot [bool] -or
        $Record.createdFirewallRule -isnot [bool] -or
        $Record.changedNetworkCategory -ne ($Record.previousNetworkCategory -eq 'Public') -or
        [string]::IsNullOrWhiteSpace([string]$Record.pythonwPath)) {
        throw 'LAN setup record is invalid or does not match this exact interface; refusing to rely on or overwrite it.'
    }
}

if (-not (Test-IsAdministrator)) {
    throw 'Administrator rights are required. Open PowerShell with Run as administrator, then rerun this script.'
}

$network = Get-TargetNetwork
$existingRule = Get-ManagedRule

if ($Action -eq 'Rollback') {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        Write-Output 'No Agent Beacon LAN setup record exists; nothing was changed.'
        exit 0
    }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    Assert-SetupRecord -Record $state -Network $network

    if ($state.createdFirewallRule -eq $true) {
        if ($null -eq $existingRule) {
            Write-Output 'The recorded firewall rule is already absent.'
        } else {
            Assert-ManagedRuleExact -Rule $existingRule -ExpectedProgram ([string]$state.pythonwPath)
            Remove-NetFirewallRule -PolicyStore PersistentStore -Name $ruleName
            Write-Output 'Removed the Agent Beacon rule created by this script.'
        }
    }

    if ($state.changedNetworkCategory -eq $true) {
        if ($network.Profile.NetworkCategory -eq 'Private') {
            Set-NetConnectionProfile -InterfaceIndex $network.Adapter.ifIndex -NetworkCategory Public
            Write-Output 'Restored this Ethernet interface to its recorded Public category.'
        } elseif ($network.Profile.NetworkCategory -eq 'Public') {
            Write-Output 'The Ethernet profile is already restored to Public.'
        } else {
            throw "The current profile is '$($network.Profile.NetworkCategory)'; firewall rollback may be complete, but the profile and state record were preserved."
        }
    }

    Remove-Item -LiteralPath $statePath -Force
    Write-Output 'Agent Beacon LAN rollback completed.'
    exit 0
}

$pythonwCommand = Get-Command pythonw.exe -ErrorAction Stop
$pythonwPath = [IO.Path]::GetFullPath($pythonwCommand.Source)
if ($Action -eq 'Setup') {
    $privateFirewallProfile = @(Get-NetFirewallProfile -PolicyStore ActiveStore -Profile Private -ErrorAction Stop)
    if ($privateFirewallProfile.Count -ne 1 -or $privateFirewallProfile[0].Enabled -ne $true -or
        $privateFirewallProfile[0].DefaultInboundAction -ne 'Block') {
        throw 'The Windows Private firewall profile is not confirmed enabled with a default inbound block; refusing setup because the allow rule would not provide the documented protection.'
    }
    Assert-NoBroaderAllowRule -ExpectedProgram $pythonwPath
}

# Existing state is the proof that an existing same-name rule belongs to this
# setup. Without it, even an identically shaped rule is left untouched.
$state = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    Assert-SetupRecord -Record $state -Network $network
}
if ($null -ne $existingRule) {
    if ($null -eq $state -or $state.createdFirewallRule -ne $true) {
        throw "Firewall rule '$ruleName' already exists without a matching ownership record; refusing to overwrite it."
    }
    Assert-ManagedRuleExact -Rule $existingRule -ExpectedProgram ([string]$state.pythonwPath)
    if ([string]$state.pythonwPath -ine $pythonwPath) {
        throw 'pythonw.exe changed since setup; refusing to silently retarget the existing firewall rule.'
    }
} elseif ($null -ne $state -and $state.createdFirewallRule -eq $true) {
    throw 'The setup record says this script created a rule, but that rule is absent. Inspect the Windows Firewall before rerunning.'
}

if ($network.Profile.NetworkCategory -eq 'Private' -and $null -ne $state) {
    Write-Output 'Agent Beacon LAN firewall rule and Private profile are already configured.'
    exit 0
}

$changedCategoryThisRun = $false
$createdRuleThisRun = $false
$previousCategory = if ($null -ne $state) { [string]$state.previousNetworkCategory } else { [string]$network.Profile.NetworkCategory }
$createdFirewallRule = if ($null -ne $state) { [bool]$state.createdFirewallRule } else { $false }
try {
    if ($network.Profile.NetworkCategory -eq 'Public') {
        Set-NetConnectionProfile -InterfaceIndex $network.Adapter.ifIndex -NetworkCategory Private
        $changedCategoryThisRun = $true
    }
    if ($null -eq $existingRule) {
        New-NetFirewallRule -Name $ruleName -DisplayName 'Agent Beacon private LAN (TCP 8765)' `
            -Description $ruleDescription -Direction Inbound -Action Allow -Enabled True `
            -Profile Private -Program $pythonwPath -Protocol TCP -LocalPort $port `
            -RemoteAddress LocalSubnet -InterfaceAlias $InterfaceAlias | Out-Null
        $createdRuleThisRun = $true
        $createdFirewallRule = $true
    }

    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    $record = [ordered]@{
        owner = 'AgentBeacon.Enable-AgentBeaconLan.v1'
        interfaceAlias = $InterfaceAlias
        interfaceIndex = [int]$network.Adapter.ifIndex
        interfaceGuid = [string]$network.Adapter.InterfaceGuid
        networkProfileName = [string]$network.Profile.Name
        ipv4Address = $expectedIPv4Address
        ruleName = $ruleName
        pythonwPath = $pythonwPath
        previousNetworkCategory = $previousCategory
        changedNetworkCategory = ($previousCategory -eq 'Public')
        createdFirewallRule = $createdFirewallRule
    }
    $temporaryStatePath = "$statePath.tmp-$PID"
    try {
        $record | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryStatePath -Encoding UTF8
        Move-Item -LiteralPath $temporaryStatePath -Destination $statePath -Force
    } catch {
        Remove-Item -LiteralPath $temporaryStatePath -Force -ErrorAction SilentlyContinue
        throw
    }
} catch {
    $setupError = $_
    $rollbackErrors = [System.Collections.Generic.List[string]]::new()
    if ($createdRuleThisRun) {
        try {
            $failedRule = Get-ManagedRule
            if ($null -ne $failedRule) {
                Assert-ManagedRuleExact -Rule $failedRule -ExpectedProgram $pythonwPath
                Remove-NetFirewallRule -PolicyStore PersistentStore -Name $ruleName
            }
        } catch { $rollbackErrors.Add("Created firewall rule could not be rolled back: $($_.Exception.Message)") }
    }
    if ($changedCategoryThisRun) {
        try {
            $currentProfile = Get-NetConnectionProfile -InterfaceIndex $network.Adapter.ifIndex -ErrorAction Stop
            if ($currentProfile.NetworkCategory -eq 'Private') {
                Set-NetConnectionProfile -InterfaceIndex $network.Adapter.ifIndex -NetworkCategory Public
            } else {
                $rollbackErrors.Add("Network profile is now '$($currentProfile.NetworkCategory)'; it was not overwritten during rollback.")
            }
        } catch { $rollbackErrors.Add("Changed network category could not be rolled back: $($_.Exception.Message)") }
    }
    if ($rollbackErrors.Count -gt 0) {
        throw "LAN setup failed: $($setupError.Exception.Message) Rollback needs attention: $($rollbackErrors -join ' '); inspect the Windows Firewall and network profile before retrying."
    }
    throw "LAN setup failed and changes made by this run were rolled back: $($setupError.Exception.Message)"
}

Write-Output "Configured TCP $port for pythonw.exe on $InterfaceAlias ($expectedIPv4Address), Private profile, and LocalSubnet only."
Write-Output "Rollback with: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action Rollback"
