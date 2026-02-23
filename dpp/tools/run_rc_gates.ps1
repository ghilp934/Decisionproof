# Decisionproof RC Gates One-shot Execution Script (Windows PowerShell)
# Purpose: Run all RC gates (RC-1 to RC-9) and auto-dump logs on failure

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$EvidenceDir = Join-Path $RepoRoot "evidence\01_ci\$Timestamp"
$DumpDir = Join-Path $EvidenceDir "dump_logs"

# Create evidence directories
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path $DumpDir | Out-Null

# RC test files in execution order
$RcTests = @(
    "apps/api/tests/test_rc1_contract.py",
    "apps/api/tests/test_rc2_error_format.py",
    "apps/api/tests/test_rc3_rate_limit_headers.py",
    "apps/api/tests/test_rc4_billing_invariants.py",
    "apps/worker/tests/test_rc4_finalize_invariants.py",
    "apps/api/tests/test_rc5_gate.py",
    "apps/api/tests/test_rc6_observability.py",
    "apps/api/tests/test_rc7_otel_contract.py",
    "apps/api/tests/test_rc8_release_packet_gate.py",
    "apps/api/tests/test_rc9_ops_pack_gate.py",
    "apps/api/tests/test_rc10_logging_masking_and_worm_audit.py",
    "apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py",
    "apps/api/tests/test_rc12_tls_drift_gate.py",
    "apps/api/tests/test_rc13_worm_required_guard.py",
    "apps/api/tests/test_rc10_webhook_error_semantics.py",
    "apps/api/tests/test_rc10_worm_mode_hardening.py",
    "apps/api/tests/test_rc10_p59_fingerprint_hmac_kid.py",
    "apps/api/tests/test_rc14_demo_marketplace_gate.py"
)

# Auto-dump logs function
function Dump-Logs {
    param([int]$ExitCode)

    # CRITICAL: Display pytest output FIRST if files exist (before any other output)
    $StdoutFile = Join-Path $EvidenceDir "rc_run_stdout.log"
    $StderrFile = Join-Path $EvidenceDir "rc_run_stderr.log"

    if (Test-Path $StdoutFile) {
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
        Write-Host "PYTEST STDOUT:" -ForegroundColor Yellow
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
        Get-Content $StdoutFile | Write-Host
    } else {
        Write-Host "WARNING: rc_run_stdout.log not found at $EvidenceDir" -ForegroundColor Red
    }

    if (Test-Path $StderrFile) {
        $StderrContent = Get-Content $StderrFile -Raw
        if ($StderrContent.Trim().Length -gt 0) {
            Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
            Write-Host "PYTEST STDERR:" -ForegroundColor Yellow
            Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
            Write-Host $StderrContent
        }
    }

    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
    Write-Host "[FAILURE DETECTED] Exit code: $ExitCode" -ForegroundColor Red
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
    Write-Host "Auto-dumping logs to: $DumpDir"

    # Docker compose status
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Host "Collecting docker compose ps..."
        $ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"

        docker compose -f $ComposeFile ps -a *> (Join-Path $DumpDir "docker_compose_ps.txt")

        # Service logs
        Write-Host "Collecting docker compose logs (last 500 lines)..."
        docker compose -f $ComposeFile logs --tail=500 --timestamps postgres *> (Join-Path $DumpDir "docker_postgres.log")
        docker compose -f $ComposeFile logs --tail=500 --timestamps redis *> (Join-Path $DumpDir "docker_redis.log")
        docker compose -f $ComposeFile logs --tail=500 --timestamps localstack *> (Join-Path $DumpDir "docker_localstack.log")

        # Container status
        Write-Host "Collecting docker ps -a..."
        docker ps -a *> (Join-Path $DumpDir "docker_ps_all.txt")
    }

    # Last command info
    "Exit code: $ExitCode" | Out-File -FilePath (Join-Path $DumpDir "last_error.txt")

    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
    Write-Host "Logs dumped to: $DumpDir" -ForegroundColor Red
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
}

try {
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host "Decisionproof RC Gates One-shot Execution" -ForegroundColor Green
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host "Timestamp: $Timestamp"
    Write-Host "Evidence directory: $EvidenceDir"
    Write-Host ""

    # Step 1: Environment snapshot
    Write-Host "[1/6] Collecting environment snapshot..." -ForegroundColor Yellow
    $EnvOutput = @"
=== Python Version ===
$(python --version 2>&1)

=== Pytest Version ===
$(pytest --version 2>&1)

=== Docker Version ===
$(docker --version 2>&1)

=== Docker Compose Version ===
$(docker compose version 2>&1)

=== Docker Compose Services Status ===
$(docker compose -f "$RepoRoot\infra\docker-compose.yml" ps 2>&1)
"@
    $EnvOutput | Out-File -FilePath (Join-Path $EvidenceDir "rc_run_env.txt")

    # Step 2: Start dependencies
    Write-Host "[2/6] Starting docker dependencies (postgres/redis/localstack)..." -ForegroundColor Yellow
    Push-Location (Join-Path $RepoRoot "infra")
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "Docker compose up failed" }

    Write-Host "Waiting for services to be healthy (max 60s)..."
    $timeout = 60
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        $status = docker compose ps 2>&1 | Out-String
        if ($status -match "\(healthy\)") {
            break
        }
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
    if ($elapsed -ge $timeout) {
        Write-Host "Warning: Services may not be fully healthy" -ForegroundColor Red
    }
    Pop-Location

    # Step 3: Build docker images (RC-5 requirement)
    Write-Host "[3/6] Building docker images for RC-5 gate..." -ForegroundColor Yellow
    Push-Location $RepoRoot

    docker build -f Dockerfile.api -t dpp-api:rc-test .
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed for dpp-api" }

    docker build -f Dockerfile.worker -t dpp-worker:rc-test .
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed for dpp-worker" }

    docker build -f Dockerfile.reaper -t dpp-reaper:rc-test .
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed for dpp-reaper" }

    Pop-Location

    # Step 4: Set environment variables
    Write-Host "[4/6] Setting environment variables..." -ForegroundColor Yellow
    $env:DATABASE_URL = "postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
    $env:REDIS_URL = "redis://localhost:6379/0"
    $env:AWS_ENDPOINT_URL = "http://localhost:4566"
    $env:AWS_ACCESS_KEY_ID = "test"
    $env:AWS_SECRET_ACCESS_KEY = "test"
    $env:AWS_DEFAULT_REGION = "us-east-1"

    # Ops Hardening v2: Service-specific endpoints and required env vars
    $env:S3_ENDPOINT_URL = "http://localhost:4566"
    $env:SQS_ENDPOINT_URL = "http://localhost:4566"
    $env:S3_RESULT_BUCKET = "dpp-results-test"
    $env:SQS_QUEUE_URL = "http://localhost:4566/000000000000/dpp-runs"

    # Step 5: Construct pytest command
    Write-Host "[5/6] Constructing pytest command..." -ForegroundColor Yellow
    $PytestArgs = @("-q", "-o", "addopts=", "--maxfail=1") + $RcTests
    $PytestCmd = "pytest " + ($PytestArgs -join " ")

    $PytestCmd | Out-File -FilePath (Join-Path $EvidenceDir "rc_run_cmd.txt")
    Write-Host "Command: $PytestCmd"
    Write-Host ""

    # Step 6: Execute RC gates
    Write-Host "[6/6] Executing RC gates..." -ForegroundColor Yellow
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green

    Push-Location $RepoRoot

    # Run pytest and capture output
    $StdoutFile = Join-Path $EvidenceDir "rc_run_stdout.log"
    $StderrFile = Join-Path $EvidenceDir "rc_run_stderr.log"

    & pytest @PytestArgs > $StdoutFile 2> $StderrFile
    $PytestExitCode = $LASTEXITCODE

    Pop-Location

    # Display output
    if (Test-Path $StdoutFile) {
        Get-Content $StdoutFile | Write-Host
    } else {
        Write-Host "ERROR: rc_run_stdout.log not found!" -ForegroundColor Red
    }

    if (Test-Path $StderrFile) {
        $StderrContent = Get-Content $StderrFile -Raw
        if ($StderrContent.Trim().Length -gt 0) {
            Write-Host "--- STDERR ---" -ForegroundColor Yellow
            Write-Host $StderrContent
        }
    }

    # Check result
    if ($PytestExitCode -eq 0) {
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
        Write-Host "✅ ALL RC GATES PASSED" -ForegroundColor Green
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
        Write-Host "Evidence saved to: $EvidenceDir"
        exit 0
    } else {
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Red
        Write-Host "❌ RC GATES FAILED (exit code: $PytestExitCode)" -ForegroundColor Red
        Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Red
        throw "RC gates failed with exit code $PytestExitCode"
    }

} catch {
    $ExitCode = if ($LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
    Dump-Logs -ExitCode $ExitCode
    exit $ExitCode
} finally {
    Write-Host ""
    Write-Host "Evidence location: $EvidenceDir" -ForegroundColor Cyan
}
