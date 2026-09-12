<#
  build.ps1 - GCalCtrlCli.exe を PyInstaller でビルドする
  Usage: .\build.ps1
#>
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# venv の作成とパッケージインストール
$Venv = Join-Path $Root ".venv"
if (-not (Test-Path $Venv)) {
    py -3.13 -m venv $Venv
}
& "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install --quiet pyinstaller

# ビルド
$DistPath = "$Root\dist"
$WorkPath = "$Root\build"
$EntryPy = "$Root\gcal_ctrl\cli.py"

Push-Location $Root
try {
    & "$Venv\Scripts\pyinstaller.exe" --onefile --name GCalCtrlCli --console --distpath "dist" --workpath "build" --specpath "." -y "gcal_ctrl\cli.py"
} finally {
    Pop-Location
}

$Exe = Join-Path $Root "dist\GCalCtrlCli.exe"
if (Test-Path $Exe) {
    Write-Host "Build succeeded: $Exe"
} else {
    throw "Build failed: GCalCtrlCli.exe not found."
}
