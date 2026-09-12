<#
  build.ps1 - GCalCtrlCli.exe を PyInstaller でビルドする
  Usage: .\build.ps1
#>
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# venv の作成とパッケージインストール
$Venv = Join-Path $Root ".venv"
if (-not (Test-Path $Venv)) {
    python -m venv $Venv
}
& "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install --quiet pyinstaller

# ビルド
& "$Venv\Scripts\python.exe" -m PyInstaller `
    --onefile `
    --name GCalCtrlCli `
    --console `
    --distpath (Join-Path $Root "dist") `
    (Join-Path $Root "gcal_ctrl\cli.py")

$Exe = Join-Path $Root "dist\GCalCtrlCli.exe"
if (Test-Path $Exe) {
    Write-Host "Build succeeded: $Exe"
} else {
    throw "Build failed: GCalCtrlCli.exe not found."
}
