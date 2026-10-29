# Creates a Windows desktop shortcut to the built NordScribe.exe.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File packaging\Create-Shortcut.ps1
#
# The shortcut uses the EXE icon, which is the multi-size logo embedded by
# packaging/NordScribe.spec. Pass -ShortcutPath to put it elsewhere.

param(
    [string]$ExePath = (Join-Path $PSScriptRoot "..\dist\NordScribe.exe"),
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "NordScribe.lnk")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "NordScribe.exe not found: $ExePath. Build it first: pyinstaller --noconfirm --clean packaging\NordScribe.spec"
}

$exe = (Resolve-Path -LiteralPath $ExePath).Path
$shortcutDir = Split-Path -Parent $ShortcutPath
if ($shortcutDir -and -not (Test-Path -LiteralPath $shortcutDir)) {
    New-Item -ItemType Directory -Path $shortcutDir -Force | Out-Null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path -Parent $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = "NordScribe - local audio transcription"
$shortcut.Save()

Write-Host "Shortcut created: $ShortcutPath"
