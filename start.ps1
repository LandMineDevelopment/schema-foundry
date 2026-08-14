param(
    [ValidateSet("ui", "docker-db", "ai", "ai-local-db", "ai-docker-db")]
    [string]$Mode = "ai-docker-db",
    [switch]$NoOpen,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

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
if ($Mode.StartsWith("ai") -and -not $env:SCHEMII_OPENCODE_PASSWORD) {
    $env:SCHEMII_OPENCODE_PASSWORD = (& docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $env:SCHEMII_OPENCODE_PASSWORD) {
        throw "Schemii could not generate its internal AI credential with Docker."
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
