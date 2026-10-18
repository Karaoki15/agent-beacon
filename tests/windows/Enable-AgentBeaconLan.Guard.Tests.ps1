$ErrorActionPreference = 'Stop'
$script:InterfaceAlias = 'Ethernet'
$script:port = 8765

$scriptPath = Join-Path $PSScriptRoot '..\..\scripts\windows\Enable-AgentBeaconLan.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {
    throw "LAN helper has PowerShell parse errors: $($parseErrors -join '; ')"
}

$functionNames = @(
    'Get-VerifiedRuntimeOwnerSid',
    'Test-PortFilterMayMatchTarget',
    'Test-RuleBoundaryIsBroader',
    'Test-RuleOwnerMayMatchTarget',
    'Assert-NoBroaderAllowRule'
)
$functionNodes = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $functionNames -contains $node.Name
}, $false)
if ($functionNodes.Count -ne $functionNames.Count) {
    throw 'Could not isolate the LAN helper guard functions for tests.'
}
foreach ($functionNode in $functionNodes) {
    . ([scriptblock]::Create($functionNode.Extent.Text))
}

$script:assertionCount = 0
function Assert-Equal {
    param([object]$Expected, [object]$Actual, [string]$Message)
    $script:assertionCount++
    if ($Expected -ne $Actual) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

$broadListenerFilter = [pscustomobject]@{ Protocol = 'Any'; LocalPort = 'Any' }
Assert-Equal $true (Test-PortFilterMayMatchTarget -Filter $broadListenerFilter) 'Any protocol/port must be considered a possible TCP 8765 match'

$exactLanScope = Test-RuleBoundaryIsBroader -Profile 'Private' -InterfaceAliases @('Ethernet') -RemoteAddresses @('LocalSubnet')
Assert-Equal $true $exactLanScope.CanMatchTarget 'Exact LAN scope should include the target'
Assert-Equal $false $exactLanScope.IsBroader 'Exact LAN scope should not be treated as broader even when its port/program filters are Any'

$broadRemoteScope = Test-RuleBoundaryIsBroader -Profile 'Private' -InterfaceAliases @('Ethernet') -RemoteAddresses @('Any')
Assert-Equal $true $broadRemoteScope.IsBroader 'Any remote address must be rejected as broader'

$broadProfileScope = Test-RuleBoundaryIsBroader -Profile 'Any' -InterfaceAliases @('Ethernet') -RemoteAddresses @('LocalSubnet')
Assert-Equal $true $broadProfileScope.IsBroader 'Any profile must be rejected as broader'

$otherInterfaceScope = Test-RuleBoundaryIsBroader -Profile 'Private' -InterfaceAliases @('Wi-Fi') -RemoteAddresses @('LocalSubnet')
Assert-Equal $false $otherInterfaceScope.CanMatchTarget 'A rule limited to another interface must not match Ethernet traffic'

$knownRuntimeUserSid = 'S-1-5-21-111-222-333-1001'
Assert-Equal $false (Test-RuleOwnerMayMatchTarget -RuleOwnerSid 'S-1-5-18' -LocalUser 'Any' -RuntimeOwnerSid $knownRuntimeUserSid) 'Verified non-SYSTEM runtime owner should exclude SYSTEM-owned rules'
Assert-Equal $true (Test-RuleOwnerMayMatchTarget -RuleOwnerSid 'S-1-5-18' -LocalUser 'Any' -RuntimeOwnerSid $null) 'Unknown runtime owner must keep SYSTEM-owned rules as possible matches'
Assert-Equal $true (Test-RuleOwnerMayMatchTarget -RuleOwnerSid 'S-1-5-18' -LocalUser 'D:(A;;CC;;;S-1-5-21-1-2-3-1001)' -RuntimeOwnerSid $knownRuntimeUserSid) 'A non-Any LocalUser condition must keep SYSTEM-owned rules as possible matches'
Assert-Equal $true (Test-RuleOwnerMayMatchTarget -RuleOwnerSid 'S-1-5-18' -LocalUser 'Any' -RuntimeOwnerSid 'S-1-5-18') 'SYSTEM runtime owner must not exclude SYSTEM-owned rules'

$script:stateDirectory = 'C:\mock\project\.beacon\windows'
$script:projectRoot = 'C:\mock\project'
$script:expectedProgram = 'C:\mock\pythonw.exe'
$script:mockCreatedUtc = [DateTimeOffset]::UtcNow.ToString('o')
$script:mockOwnerSid = $knownRuntimeUserSid
function Test-Path { return $true }
function Get-Content {
    return '{"pid":1234,"project":"C:\\mock\\project","executable":"C:\\mock\\pythonw.exe","created_utc":"' + $script:mockCreatedUtc + '"}'
}
function Get-CimInstance {
    return [pscustomobject]@{
        ProcessId = 1234
        Name = 'pythonw.exe'
        ExecutablePath = $script:expectedProgram
        CreationDate = [DateTimeOffset]::Parse($script:mockCreatedUtc).UtcDateTime
    }
}
function Invoke-CimMethod {
    return [pscustomobject]@{ ReturnValue = 0; Sid = $script:mockOwnerSid }
}
Assert-Equal $knownRuntimeUserSid (Get-VerifiedRuntimeOwnerSid -ExpectedProgram $script:expectedProgram) 'Matching process record and live process should verify its owner SID'
$script:expectedProgram = 'C:\different\pythonw.exe'
Assert-Equal $null (Get-VerifiedRuntimeOwnerSid -ExpectedProgram $script:expectedProgram) 'A process executable mismatch must prevent owner verification'

$script:expectedProgram = 'C:\mock\pythonw.exe'
$script:mockRule = [pscustomobject]@{ Name = 'mock-rule'; Profile = 'Private'; Owner = $knownRuntimeUserSid }
$script:mockPortFilter = [pscustomobject]@{ Protocol = 'Any'; LocalPort = 'Any' }
$script:mockApplicationFilter = [pscustomobject]@{ Program = 'Any' }
$script:mockInterfaceFilter = [pscustomobject]@{ InterfaceAlias = @('Ethernet') }
$script:mockAddressFilter = [pscustomobject]@{ RemoteAddress = @('LocalSubnet') }
$script:mockSecurityFilter = [pscustomobject]@{ LocalUser = 'Any' }
$script:mockRuntimeOwnerSid = $knownRuntimeUserSid
function Get-VerifiedRuntimeOwnerSid { return $script:mockRuntimeOwnerSid }
function Get-NetFirewallRule { return @($script:mockRule) }
function Get-NetFirewallPortFilter { return @($script:mockPortFilter) }
function Get-NetFirewallApplicationFilter { return @($script:mockApplicationFilter) }
function Get-NetFirewallInterfaceFilter { return @($script:mockInterfaceFilter) }
function Get-NetFirewallAddressFilter { return @($script:mockAddressFilter) }
function Get-NetFirewallSecurityFilter { return @($script:mockSecurityFilter) }

Assert-NoBroaderAllowRule -ExpectedProgram $script:expectedProgram
$script:assertionCount++

function Assert-GuardThrows {
    param([string]$Message)
    $script:assertionCount++
    try {
        Assert-NoBroaderAllowRule -ExpectedProgram $script:expectedProgram
    } catch {
        return
    }
    throw $Message
}

$script:mockAddressFilter = [pscustomobject]@{ RemoteAddress = @('Any') }
Assert-GuardThrows 'Broader remote scope must block setup'
$script:mockAddressFilter = [pscustomobject]@{ RemoteAddress = @('LocalSubnet') }
$script:mockRule.Profile = 'Any'
Assert-GuardThrows 'Broader profile scope must block setup'
$script:mockRule.Profile = 'Private'
$script:mockRule.Owner = 'S-1-5-18'
$script:mockAddressFilter = [pscustomobject]@{ RemoteAddress = @('Any') }
$script:mockRuntimeOwnerSid = $knownRuntimeUserSid
Assert-NoBroaderAllowRule -ExpectedProgram $script:expectedProgram
$script:assertionCount++
$script:mockRuntimeOwnerSid = $null
Assert-GuardThrows 'Unknown runtime owner must not skip a SYSTEM-owned rule'
$script:mockRuntimeOwnerSid = $knownRuntimeUserSid
$script:mockSecurityFilter = [pscustomobject]@{ LocalUser = 'D:(A;;CC;;;S-1-5-21-111-222-333-1001)' }
Assert-GuardThrows 'Non-Any LocalUser condition must not be skipped based on Owner'

Write-Output "LAN helper guard parser and isolated checks passed ($script:assertionCount assertions)."
