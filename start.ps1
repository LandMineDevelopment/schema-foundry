param(
    [ValidateSet("ui", "docker-db", "ai", "ai-local-db", "ai-docker-db", "credentials-backup", "credentials-restore", "credentials-rotate")]
    [string]$Mode = "ai-docker-db",
    [string]$Path,
    [switch]$NoOpen,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$runningOnWindows = $env:OS -eq "Windows_NT"

if ($Help) {
    Write-Host "Usage: powershell -ExecutionPolicy Bypass -File .\start.ps1 [-Mode <mode>] [-NoOpen]"
    Write-Host ""
    Write-Host "Modes:"
    Write-Host "  ai-docker-db  Complete UI, tutorial PostgreSQL, and AI stack (default)"
    Write-Host "  ui            Local schema design only"
    Write-Host "  docker-db     UI and tutorial PostgreSQL without AI"
    Write-Host "  ai            UI and AI without included PostgreSQL"
    Write-Host "  ai-local-db   Linux host PostgreSQL with AI"
    Write-Host ""
    Write-Host "Credential lifecycle:"
    Write-Host "  -Mode credentials-backup -Path <directory>"
    Write-Host "  -Mode credentials-restore -Path <directory>"
    Write-Host "  -Mode credentials-rotate"
    Write-Host ""
    Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File .\uninstall.ps1"
    Write-Host "Setup help: https://github.com/LandMineDevelopment/schemii#install-docker"
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, reopen PowerShell, and see https://github.com/LandMineDevelopment/schemii#install-docker"
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed, but the daemon is unavailable or access was denied. Start Docker Desktop, run 'docker info', and see https://github.com/LandMineDevelopment/schemii#docker-is-installed-but-unavailable"
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose was not found. Update Docker Desktop or install Compose from https://docs.docker.com/compose/install/"
}

$scriptDirectory = (Resolve-Path $PSScriptRoot).Path
$project = $env:SCHEMII_INSTANCE
if (-not $project) {
    $legacyContainer = (& docker ps -aq --filter "label=com.docker.compose.project=schemii" --filter "label=com.docker.compose.service=schemii" | Select-Object -First 1)
    $legacyWorkingDirectory = if ($legacyContainer) { (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' $legacyContainer 2>$null) } else { "" }
    if ($legacyWorkingDirectory -eq $scriptDirectory) {
        $project = "schemii"
    }
    elseif (-not $legacyContainer) {
        docker volume inspect schemii_schemii-config *> $null
        $legacyConfig = $LASTEXITCODE -eq 0
        docker volume inspect schemii_schemii-schemas *> $null
        $legacySchemas = $LASTEXITCODE -eq 0
        if ($legacyConfig -and $legacySchemas) {
            throw "Legacy Schemii data volumes were found without a container that identifies their installation directory. Reuse them with `$env:SCHEMII_INSTANCE='schemii'; .\start.ps1 -Mode $Mode, or choose another unique instance name for a separate installation."
        }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($scriptDirectory)
            $hash = $sha.ComputeHash($bytes)
            $instanceNumber = [BitConverter]::ToUInt32($hash, 0)
        }
        finally {
            $sha.Dispose()
        }
        $project = "schemii-$instanceNumber"
    }
    else {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($scriptDirectory)
            $hash = $sha.ComputeHash($bytes)
            $instanceNumber = [BitConverter]::ToUInt32($hash, 0)
        }
        finally {
            $sha.Dispose()
        }
        $project = "schemii-$instanceNumber"
    }
}
if ($project -cnotmatch '^[a-z0-9][a-z0-9_-]*$') {
    throw "SCHEMII_INSTANCE must contain only lowercase letters, numbers, hyphens, or underscores."
}

$credentialRoot = if ($env:SCHEMII_CREDENTIAL_ROOT) { $env:SCHEMII_CREDENTIAL_ROOT } elseif ($runningOnWindows) { Join-Path $env:LOCALAPPDATA "Schemii\credentials" } else { Join-Path $HOME ".local/share/schemii/credentials" }
$credentialDirectory = if ($env:SCHEMII_CREDENTIAL_DIR) { $env:SCHEMII_CREDENTIAL_DIR } else { Join-Path $credentialRoot $project }
if (-not [System.IO.Path]::IsPathRooted($credentialDirectory)) { throw "SCHEMII_CREDENTIAL_DIR must be an absolute path." }
$credentialFiles = @("metadata_bootstrap_password", "metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")
$credentialTransaction = Join-Path $credentialDirectory ".credential-transaction"
function Protect-CredentialPath([string]$Target, [bool]$Container) {
    if (-not $runningOnWindows) {
        & chmod $(if ($Container) { "700" } else { "600" }) $Target
        if ($LASTEXITCODE -ne 0) { throw "Could not restrict credential permissions: $Target" }
        return
    }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $sid) { throw "Could not determine the current Windows user for credential ACLs." }
    $security = if ($Container) { [System.Security.AccessControl.DirectorySecurity]::new() } else { [System.Security.AccessControl.FileSecurity]::new() }
    $security.SetOwner($sid)
    $security.SetAccessRuleProtection($true, $false)
    $inheritance = if ($Container) { [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit } else { [System.Security.AccessControl.InheritanceFlags]::None }
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
    Set-Acl -LiteralPath $Target -AclObject $security
    $verified = Get-Acl -LiteralPath $Target
    $verifiedOwner = $verified.GetOwner([System.Security.Principal.SecurityIdentifier])
    $verifiedRules = @($verified.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    $invalidRule = @($verifiedRules | Where-Object {
        $_.IdentityReference -ne $sid -or $_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or $_.IsInherited -or
        ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -ne [System.Security.AccessControl.FileSystemRights]::FullControl
    })
    if ($verified.AreAccessRulesProtected -ne $true -or $verifiedOwner -ne $sid -or $verifiedRules.Count -ne 1 -or $invalidRule.Count -ne 0) {
        throw "Credential ACL verification failed closed: $Target"
    }
}
function Protect-CredentialTree([string]$Directory) {
    Protect-CredentialPath $Directory $true
    foreach ($item in @(Get-ChildItem -LiteralPath $Directory -Force -Recurse)) {
        Protect-CredentialPath $item.FullName $item.PSIsContainer
    }
}
$credentialParent = Split-Path -Parent $credentialDirectory
New-Item -ItemType Directory -Force -Path $credentialParent | Out-Null
$credentialLockPath = "${credentialDirectory}.lock"
$credentialLock = $null
$lockDeadline = [DateTime]::UtcNow.AddSeconds(60)
while (-not $credentialLock) {
    try {
        $credentialLock = [System.IO.File]::Open($credentialLockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    }
    catch [System.IO.IOException] {
        if ([DateTime]::UtcNow -ge $lockDeadline) { throw "Timed out waiting for another launcher credential operation for $project." }
        Start-Sleep -Seconds 1
    }
}
function Exit-CredentialLock {
    if ($script:credentialLock) {
        $script:credentialLock.Dispose()
        $script:credentialLock = $null
    }
}
try {
    Protect-CredentialPath $credentialLockPath $false
    function New-CredentialValue {
        $bytes = [byte[]]::new(32)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        return [Convert]::ToHexString($bytes).ToLowerInvariant()
    }
function Write-CredentialFile([string]$Target, [string]$Value) {
    if ($Value -cnotmatch '^[A-Za-z0-9_-]{16,256}$') { throw "Refusing to write an invalid credential." }
    [System.IO.File]::WriteAllText($Target, $Value + "`n", [System.Text.UTF8Encoding]::new($false))
    Protect-CredentialPath $Target $false
}
function Read-CredentialValue([string]$Target, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "$Name is missing." }
    $raw = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
    if ($raw -cnotmatch '\A([A-Za-z0-9_-]{16,256})(?:\n)?\z') { throw "$Name must be one line containing 16-256 characters from [A-Za-z0-9_-] with an optional LF terminator." }
    return $Matches[1]
}
function Write-InstanceMarker([string]$Target, [string]$Value) {
    if (-not $Value -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw "Invalid instance marker." }
    [System.IO.File]::WriteAllText($Target, $Value + "`n", [System.Text.UTF8Encoding]::new($false))
    Protect-CredentialPath $Target $false
}
function Read-InstanceMarker([string]$Target, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "$Name is missing." }
    $raw = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
    if ($raw -cnotmatch '\A([^\r\n]+)(?:\r?\n)?\z') { throw "$Name must contain exactly one nonempty line." }
    return $Matches[1]
}
function Replace-CredentialFile([string]$Target, [string]$Value) {
    $temporary = Join-Path $credentialDirectory (".credential." + [Guid]::NewGuid().ToString("N"))
    try {
        Write-CredentialFile $temporary $Value
        # Preserve the file identity so existing Compose secret bind mounts
        # observe the update. The transaction retains both sets for recovery.
        [System.IO.File]::WriteAllBytes($Target, [System.IO.File]::ReadAllBytes($temporary))
        Protect-CredentialPath $Target $false
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}
New-Item -ItemType Directory -Force -Path $credentialDirectory | Out-Null
Protect-CredentialTree $credentialDirectory
$instanceFile = Join-Path $credentialDirectory "instance"
if (Test-Path $instanceFile) {
    if ((Read-InstanceMarker $instanceFile "Credential instance marker") -cne $project) { throw "Credential directory belongs to a different instance; refusing to use it." }
}
else { Write-InstanceMarker $instanceFile $project }

docker volume inspect "${project}_schemii-metadata-postgres" *> $null
$legacyMetadata = $LASTEXITCODE -eq 0
$migrationFile = Join-Path $credentialDirectory "metadata_migration_password"
if ($legacyMetadata -and -not (Test-Path $migrationFile)) {
    $legacyMetadataContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
    $legacyValues = @{}
    if ($legacyMetadataContainer) {
        $details = (& docker inspect $legacyMetadataContainer | ConvertFrom-Json)[0]
        foreach ($item in $details.Config.Env) {
            $separator = $item.IndexOf("=")
            if ($separator -gt 0) { $legacyValues[$item.Substring(0, $separator)] = $item.Substring($separator + 1) }
        }
    }
    $defaults = @{
        metadata_bootstrap_password = if ($legacyValues.POSTGRES_PASSWORD) { $legacyValues.POSTGRES_PASSWORD } else { "schemii-metadata-bootstrap-local" }
        metadata_migration_password = if ($legacyValues.SCHEMII_METADATA_MIGRATION_PASSWORD) { $legacyValues.SCHEMII_METADATA_MIGRATION_PASSWORD } else { "schemii-metadata-migration-local" }
        metadata_schemii_password = if ($legacyValues.SCHEMII_METADATA_SCHEMII_PASSWORD) { $legacyValues.SCHEMII_METADATA_SCHEMII_PASSWORD } else { "schemii-metadata-runtime-local" }
        metadata_schemer_password = if ($legacyValues.SCHEMII_METADATA_SCHEMER_PASSWORD) { $legacyValues.SCHEMII_METADATA_SCHEMER_PASSWORD } else { "schemer-metadata-runtime-local" }
    }
    foreach ($name in $defaults.Keys) { Write-CredentialFile (Join-Path $credentialDirectory $name) $defaults[$name] }
    Write-Warning "Existing metadata volume ${project}_schemii-metadata-postgres was found without managed credentials. Historical credentials were preserved. Back them up; legacy rotation may first require the reviewed bootstrap-owned function. The volume was not reset."
}
foreach ($name in $credentialFiles) {
    $secretPath = Join-Path $credentialDirectory $name
    if (-not (Test-Path $secretPath)) { Write-CredentialFile $secretPath (New-CredentialValue) }
    else { Protect-CredentialPath $secretPath $false }
    [void](Read-CredentialValue $secretPath $name)
}
$env:SCHEMII_CREDENTIAL_DIR = $credentialDirectory

function Invoke-MetadataPsql([string]$Container, [string]$AuthenticationPassword, [string]$Sql) {
    $localPgpass = Join-Path $credentialDirectory (".pgpass." + [Guid]::NewGuid().ToString("N"))
    try {
        [System.IO.File]::WriteAllText($localPgpass, "127.0.0.1:5432:schemii_metadata:schemii_metadata_migration:$AuthenticationPassword`n", [System.Text.UTF8Encoding]::new($false))
        Protect-CredentialPath $localPgpass $false
        & docker cp $localPgpass "${Container}:/tmp/schemii-credential-operation.pgpass" *> $null
        if ($LASTEXITCODE -ne 0) { throw "Could not stage metadata authentication." }
        & docker exec $Container sh -c 'chown postgres:postgres /tmp/schemii-credential-operation.pgpass && chmod 600 /tmp/schemii-credential-operation.pgpass' *> $null
        if ($LASTEXITCODE -ne 0) { throw "Could not protect staged metadata authentication." }
        $Sql | & docker exec -i -u postgres -e PGPASSFILE=/tmp/schemii-credential-operation.pgpass $Container psql --quiet --set ON_ERROR_STOP=1 --host 127.0.0.1 --username schemii_metadata_migration --dbname schemii_metadata *> $null
        if ($LASTEXITCODE -ne 0) { throw "Metadata authentication or credential update failed." }
    }
    finally {
        & docker exec -u postgres $Container rm -f /tmp/schemii-credential-operation.pgpass *> $null
        if (Test-Path -LiteralPath $localPgpass) { Remove-Item -LiteralPath $localPgpass -Force }
    }
}
function Test-MetadataAuthentication([string]$Container, [string]$Password) {
    try { Invoke-MetadataPsql $Container $Password "SELECT 1;"; return $true }
    catch { return $false }
}
function Wait-MetadataReady([string]$Container) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec -u postgres $Container pg_isready --quiet --host 127.0.0.1 --port 5432 --dbname schemii_metadata *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "Metadata PostgreSQL did not become ready within 30 seconds."
}
function Invoke-MetadataPasswordUpdate([string]$Container, [string]$AuthenticationPassword, [hashtable]$Values) {
    $inputText = @(
        "\prompt '' migration_password"
        $Values["metadata_migration_password"]
        "\prompt '' schemii_password"
        $Values["metadata_schemii_password"]
        "\prompt '' schemer_password"
        $Values["metadata_schemer_password"]
        "SELECT schemii_admin.rotate_metadata_passwords(:'migration_password', :'schemii_password', :'schemer_password');"
    ) -join "`n"
    Invoke-MetadataPsql $Container $AuthenticationPassword $inputText
}
function Restart-CredentialConsumers([string]$MetadataContainer) {
    & docker restart $MetadataContainer *> $null
    if ($LASTEXITCODE -ne 0) { throw "Metadata container restart failed." }
    $containers = @(& docker ps -q --filter "label=com.docker.compose.project=$project")
    foreach ($container in $containers) {
        if ($container -and $container -cne $MetadataContainer) {
            & docker restart $container *> $null
            if ($LASTEXITCODE -ne 0) { throw "Dependent container restart failed." }
        }
    }
}
function Get-TransactionValues([string]$Side) {
    $values = @{}
    foreach ($name in $credentialFiles) { $values[$name] = Read-CredentialValue (Join-Path (Join-Path $credentialTransaction $Side) $name) "$Side $name" }
    return $values
}
function Replace-TransactionValues([string]$Side) {
    $values = Get-TransactionValues $Side
    foreach ($name in $credentialFiles) { Replace-CredentialFile (Join-Path $credentialDirectory $name) $values[$name] }
}
function New-CredentialTransaction([hashtable]$NewValues) {
    $staging = Join-Path $credentialDirectory (".credential-transaction-stage." + [Guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Protect-CredentialPath $staging $true
        $oldDirectory = Join-Path $staging "old"
        $newDirectory = Join-Path $staging "new"
        New-Item -ItemType Directory -Path $oldDirectory, $newDirectory | Out-Null
        Protect-CredentialPath $oldDirectory $true
        Protect-CredentialPath $newDirectory $true
        foreach ($name in $credentialFiles) {
            Write-CredentialFile (Join-Path $oldDirectory $name) (Read-CredentialValue (Join-Path $credentialDirectory $name) $name)
            Write-CredentialFile (Join-Path $newDirectory $name) $NewValues[$name]
        }
        Write-InstanceMarker (Join-Path $staging "instance") $project
        Move-Item -LiteralPath $staging -Destination $credentialTransaction
    }
    finally { if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
}
function Undo-CredentialTransaction([string]$MetadataContainer) {
    $oldValues = Get-TransactionValues "old"
    $newValues = Get-TransactionValues "new"
    & docker start $MetadataContainer *> $null
    if ($LASTEXITCODE -ne 0) { throw "Metadata container could not start for credential recovery." }
    Wait-MetadataReady $MetadataContainer
    if (Test-MetadataAuthentication $MetadataContainer $newValues["metadata_migration_password"]) {
        Invoke-MetadataPasswordUpdate $MetadataContainer $newValues["metadata_migration_password"] $oldValues
    }
    elseif (-not (Test-MetadataAuthentication $MetadataContainer $oldValues["metadata_migration_password"])) {
        throw "Neither staged metadata credential authenticates; transaction recovery requires administrator review."
    }
    Replace-TransactionValues "old"
    Restart-CredentialConsumers $MetadataContainer
    Wait-MetadataReady $MetadataContainer
    if (-not (Test-MetadataAuthentication $MetadataContainer $oldValues["metadata_migration_password"])) { throw "Rolled-back metadata credential did not authenticate." }
    Remove-Item -LiteralPath $credentialTransaction -Recurse -Force
}
function Complete-CredentialTransaction([string]$MetadataContainer) {
    $oldValues = Get-TransactionValues "old"
    $newValues = Get-TransactionValues "new"
    Wait-MetadataReady $MetadataContainer
    Invoke-MetadataPasswordUpdate $MetadataContainer $oldValues["metadata_migration_password"] $newValues
    Replace-TransactionValues "new"
    Restart-CredentialConsumers $MetadataContainer
    Wait-MetadataReady $MetadataContainer
    if (-not (Test-MetadataAuthentication $MetadataContainer $newValues["metadata_migration_password"])) { throw "Restored metadata credential did not authenticate." }
    Remove-Item -LiteralPath $credentialTransaction -Recurse -Force
}

Get-ChildItem -LiteralPath $credentialDirectory -Directory -Filter ".credential-transaction-stage.*" | Remove-Item -Recurse -Force
if (Test-Path -LiteralPath $credentialTransaction -PathType Container) {
    if ((Read-InstanceMarker (Join-Path $credentialTransaction "instance") "Credential transaction marker") -cne $project) { throw "Credential transaction belongs to another instance; refusing recovery." }
    $recoveryContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
    if (-not $recoveryContainer) { throw "An incomplete credential transaction needs its metadata container for recovery." }
    Write-Warning "Recovering an incomplete credential transaction for $project."
    Undo-CredentialTransaction $recoveryContainer
}

if ($Mode -eq "credentials-backup") {
    if (-not $Path) { throw "credentials-backup requires -Path <directory>." }
    $backupDirectory = Join-Path ([System.IO.Path]::GetFullPath($Path)) $project
    New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
    Protect-CredentialTree $backupDirectory
    foreach ($name in @("instance") + $credentialFiles) { Copy-Item -LiteralPath (Join-Path $credentialDirectory $name) -Destination (Join-Path $backupDirectory $name); Protect-CredentialPath (Join-Path $backupDirectory $name) $false }
    Exit-CredentialLock
    Write-Host "Credential backup created at $backupDirectory. Protect it like a password vault."
    exit 0
}
if ($Mode -eq "credentials-restore") {
    if (-not $Path) { throw "credentials-restore requires -Path <directory>." }
    $sourceDirectory = [System.IO.Path]::GetFullPath($Path)
    if (Test-Path (Join-Path $sourceDirectory $project) -PathType Container) { $sourceDirectory = Join-Path $sourceDirectory $project }
    if ($runningOnWindows) { Protect-CredentialTree $sourceDirectory }
    if ((Read-InstanceMarker (Join-Path $sourceDirectory "instance") "Backup instance marker") -cne $project) { throw "Backup instance marker does not exactly match $project." }
    $restored = @{}
    foreach ($name in $credentialFiles) {
        $sourceFile = Join-Path $sourceDirectory $name
        $restored[$name] = Read-CredentialValue $sourceFile "Backup $name"
    }
    if ($legacyMetadata) {
        $metadataContainer = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
        if (-not $metadataContainer) { throw "Start the instance before restoring credentials for its existing metadata volume. No files were changed." }
        & docker start $metadataContainer *> $null
        New-CredentialTransaction $restored
        try { Complete-CredentialTransaction $metadataContainer }
        catch {
            Write-Warning "Credential restore failed; rolling back PostgreSQL, files, and containers."
            Undo-CredentialTransaction $metadataContainer
            throw
        }
    }
    else { foreach ($name in $credentialFiles) { Replace-CredentialFile (Join-Path $credentialDirectory $name) $restored[$name] } }
    Exit-CredentialLock
    Write-Host "Credentials restored for $project and dependent containers restarted."
    exit 0
}
if ($Mode -eq "credentials-rotate") {
    $metadataContainer = (& docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
    if (-not $metadataContainer) { throw "Start the instance before rotating credentials. No files were changed." }
    $newValues = @{}
    $newValues["metadata_bootstrap_password"] = Read-CredentialValue (Join-Path $credentialDirectory "metadata_bootstrap_password") "metadata_bootstrap_password"
    foreach ($name in @("metadata_migration_password", "metadata_schemii_password", "metadata_schemer_password", "opencode_password")) { $newValues[$name] = New-CredentialValue }
    New-CredentialTransaction $newValues
    try { Complete-CredentialTransaction $metadataContainer }
    catch {
        Write-Warning "Credential rotation failed; rolling back PostgreSQL, files, and containers."
        Undo-CredentialTransaction $metadataContainer
        throw
    }
    Exit-CredentialLock
    Write-Host "Credentials rotated for $project and dependent containers restarted."
    exit 0
}
}
finally {
    Exit-CredentialLock
}
if ($project -eq "schemii") {
    $defaultPort = 8080
    $defaultOpenCodePort = 4096
    $defaultMetadataPort = 5433
}
else {
    $portSha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $portHash = $portSha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($project))
        $portNumber = [BitConverter]::ToUInt32($portHash, 0)
    }
    finally {
        $portSha.Dispose()
    }
    $defaultPort = 12000 + ($portNumber % 30000)
    $defaultOpenCodePort = 42000 + ($portNumber % 20000)
    $defaultMetadataPort = 20000 + ($portNumber % 20000)
}
function Test-LocalTcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(150) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}
$currentInstance = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=schemii" | Select-Object -First 1)
$currentOpenCode = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=opencode" | Select-Object -First 1)
$currentMetadata = (& docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=metadata-postgres" | Select-Object -First 1)
if (-not $env:SCHEMII_HOST_PORT) {
    $selectedPort = $defaultPort
    if ($currentInstance) {
        $details = (& docker inspect $currentInstance | ConvertFrom-Json)[0]
        $binding = $details.HostConfig.PortBindings.'8080/tcp'
        if ($binding) {
            $selectedPort = [int]$binding[0].HostPort
        }
        else {
            $portEnvironment = $details.Config.Env | Where-Object { $_.StartsWith("SCHEMII_PORT=") } | Select-Object -First 1
            if ($portEnvironment) { $selectedPort = [int]$portEnvironment.Substring("SCHEMII_PORT=".Length) }
        }
    }
    else {
        while (Test-LocalTcpPort $selectedPort) {
            $selectedPort++
            if ($selectedPort -gt 41999) { $selectedPort = 12000 }
        }
    }
    $env:SCHEMII_HOST_PORT = [string]$selectedPort
}
if (-not $env:SCHEMII_OPENCODE_HOST_PORT) {
    $selectedOpenCodePort = $defaultOpenCodePort
    if ($currentOpenCode) {
        $openCodeDetails = (& docker inspect $currentOpenCode | ConvertFrom-Json)[0]
        $openCodeBinding = $openCodeDetails.HostConfig.PortBindings.'4096/tcp'
        if ($openCodeBinding) { $selectedOpenCodePort = [int]$openCodeBinding[0].HostPort }
    }
    else {
        while (Test-LocalTcpPort $selectedOpenCodePort) {
            $selectedOpenCodePort++
            if ($selectedOpenCodePort -gt 61999) { $selectedOpenCodePort = 42000 }
        }
    }
    $env:SCHEMII_OPENCODE_HOST_PORT = [string]$selectedOpenCodePort
}
if (-not $env:SCHEMII_METADATA_HOST_PORT) {
    $selectedMetadataPort = $defaultMetadataPort
    if ($currentMetadata) {
        $metadataDetails = (& docker inspect $currentMetadata | ConvertFrom-Json)[0]
        $metadataBinding = $metadataDetails.HostConfig.PortBindings.'5432/tcp'
        if ($metadataBinding) { $selectedMetadataPort = [int]$metadataBinding[0].HostPort }
    }
    else {
        while ((Test-LocalTcpPort $selectedMetadataPort) -or $selectedMetadataPort -eq [int]$env:SCHEMII_HOST_PORT -or $selectedMetadataPort -eq [int]$env:SCHEMII_OPENCODE_HOST_PORT) {
            $selectedMetadataPort++
            if ($selectedMetadataPort -gt 41999) { $selectedMetadataPort = 20000 }
        }
    }
    $env:SCHEMII_METADATA_HOST_PORT = [string]$selectedMetadataPort
}
if (-not $env:SCHEMII_IMAGE) { $env:SCHEMII_IMAGE = "schemii:$project" }
if (-not $env:SCHEMII_METADATA_IMAGE) { $env:SCHEMII_METADATA_IMAGE = "schemii-metadata-postgres:$project" }
if (-not $env:SCHEMII_OPENCODE_IMAGE) { $env:SCHEMII_OPENCODE_IMAGE = "schemii-opencode:1.18.15-$project" }

$composeArgs = @("compose", "--project-name", $project, "--project-directory", $scriptDirectory, "-f", (Join-Path $scriptDirectory "compose.yaml"))
switch ($Mode) {
    "docker-db" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"))
    }
    "ai" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.ai.yaml"))
    }
    "ai-local-db" {
        if (-not $IsLinux) {
            throw "ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop."
        }
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.local-db.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.local-db.yaml"))
    }
    "ai-docker-db" {
        $composeArgs += @("-f", (Join-Path $scriptDirectory "compose.postgres.yaml"), "-f", (Join-Path $scriptDirectory "compose.ai.yaml"))
    }
}
$composeFiles = $composeArgs
$upArgs = $composeArgs + @("up", "--build", "-d", "--remove-orphans")

$port = $env:SCHEMII_HOST_PORT
$url = "http://127.0.0.1:$port/"
$wasReady = $false
if (-not $NoOpen) {
    try {
        Invoke-WebRequest -Uri $url -TimeoutSec 1 -UseBasicParsing *> $null
        $wasReady = $true
    }
    catch {
        $wasReady = $false
    }
}

Write-Host "Starting Schemii instance $project in $Mode mode."
Write-Host "The first start downloads images and dependencies and may take several minutes."
& docker @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "Schemii could not be started. Review the Docker output above."
}
$containerId = (& docker @composeFiles ps -q schemii | Select-Object -First 1)
if (-not $containerId) {
    throw "Schemii did not start. Review the Docker Compose output above."
}
$containerName = (& docker inspect --format "{{.Name}}" $containerId 2>$null).TrimStart("/")
$health = ""
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $health = (& docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null)
    if ($health -eq "healthy") {
        break
    }
    if ($health -eq "unhealthy") {
        throw "Schemii failed its container health check. Run 'docker logs $containerName' for details."
    }
    Start-Sleep -Seconds 1
}
if ($health -ne "healthy") {
    throw "Schemii did not become ready within 60 seconds after the build. Run 'docker logs $containerName' for details."
}

Write-Host ""
Write-Host "Schemii is ready at $url"
Write-Host "Mode: $Mode"
Write-Host "Instance: $project"
Write-Host "Saved data remains in Docker named volumes."

if (-not $NoOpen -and -not $wasReady) {
    Start-Process $url
}
