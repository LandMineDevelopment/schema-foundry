param(
    [ValidateSet("ui", "docker-db", "ai", "ai-local-db", "ai-docker-db")]
    [string]$Mode = "ui",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required. Install and start Docker Desktop, then try again."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but not running. Start it and try again."
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose is unavailable. Update Docker Desktop and try again."
}

$composeArgs = @("compose", "-f", "compose.yaml")
switch ($Mode) {
    "docker-db" {
        $composeArgs += @("-f", "compose.postgres.yaml")
    }
    "ai" {
        $composeArgs += @("-f", "compose.ai.yaml")
    }
    "ai-local-db" {
        if (-not $IsLinux) {
            throw "ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop."
        }
        $composeArgs += @("-f", "compose.local-db.yaml", "-f", "compose.ai.yaml", "-f", "compose.ai.local-db.yaml")
    }
    "ai-docker-db" {
        $composeArgs += @("-f", "compose.postgres.yaml", "-f", "compose.ai.yaml")
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

$port = if ($env:SCHEMII_HOST_PORT) { $env:SCHEMII_HOST_PORT } else { "8080" }
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

& docker @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "Schemii could not be started. Review the Docker output above."
}
$containerId = (& docker @composeFiles ps -q schemii | Select-Object -First 1)
if (-not $containerId) {
    throw "Schemii did not start. Review the Docker Compose output above."
}
$health = ""
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $health = (& docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null)
    if ($health -eq "healthy") {
        break
    }
    if ($health -eq "unhealthy") {
        throw "Schemii failed its container health check. Run docker compose logs schemii for details."
    }
    Start-Sleep -Seconds 1
}
if ($health -ne "healthy") {
    throw "Schemii did not become ready within 60 seconds. Run docker compose logs schemii for details."
}

Write-Host ""
Write-Host "Schemii is ready at $url"
Write-Host "Mode: $Mode"
Write-Host "Saved data remains in Docker named volumes."

if (-not $NoOpen -and -not $wasReady) {
    Start-Process $url
}
