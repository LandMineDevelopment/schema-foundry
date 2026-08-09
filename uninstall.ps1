param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$repoDirectory = (Resolve-Path $PSScriptRoot).Path
$homeDirectory = [System.IO.Path]::GetFullPath($HOME)
if (
    $repoDirectory -eq [System.IO.Path]::GetPathRoot($repoDirectory) -or
    $repoDirectory -eq $homeDirectory -or
    -not (Test-Path (Join-Path $repoDirectory "compose.yaml") -PathType Leaf) -or
    -not (Test-Path (Join-Path $repoDirectory "start.ps1") -PathType Leaf) -or
    -not (Test-Path (Join-Path $repoDirectory "src/schemii") -PathType Container)
) {
    throw "Refusing to remove $repoDirectory because it is not a recognized Schemii repository."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install or restore Docker first so Schemii containers and volumes can be removed safely."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is unavailable or access was denied. Start Docker and run 'docker info' before uninstalling Schemii."
}

$projects = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$volumes = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$images = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
docker ps -a --filter "label=com.docker.compose.service=schemii" --format '{{.Label "com.docker.compose.project"}}' |
    ForEach-Object { if ($_ -cmatch '^[a-z0-9][a-z0-9_-]*$') { [void]$projects.Add($_) } }

$volumeSuffixes = @(
    "schemii-config", "schemii-schemas", "schemii-postgres",
    "schemii-opencode-data", "schemii-opencode-config", "schemii-opencode-state", "schemii-opencode-cache"
)
docker volume ls --format '{{.Name}}' | ForEach-Object {
    $volume = $_
    [void]$volumes.Add($volume)
    foreach ($suffix in $volumeSuffixes) {
        $ending = "_$suffix"
        if ($volume.EndsWith($ending, [System.StringComparison]::Ordinal)) {
            $project = $volume.Substring(0, $volume.Length - $ending.Length)
            if ($project -cmatch '^[a-z0-9][a-z0-9_-]*$') { [void]$projects.Add($project) }
            break
        }
    }
}
docker image ls --format '{{.Repository}}:{{.Tag}}' | ForEach-Object { [void]$images.Add($_) }

function Invoke-DockerRemoval([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker removal failed: docker $($DockerArguments -join ' ')"
    }
}

Write-Host "This permanently removes:"
Write-Host "  - every detected Schemii Docker container and network"
Write-Host "  - all detected Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats"
Write-Host "  - Schemii-built images"
Write-Host "  - repository: $repoDirectory"
if ($projects.Count) {
    Write-Host "Detected Schemii instances:"
    $projects | Sort-Object | ForEach-Object { Write-Host "  - $_" }
}
else {
    Write-Host "Detected Schemii instances: none"
}
Write-Host "Unrelated Docker projects, images, and volumes are not removed."

if (-not $Yes) {
    $confirmation = Read-Host "Type UNINSTALL to continue"
    if ($confirmation -cne "UNINSTALL") {
        Write-Host "Uninstall cancelled. Nothing was removed."
        exit 1
    }
}

foreach ($project in $projects) {
    $containerIds = @(docker ps -aq --filter "label=com.docker.compose.project=$project")
    if ($containerIds.Count) { Invoke-DockerRemoval (@("rm", "-f") + $containerIds) }
    $networkIds = @(docker network ls -q --filter "label=com.docker.compose.project=$project")
    if ($networkIds.Count) { Invoke-DockerRemoval (@("network", "rm") + $networkIds) }
    foreach ($suffix in $volumeSuffixes) {
        $volume = "${project}_$suffix"
        if ($volumes.Contains($volume)) { Invoke-DockerRemoval @("volume", "rm", $volume) }
    }
    foreach ($image in @("schemii:$project", "schemii-opencode:1.18.15-$project")) {
        if ($images.Contains($image)) { Invoke-DockerRemoval @("image", "rm", $image) }
    }
}
foreach ($image in @("schemii:local", "schemii-opencode:1.18.15-local")) {
    if ($images.Contains($image)) { Invoke-DockerRemoval @("image", "rm", $image) }
}

$repoParent = Split-Path -Parent $repoDirectory
$repoName = Split-Path -Leaf $repoDirectory
Write-Host "Docker resources removed. Removing repository $repoDirectory"
Set-Location $repoParent
Remove-Item -LiteralPath (Join-Path $repoParent $repoName) -Recurse -Force
Write-Host "Schemii has been uninstalled."
