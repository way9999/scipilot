param(
  [Parameter(Mandatory = $true)]
  [string]$Repo,
  [string]$Version = "",
  [string]$ArtifactsDir = "release",
  [string]$NotesFile = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Invoke-Gh {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Args
  )

  & gh @Args
  if ($LASTEXITCODE -ne 0) {
    throw "gh $($Args -join ' ') failed with exit code $LASTEXITCODE"
  }
}

Invoke-Gh -Args @("auth", "status")

if ([string]::IsNullOrWhiteSpace($Version)) {
  $packageJson = Get-Content -Path (Join-Path $projectRoot "package.json") -Raw | ConvertFrom-Json
  $Version = [string]$packageJson.version
}

$tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }
$artifactRoot = Join-Path $projectRoot $ArtifactsDir
$versionRoot = Join-Path $artifactRoot $Version

if (!(Test-Path $versionRoot)) {
  throw "Artifact directory not found: $versionRoot"
}

$assetPaths = Get-ChildItem -Path $versionRoot -File | ForEach-Object { $_.FullName }
if ($assetPaths.Count -eq 0) {
  throw "No artifacts found in $versionRoot"
}

$releaseArgs = @("release", "view", $tag, "-R", $Repo)
$releaseExists = $false
try {
  Invoke-Gh -Args $releaseArgs | Out-Null
  $releaseExists = $true
} catch {
  $releaseExists = $false
}

if (-not $releaseExists) {
  $createArgs = @("release", "create", $tag, "-R", $Repo, "--title", "SciPilot $Version")
  if ($NotesFile) {
    $createArgs += @("--notes-file", (Resolve-Path $NotesFile).Path)
  } else {
    $createArgs += @("--notes", "SciPilot $Version")
  }
  Invoke-Gh -Args $createArgs
}

$uploadArgs = @("release", "upload", $tag, "-R", $Repo, "--clobber")
$uploadArgs += $assetPaths
Invoke-Gh -Args $uploadArgs

Write-Host "Uploaded release assets for $tag to $Repo"
