param(
  [string]$Version = "",
  [string]$PrivateKeyPath = ".tauri/updater.key",
  [string]$OutputDir = "release",
  [string]$Repo = "way9999/scipilot",
  [string]$NotesFile = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if ([string]::IsNullOrWhiteSpace($Version)) {
  $packageJson = Get-Content -Path (Join-Path $projectRoot "package.json") -Raw | ConvertFrom-Json
  $Version = [string]$packageJson.version
}

$plainVersion = if ($Version.StartsWith("v")) { $Version.Substring(1) } else { $Version }
$tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }

$resolvedKeyPath = Join-Path $projectRoot $PrivateKeyPath
if (!(Test-Path $resolvedKeyPath)) {
  throw "Updater private key not found: $resolvedKeyPath"
}

if (-not $SkipBuild) {
  $env:TAURI_SIGNING_PRIVATE_KEY = $resolvedKeyPath
  if ($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -eq $null) {
    Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
  }

  Write-Host "Building SciPilot $plainVersion with updater signing..."
  pnpm tauri build
  if ($LASTEXITCODE -ne 0) {
    throw "pnpm tauri build failed with exit code $LASTEXITCODE"
  }
}

$bundleRoot = Join-Path $projectRoot "src-tauri/target/release/bundle"
$releaseRoot = Join-Path $projectRoot $OutputDir
$versionRoot = Join-Path $releaseRoot $plainVersion

if (Test-Path $versionRoot) {
  Remove-Item -LiteralPath $versionRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $versionRoot | Out-Null

$msiName = "SciPilot_${plainVersion}_x64_en-US.msi"
$msiSigName = "${msiName}.sig"
$nsisName = "SciPilot_${plainVersion}_x64-setup.exe"
$nsisSigName = "${nsisName}.sig"

$msiPath = Join-Path $bundleRoot "msi/$msiName"
$msiSigPath = Join-Path $bundleRoot "msi/$msiSigName"
$nsisPath = Join-Path $bundleRoot "nsis/$nsisName"
$nsisSigPath = Join-Path $bundleRoot "nsis/$nsisSigName"

foreach ($requiredPath in @($msiPath, $msiSigPath, $nsisPath, $nsisSigPath)) {
  if (!(Test-Path $requiredPath)) {
    throw "Required updater artifact not found: $requiredPath"
  }
}

$notes = "SciPilot $plainVersion"
if ($NotesFile) {
  $resolvedNotesPath = Resolve-Path $NotesFile -ErrorAction Stop
  $notes = (Get-Content -Path $resolvedNotesPath -Raw).Trim()
}

function Read-Signature([string]$Path) {
  return ((Get-Content -Path $Path -Raw) -replace "`r", "" -replace "`n", "").Trim()
}

$releaseBaseUrl = "https://github.com/$Repo/releases/download/$tag"
$manifest = [ordered]@{
  version = $plainVersion
  notes = $notes
  pub_date = (Get-Date).ToUniversalTime().ToString("o")
  platforms = [ordered]@{
    "windows-x86_64" = [ordered]@{
      signature = Read-Signature $msiSigPath
      url = "$releaseBaseUrl/$msiName"
    }
    "windows-x86_64-msi" = [ordered]@{
      signature = Read-Signature $msiSigPath
      url = "$releaseBaseUrl/$msiName"
    }
    "windows-x86_64-nsis" = [ordered]@{
      signature = Read-Signature $nsisSigPath
      url = "$releaseBaseUrl/$nsisName"
    }
  }
}

$manifestPath = Join-Path $versionRoot "latest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding UTF8

$artifactPatterns = @(
  "msi/*.msi",
  "nsis/*.exe",
  "nsis/*.sig",
  "msi/*.sig"
)

foreach ($pattern in $artifactPatterns) {
  Get-ChildItem -Path (Join-Path $bundleRoot $pattern) -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Name -like "*$plainVersion*") {
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $versionRoot $_.Name) -Force
    }
  }
}

Write-Host "Release artifacts copied to $versionRoot"
Get-ChildItem -Path $versionRoot | Select-Object Name, Length
