# create_supabase_ca_configmap.ps1
# PowerShell wrapper for create_supabase_ca_configmap.sh
#
# Usage (from repo root or from this directory):
#   .\k8s\overlays\pilot\create_supabase_ca_configmap.ps1 C:\path\to\supabase-ca.crt
param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$CertPath
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ShScript  = Join-Path $ScriptDir "create_supabase_ca_configmap.sh"

# cygpath으로 bash용 경로로 변환 (git bash 환경)
$BashCert  = & cygpath -u $CertPath 2>$null
if (-not $BashCert) { $BashCert = $CertPath -replace '\\','/' }

Write-Host "Running: bash $ShScript $BashCert"
bash $ShScript $BashCert
