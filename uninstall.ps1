param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$runningOnWindows = $env:OS -eq "Windows_NT"
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

$credentialRoot = if ($env:SCHEMII_CREDENTIAL_ROOT) { $env:SCHEMII_CREDENTIAL_ROOT } elseif ($runningOnWindows) { Join-Path $env:LOCALAPPDATA "Schemii\credentials" } else { Join-Path $HOME ".local/share/schemii/credentials" }
$volumeSuffixes = @(
    "schemii-config", "schemii-schemas", "schemii-postgres", "schemii-metadata-postgres",
    "schemii-opencode-data", "schemii-opencode-config", "schemii-opencode-state", "schemii-opencode-cache",
    "schemer-dashboards"
)
$approvedProjects = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$orphanVolumeCounts = @{}
$orphanVolumesSeen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$ownedImages = @{}
$allContainerIds = @(docker ps -aq | Where-Object { $_ })
$allVolumes = @(docker volume ls -q | Where-Object { $_ })

function Test-ProjectName([string]$Project) { return $Project -cmatch '^[a-z0-9][a-z0-9_-]*$' }
function Test-RepositoryWorkingDirectory([string]$WorkingDirectory) {
    if (-not $WorkingDirectory) { return $false }
    try { $resolved = [System.IO.Path]::GetFullPath($WorkingDirectory) }
    catch { return $false }
    $comparison = if ($runningOnWindows) { [System.StringComparison]::OrdinalIgnoreCase } else { [System.StringComparison]::Ordinal }
    return [string]::Equals($resolved, $repoDirectory, $comparison)
}
function Get-CredentialDirectory([string]$Project) {
    if ($env:SCHEMII_CREDENTIAL_DIR) { return $env:SCHEMII_CREDENTIAL_DIR }
    return Join-Path $credentialRoot $Project
}
function Test-CredentialMarker([string]$Project) {
    $instanceFile = Join-Path (Get-CredentialDirectory $Project) "instance"
    if (-not (Test-Path $instanceFile -PathType Leaf)) { return $false }
    $raw = [System.IO.File]::ReadAllText($instanceFile, [System.Text.Encoding]::UTF8)
    return $raw -cmatch ("\A" + [regex]::Escape($Project) + "(?:\r?\n)?\z")
}
function Invoke-DockerRemoval([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker removal failed: docker $($DockerArguments -join ' ')" }
}

foreach ($containerId in $allContainerIds) {
    $labels = (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' $containerId 2>$null) -split '\|', 3
    if ($labels.Count -eq 3 -and (Test-ProjectName $labels[0]) -and $labels[1] -cin @("schemii", "schemer") -and (Test-RepositoryWorkingDirectory $labels[2])) {
        [void]$approvedProjects.Add($labels[0])
    }
}
foreach ($volume in $allVolumes) {
    $labels = (& docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' $volume 2>$null) -split '\|', 2
    if ($labels.Count -ne 2) { continue }
    $project, $logicalName = $labels
    $key = "${project}:$logicalName"
    if ((Test-ProjectName $project) -and $volumeSuffixes -ccontains $logicalName -and $volume -ceq "${project}_$logicalName" -and $orphanVolumesSeen.Add($key)) {
        $orphanVolumeCounts[$project] = 1 + [int]$orphanVolumeCounts[$project]
    }
}
foreach ($project in @($orphanVolumeCounts.Keys)) {
    if ($orphanVolumeCounts[$project] -ge 2 -or (Test-CredentialMarker $project)) { [void]$approvedProjects.Add($project) }
}

Write-Host "This permanently removes:"
Write-Host "  - every verified Schemii Docker container and network"
Write-Host "  - all verified Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats"
Write-Host "  - safely attributable project-scoped Schemii images"
Write-Host "  - each verified instance credential directory"
Write-Host "  - repository: $repoDirectory"
if ($approvedProjects.Count) {
    Write-Host "Detected Schemii instances:"
    $approvedProjects | Sort-Object | ForEach-Object { Write-Host "  - $_" }
}
else { Write-Host "Detected Schemii instances: none" }
Write-Host "Unrelated or ambiguously owned Docker projects, images, and volumes are not removed."

if (-not $Yes) {
    $confirmation = Read-Host "Type UNINSTALL to continue"
    if ($confirmation -cne "UNINSTALL") {
        Write-Host "Uninstall cancelled. Nothing was removed."
        exit 1
    }
}

foreach ($project in @($approvedProjects | Sort-Object)) {
    $ownedContainerIds = @()
    foreach ($containerId in $allContainerIds) {
        $details = (& docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{.Image}}|{{.Config.Image}}' $containerId 2>$null) -split '\|', 4
        if ($details.Count -ne 4 -or $details[0] -cne $project -or -not (Test-RepositoryWorkingDirectory $details[1])) { continue }
        $ownedContainerIds += $containerId
        if ($details[3] -cin @("schemii:$project", "schemii-metadata-postgres:$project", "schemii-opencode:1.18.15-$project")) {
            $ownedImages[$details[3]] = $details[2]
        }
    }
    if ($ownedContainerIds.Count) { Invoke-DockerRemoval (@("rm", "-f") + $ownedContainerIds) }

    foreach ($networkId in @(docker network ls -q --filter "label=com.docker.compose.project=$project" | Where-Object { $_ })) {
        $labels = (& docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.network" }}|{{.Name}}' $networkId 2>$null) -split '\|', 3
        if ($labels.Count -eq 3 -and $labels[0] -ceq $project -and $labels[1] -ceq "default" -and $labels[2] -ceq "${project}_default") {
            Invoke-DockerRemoval @("network", "rm", $networkId)
        }
    }
    foreach ($volume in $allVolumes) {
        $labels = (& docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}|{{.Name}}' $volume 2>$null) -split '\|', 3
        if ($labels.Count -eq 3 -and $labels[0] -ceq $project -and $volumeSuffixes -ccontains $labels[1] -and $labels[2] -ceq "${project}_$($labels[1])" -and $volume -ceq $labels[2]) {
            Invoke-DockerRemoval @("volume", "rm", $volume)
        }
    }
    if (Test-CredentialMarker $project) { Remove-Item -LiteralPath (Get-CredentialDirectory $project) -Recurse -Force }
}

foreach ($imageReference in @($ownedImages.Keys)) {
    $imageId = $ownedImages[$imageReference]
    $currentId = & docker image inspect --format '{{.Id}}' $imageReference 2>$null
    $inspectSucceeded = $LASTEXITCODE -eq 0
    $imageUsers = @(docker ps -aq --filter "ancestor=$imageId" | Where-Object { $_ })
    $referenceCheckSucceeded = $LASTEXITCODE -eq 0
    if ($imageId -and $inspectSucceeded -and $referenceCheckSucceeded -and $currentId -ceq $imageId -and -not $imageUsers.Count) {
        Invoke-DockerRemoval @("image", "rm", $imageReference)
    }
}

$repoParent = Split-Path -Parent $repoDirectory
$repoName = Split-Path -Leaf $repoDirectory
Write-Host "Verified Docker resources removed. Removing repository $repoDirectory"
Set-Location $repoParent
Remove-Item -LiteralPath (Join-Path $repoParent $repoName) -Recurse -Force
Write-Host "Schemii has been uninstalled."
