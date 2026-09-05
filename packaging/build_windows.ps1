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
$ReleaseRoot = Join-Path $DistRoot "DJDmaker_v0.1"

$env:DJD_FFMPEG_PATH = (Resolve-Path -LiteralPath $FFmpegPath).Path
$env:DJD_FFPROBE_PATH = (Resolve-Path -LiteralPath $FFprobePath).Path
$env:DJD_FFMPEG_LICENSE = (Resolve-Path -LiteralPath $FFmpegLicense).Path

& $Python -m djd_maker.packaging.preflight --root $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Packaging preflight failed." }

& $Python -m PyInstaller --noconfirm --clean --workpath $WorkRoot --distpath $DistRoot $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }

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
