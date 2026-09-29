# Package a SILA release zip for GitHub (Windows x64).
#
#   powershell -ExecutionPolicy Bypass -File vst\release\make-release.ps1 [-Version 1.0.0] [-SkipBuild]
#
# Produces  vst\release\out\SILA-v<version>-win64.zip  containing:
#   SILA.vst3\            the VST3 bundle (copy to C:\Program Files\Common Files\VST3)
#   SILA.exe              the Standalone app
#   install-vst3.cmd      copies the bundle into the system VST3 folder (asks for admin)
#   README.txt            install + first-steps notes for people who download it
#
# The factory sample pack is embedded in the binaries (it installs itself to
# ~\SILA\library on first run), so nothing else needs shipping.

param(
    [string] $Version = "1.0.0",
    [switch] $SkipBuild
)

$ErrorActionPreference = "Stop"
$root  = Resolve-Path (Join-Path $PSScriptRoot "..")          # vst\
$build = Join-Path $root "build"
$cmake = "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }             # fall back to PATH on other boxes

if (-not $SkipBuild) {
    if (Get-Process SILA -ErrorAction SilentlyContinue) { throw "SILA.exe is running - close it first (it holds the link output)." }
    Write-Host "Building Release Standalone + VST3..."
    & $cmake --build $build --target SILA_Standalone --target SILA_VST3 --config Release
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}

$vst3 = Join-Path $build "SILA_artefacts\Release\VST3\SILA.vst3"
$exe  = Join-Path $build "SILA_artefacts\Release\Standalone\SILA.exe"
foreach ($f in @($vst3, $exe)) { if (-not (Test-Path $f)) { throw "missing build output: $f" } }

$out   = Join-Path $PSScriptRoot "out"
$stage = Join-Path $out "SILA-v$Version-win64"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

Copy-Item $vst3 (Join-Path $stage "SILA.vst3") -Recurse
Copy-Item $exe  $stage
Copy-Item (Join-Path $PSScriptRoot "install-vst3.cmd") $stage
Copy-Item (Join-Path $PSScriptRoot "README.txt") $stage
$license = Join-Path $root "..\LICENSE"
if (Test-Path $license) { Copy-Item $license $stage } else { Write-Warning "no LICENSE file in the repo root - the zip ships without one" }

$zip = "$stage.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -CompressionLevel Optimal

$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
Set-Content -Path "$zip.sha256" -Value "$hash  $(Split-Path $zip -Leaf)" -Encoding ascii
Write-Host ""
Write-Host "Release zip: $zip"
Write-Host "SHA-256:     $hash"
Write-Host ""
Write-Host "Publish with:  gh release create v$Version `"$zip`" `"$zip.sha256`" --title `"SILA v$Version`" --notes-file <notes.md>"
