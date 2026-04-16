# create_pilot_secret.ps1
# pilot_secret_values.env → AWS Secrets Manager 업로드 (PowerShell 버전)
# 실행: .\k8s\overlays\pilot\create_pilot_secret.ps1
#
# bash create_pilot_secret.sh 와 동일한 동작:
#   - decisionproof/pilot/dpp-secrets 에 put-secret-value (이미 있으면) or create-secret
#   - Phase 4: Toss 키는 포함하지 않음 (dormant)

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile    = Join-Path $ScriptDir "pilot_secret_values.env"
$SecretName = "decisionproof/pilot/dpp-secrets"
$Region     = "ap-northeast-2"
$Profile    = "dpp-admin"

# ── 1. .env 파일 파싱 ──────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) {
    Write-Error "FAIL: $EnvFile not found"
    exit 1
}

$vars = @{}
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    # 빈 줄 및 주석 건너뜀
    if ($line -match '^\s*$' -or $line -match '^\s*#') { continue }
    # KEY=VALUE 또는 KEY="VALUE" 파싱
    if ($line -match '^([A-Z_][A-Z0-9_]*)=(.*)$') {
        $key = $Matches[1]
        $val = $Matches[2].Trim()
        # 양쪽 따옴표 제거 (있는 경우)
        if ($val -match '^"(.*)"$') { $val = $Matches[1] }
        $vars[$key] = $val
    }
}

# ── 2. 필수 키 존재 확인 ───────────────────────────────────────────
$required = @(
    "DATABASE_URL", "REDIS_URL", "REDIS_PASSWORD",
    "SENTRY_DSN", "SUPABASE_URL", "SB_PUBLISHABLE_KEY", "SB_SECRET_KEY",
    "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID",
    "KS_AUDIT_FINGERPRINT_PEPPER_B64"
)
$missing = $required | Where-Object { -not $vars.ContainsKey($_) }
if ($missing) {
    Write-Error "FAIL: 다음 키가 $EnvFile 에 없습니다: $($missing -join ', ')"
    exit 1
}

$unfilled = $required | Where-Object { $vars[$_] -eq "placeholder" -or $vars[$_] -eq "FILL_IN" }
if ($unfilled) {
    Write-Error "FAIL: 아직 placeholder 상태인 키: $($unfilled -join ', ')`n  실제 값으로 채운 뒤 재실행하세요."
    exit 1
}

# ── 3. JSON 빌드 (create_pilot_secret.sh 와 동일한 키 이름) ──────────
$secret = [ordered]@{
    "database-url"                    = $vars["DATABASE_URL"]
    "redis-url"                       = $vars["REDIS_URL"]
    "redis-password"                  = $vars["REDIS_PASSWORD"]
    "sentry-dsn"                      = $vars["SENTRY_DSN"]
    "supabase-url"                    = $vars["SUPABASE_URL"]
    "sb-publishable-key"              = $vars["SB_PUBLISHABLE_KEY"]
    "sb-secret-key"                   = $vars["SB_SECRET_KEY"]
    "paypal_client_id"                = $vars["PAYPAL_CLIENT_ID"]
    "paypal_client_secret"            = $vars["PAYPAL_CLIENT_SECRET"]
    "paypal_webhook_id"               = $vars["PAYPAL_WEBHOOK_ID"]
    "ks_audit_fingerprint_pepper_b64" = $vars["KS_AUDIT_FINGERPRINT_PEPPER_B64"]
}

$secretJson = $secret | ConvertTo-Json -Compress -Depth 2

# ── 4. 임시 파일에 저장 ────────────────────────────────────────────
# $env:TEMP 사용: 한글/공백 없는 단순 경로 → file:// 파싱 안전
# UTF-8 without BOM: [System.Text.Encoding]::UTF8 은 BOM 포함 → AWS CLI 거부
# New-Object UTF8Encoding($false) 로 BOM 없는 UTF-8 강제
$tmpFile  = Join-Path $env:TEMP "dpp_pilot_secret_tmp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
try {
    [System.IO.File]::WriteAllText($tmpFile, $secretJson, $utf8NoBom)
    Write-Host "  JSON 작성 완료 (UTF-8 no BOM): $tmpFile"

    # ── 5. 기존 시크릿 여부 확인 → put or create ──────────────────
    Write-Host ""
    Write-Host "=== Secrets Manager 업로드 ==="
    Write-Host "  Secret: $SecretName"

    $exists = $false
    try {
        $null = aws secretsmanager describe-secret `
            --secret-id $SecretName `
            --region $Region --profile $Profile `
            --output text --query "Name" 2>&1
        if ($LASTEXITCODE -eq 0) { $exists = $true }
    } catch { $exists = $false }

    if ($exists) {
        Write-Host "  기존 시크릿 업데이트..."
        aws secretsmanager put-secret-value `
            --secret-id $SecretName `
            --secret-string "file://$tmpFile" `
            --region $Region --profile $Profile `
            --output text --query "VersionId"
    } else {
        Write-Host "  신규 시크릿 생성..."
        aws secretsmanager create-secret `
            --name $SecretName `
            --description "DPP Pilot application secrets" `
            --secret-string "file://$tmpFile" `
            --region $Region --profile $Profile `
            --query "ARN" --output text
    }

    Write-Host ""
    Write-Host "=== 완료 ==="
    aws secretsmanager describe-secret `
        --secret-id $SecretName `
        --region $Region --profile $Profile `
        --query "[Name, ARN]" --output table

} finally {
    if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
}
