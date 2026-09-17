# Forward to root master launcher
$masterScript = Join-Path (Split-Path -Parent $PSScriptRoot) "START_ALL_SERVICES.ps1"
& powershell -NoProfile -ExecutionPolicy Bypass -File $masterScript
