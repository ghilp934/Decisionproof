param(
    [Parameter(Mandatory=$true)]
    [string]$NewSecret
)

$env:AWS_PROFILE = "dpp-admin"
Write-Host "AWS_PROFILE: $env:AWS_PROFILE"
Write-Host "New secret length: $($NewSecret.Length) bytes"

# 기존 시크릿에서 나머지 2개 키 값 읽기
Write-Host "Reading existing secret..."
$existingData = kubectl -n dpp-pilot get secret dpp-demo-secrets -o jsonpath='{.data}' 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: could not get existing secret"
    Write-Host $existingData
    exit 1
}

$parsed = $existingData | ConvertFrom-Json
$TOKEN_B64  = $parsed.'DP_DEMO_SHARED_TOKEN'
$SALT_B64   = $parsed.'DEMO_ACTOR_KEY_SALT'
$PROXY_B64  = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($NewSecret))

Write-Host "DP_DEMO_SHARED_TOKEN length (b64): $($TOKEN_B64.Length)"
Write-Host "DEMO_ACTOR_KEY_SALT length (b64): $($SALT_B64.Length)"
Write-Host "RAPIDAPI_PROXY_SECRET new b64 length: $($PROXY_B64.Length)"

# 시크릿 재생성 (3키 모두 포함)
Write-Host "Recreating secret..."
kubectl -n dpp-pilot create secret generic dpp-demo-secrets `
    --from-literal="RAPIDAPI_PROXY_SECRET=$NewSecret" `
    --from-literal="DP_DEMO_SHARED_TOKEN=$(
        [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TOKEN_B64))
    )" `
    --from-literal="DEMO_ACTOR_KEY_SALT=$(
        [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($SALT_B64))
    )" `
    --dry-run=client -o yaml | kubectl apply -f -

if ($LASTEXITCODE -eq 0) {
    Write-Host "Secret updated. Restarting rollout..."
    kubectl -n dpp-pilot rollout restart deployment/dpp-api
    kubectl -n dpp-pilot rollout status deployment/dpp-api --timeout=120s
    Write-Host "Done."
} else {
    Write-Host "FAIL: secret update failed"
}
