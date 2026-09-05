[CmdletBinding()]
param(
    [string]$Python = "python",
    [Parameter(Mandatory = $true)][string]$FFmpegPath,
    [Parameter(Mandatory = $true)][string]$FFprobePath,
    [Parameter(Mandatory = $true)][string]$FFmpegLicense
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Spec = Join-Path $ProjectRoot "packaging\DJDMaker.spec"
$DistRoot = Join-Path $ProjectRoot "dist"
$WorkRoot = Join-Path $ProjectRoot "build\pyinstaller"
$ReleaseRoot = Join-Path $DistRoot "DJDmaker_v0.1.1"
$AssetRoot = Join-Path $ProjectRoot "build\packaging-assets"

$env:DJD_FFMPEG_PATH = (Resolve-Path -LiteralPath $FFmpegPath).Path
$env:DJD_FFPROBE_PATH = (Resolve-Path -LiteralPath $FFprobePath).Path
$ResolvedLicense = (Resolve-Path -LiteralPath $FFmpegLicense).Path
New-Item -ItemType Directory -Force -Path $AssetRoot | Out-Null
$StagedLicense = Join-Path $AssetRoot "FFmpeg-LICENSE.txt"
if ($ResolvedLicense -ne $StagedLicense) {
    Copy-Item -LiteralPath $ResolvedLicense -Destination $StagedLicense -Force
}
$env:DJD_FFMPEG_LICENSE = $StagedLicense

& $Python -m djd_maker.packaging.preflight --root $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Packaging preflight failed." }

& $Python -m PyInstaller --noconfirm --clean --workpath $WorkRoot --distpath $DistRoot $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }

# PyInstaller's contents_directory keeps collected files in _internal.  These
# reviewed portable assets deliberately live beside the EXE so application_root
# and subprocess discovery do not depend on the private bundle directory.
foreach ($Asset in @("config", "licenses", "runtime")) {
    $Source = Join-Path $ReleaseRoot "_internal\$Asset"
    $Destination = Join-Path $ReleaseRoot $Asset
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Expected collected asset not found: $Source"
    }
    if (Test-Path -LiteralPath $Destination) {
        throw "Refusing to overwrite existing portable asset: $Destination"
    }
    Move-Item -LiteralPath $Source -Destination $Destination
}

$WritableDirectories = @(
    "input", "raw_files", "output", "work", "system", "system\jobs",
    "logs", "browser", "browser\chrome-profile"
)
foreach ($Relative in $WritableDirectories) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ReleaseRoot $Relative) | Out-Null
}

& $Python -m djd_maker.packaging.preflight --root $ReleaseRoot --release-tree
if ($LASTEXITCODE -ne 0) { throw "Built portable tree failed validation." }

Write-Host "Validated onedir build: $ReleaseRoot"
Write-Host "No release ZIP was created. Perform release approval separately."
