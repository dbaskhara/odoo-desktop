# Runs the tests, then builds OdooDesktop.exe into this folder.
# Temporary build files go to %TEMP% so the project folder stays clean.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed - exe not built.' }

$work = Join-Path $env:TEMP 'OdooDesktop-build'
python -m PyInstaller OdooDesktop.spec --noconfirm --clean --workpath "$work\work" --distpath "$work\dist"
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

try {
    Copy-Item "$work\dist\OdooDesktop.exe" (Join-Path $PSScriptRoot 'OdooDesktop.exe') -Force
} catch {
    throw "Built, but OdooDesktop.exe is open. Close the app and run: Copy-Item '$work\dist\OdooDesktop.exe' '$PSScriptRoot\OdooDesktop.exe' -Force"
}
Remove-Item $work -Recurse -Force
Get-ChildItem -Path $PSScriptRoot -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Write-Host 'Done: OdooDesktop.exe is ready in this folder.'
