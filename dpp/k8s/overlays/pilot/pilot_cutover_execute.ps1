# pilot_cutover_execute.ps1
# PowerShell wrapper for pilot_cutover_execute.sh
#
# Usage (from repo root or from this directory):
#   .\k8s\overlays\pilot\pilot_cutover_execute.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ShScript  = Join-Path $ScriptDir "pilot_cutover_execute.sh"
$BashScript = & cygpath -u $ShScript 2>$null
if (-not $BashScript) { $BashScript = $ShScript -replace '\\','/' }

Write-Host "Running: bash $BashScript"
bash $BashScript
