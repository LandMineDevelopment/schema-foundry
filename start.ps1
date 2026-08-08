param(
    [ValidateSet("ui", "docker-db")]
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

$composeArgs = @("compose")
if ($Mode -eq "docker-db") {
    $composeArgs += @("-f", "compose.yaml", "-f", "compose.postgres.yaml")
}
$composeArgs += @("up", "--build", "-d", "--remove-orphans")

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Schema Foundry could not be started. Review the Docker output above."
}

$port = if ($env:SCHEMA_FOUNDRY_HOST_PORT) { $env:SCHEMA_FOUNDRY_HOST_PORT } else { "8080" }
$url = "http://127.0.0.1:$port/"
Write-Host ""
Write-Host "Schema Foundry is ready at $url"
Write-Host "Mode: $Mode"
Write-Host "Saved data remains in Docker named volumes."

if (-not $NoOpen) {
    Start-Process $url
}
