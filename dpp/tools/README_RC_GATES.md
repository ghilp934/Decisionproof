# RC Gates One-shot Execution

## RC Gates Summary

| Gate | File | Coverage |
|------|------|----------|
| RC-1 | test_rc1_contract.py | API contract |
| RC-2 | test_rc2_error_format.py | RFC 9457 error format |
| RC-3 | test_rc3_rate_limit_headers.py | IETF RateLimit headers |
| RC-4 | test_rc4_billing_invariants.py | Billing invariants (API) |
| RC-4 | test_rc4_finalize_invariants.py | Billing invariants (worker) |
| RC-5 | test_rc5_gate.py | Docker build smoke |
| RC-6 | test_rc6_observability.py | Structured logging |
| RC-7 | test_rc7_otel_contract.py | OpenTelemetry contract |
| RC-8 | test_rc8_release_packet_gate.py | Release packet |
| RC-9 | test_rc9_ops_pack_gate.py | Ops pack |
| **RC-10** | **test_rc10_logging_masking_and_worm_audit.py** | **Log masking + WORM audit** |
| **RC-11** | **test_rc11_workflow_hygiene_ephemeral_password.py** | **Workflow hygiene: ephemeral password** |
| **RC-12** | **test_rc12_tls_drift_gate.py** | **TLS drift guard: sslmode=disable absent in staging/prod** |
| **RC-13** | **test_rc13_worm_required_guard.py** | **WORM required guard: no silent FileAuditSink fallback** |
| **RC-10.P5.7** | **test_rc10_webhook_error_semantics.py** | **Webhook error taxonomy: retry storm prevention** |
| **RC-10.P5.8** | **test_rc10_worm_mode_hardening.py** | **WORM mode hardening: explicit GOVERNANCE/COMPLIANCE, no bypass** |
| **RC-10.P5.9** | **test_rc10_p59_fingerprint_hmac_kid.py** | **Fingerprint hardening: HMAC(pepper) + Key-ID prefix, rotation-ready** |
| **RC-14** | **test_rc14_demo_marketplace_gate.py** | **Mini Demo P0 Lockdown: Fail-Closed 503, auth 401, poll 429, openapi-demo LOCK** |

## RC-10: Log Masking + Kill-Switch WORM Audit (P5.2 + P5.3)

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| A | Webhook log with PII/Bearer token | `[REDACTED]` in log, `payload_hash` present |
| B | `exc_info=True` log with secret in exception | No raw secret in `exc_info` field |
| C | `sanitize_str` on 50 000-char input | < 1 s, returns `[TRUNCATED len=N sha256=...]` |
| D | STRICT=1 + FailingAuditSink | HTTP 500, kill-switch state unchanged, no raw token in logs |
| E | STRICT=0 + FailingAuditSink | HTTP 200, `audit_write_ok=false` |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `KILL_SWITCH_AUDIT_BUCKET` | _(unset)_ | S3 bucket with Object Lock enabled. If set, uses S3WormAuditSink |
| `KILL_SWITCH_AUDIT_REGION` | `AWS_DEFAULT_REGION` | AWS region for the audit bucket |
| `KILL_SWITCH_AUDIT_FILE_DIR` | `tempdir` | Local fallback directory for FileAuditSink |
| `KILL_SWITCH_AUDIT_STRICT` | `0` | `1` = fail-closed (HTTP 500 on sink failure) |

### Quick verification

```bash
# RC-10 only
pytest apps/api/tests/test_rc10_logging_masking_and_worm_audit.py -v

# With STRICT mode
KILL_SWITCH_AUDIT_STRICT=1 pytest apps/api/tests/test_rc10_logging_masking_and_worm_audit.py::TestRC10LogMaskingAndWormAudit::test_d_strict_mode_audit_failure_returns_500 -v
```

---

## RC-11: Workflow Hygiene — Ephemeral Password (P5.4)

### Purpose

Prevent password-like literals from appearing in public CI workflow files, and enforce
an ephemeral per-run password strategy with masking so that no static credential can
be extracted from the public repository.

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| A | Workflow file exists at `.github/workflows/phase45_db_rollback_drill.yml` | File readable |
| B | Banned literal `drillpw2026` is absent | `"drillpw2026" not in text` |
| C | Ephemeral password strategy is present | `DRILL_PG_PASSWORD` job env **and** `POSTGRES_PASSWORD` both set to `drill-${{ github.run_id }}-${{ github.run_attempt }}` |
| D | `LOCAL_DB_URL` uses env var (no inline password) | `${DRILL_PG_PASSWORD}` or `$DRILL_PG_PASSWORD` referenced in workflow |
| E | Ephemeral password is masked | `::add-mask::` **and** `DRILL_PG_PASSWORD` present in workflow |

### Quick verification

```bash
# RC-11 only
pytest apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py -v
```

### FAIL criteria

Any of these causes RC-11 to FAIL (CI is blocked):
- `drillpw2026` (or any new static password literal) reintroduced in the workflow
- `DRILL_PG_PASSWORD` job env var removed or changed to a static value
- `POSTGRES_PASSWORD` service env set to a static value
- `LOCAL_DB_URL` built with an inline password instead of `${DRILL_PG_PASSWORD}`
- `::add-mask::` step removed

---

## RC-12: TLS / DB URL Drift Guard (P5.5)

### Purpose

Fail CI if `sslmode=disable` (a CI/local-only setting) leaks into staging/prod
deployment assets. Config drift at this level disables server certificate
verification in production, which is a critical TLS misconfiguration.

### Scan scope

**INCLUDED** (deployment assets only):
- `k8s/**/*.yml, k8s/**/*.yaml` — Kubernetes manifests
- `infra/**/*.tf, infra/**/*.json, infra/**/*.yml, infra/**/*.yaml` — IaC
- `ops/scripts/**/*.sh, *.template, *.sql` — Operational deploy scripts

**EXCLUDED** (may legitimately use sslmode=disable):
- `evidence/**`, `**/.git/**`, `.github/workflows/**`
- `**/*.md` — documentation/runbooks
- `infra/docker-compose.yml` — local-dev only

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| T1 | Repo-wide scan on actual tree | exit 0, `ok=True`, `scanned_files > 0` |
| T2 | Synthetic staging manifest with `sslmode=disable` | exit 2, `ok=False`, exactly 1 hit, `excerpt == "sslmode=disable"` |

### Quick verification

```bash
# RC-12 only (drop addopts to avoid coverage noise)
pytest -q -o addopts= apps/api/tests/test_rc12_tls_drift_gate.py

# Standalone drift gate CLI
python tools/security/tls_drift_gate.py --root . --out evidence/security/tls_drift_gate_report.json
```

### What to do on failure

RC-12 FAILS when `sslmode=disable` appears in a scoped file.

**Option A (preferred)**: Remove `sslmode=disable` from the affected manifest/script.
  Production DB URLs must use `sslmode=require` or `sslmode=verify-full`.

**Option B**: If the file is CI-only and not a staging/prod asset, move it into
  `.github/workflows/**` (explicitly excluded) or document why it belongs in the
  CI-only category and add it to `DEFAULT_EXCLUDE_PATTERNS` in `tls_drift_gate.py`.

---

## RC-10.P5.7: Webhook Error Semantics — Retry Storm Prevention (P5.7)

### Purpose

Enforce strict HTTP status code taxonomy for webhook endpoints (PayPal + Toss) to prevent
retry storms. Providers retry 5xx responses but not 4xx. Misclassifying client faults as
server faults causes exponential retry storms that are hard to diagnose.

### Status Code Taxonomy

| Class | Condition | HTTP | Error Code |
|-------|-----------|------|------------|
| A | Invalid JSON / malformed payload | 400 | WEBHOOK_INVALID_JSON |
| B | Signature invalid / verify != SUCCESS | 401 | WEBHOOK_SIGNATURE_INVALID |
| C | Required header missing | 400 | WEBHOOK_MISSING_HEADERS |
| D | Our misconfig (missing secret/webhook_id) | 500 | WEBHOOK_PROVIDER_MISCONFIG |
| E | Upstream network/SDK error during verify | 500 | WEBHOOK_VERIFY_UPSTREAM_FAILED |
| F | Internal DB/processing error | 500 | WEBHOOK_INTERNAL_ERROR |

**500 is ONLY for D/E/F. Signature mismatch is NEVER 500.**

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| A | Invalid JSON to `/webhooks/paypal` | 400, `WEBHOOK_INVALID_JSON` log with `payload_hash` |
| B | PayPal `verify_webhook_signature` returns `FAILURE` | 401 (not 500), warning log, no `Retry-After` |
| C | PayPal verify raises `httpx.RequestError` | 500, `WEBHOOK_VERIFY_UPSTREAM_FAILED` log, `Retry-After: 60` |
| D | Toss HMAC signature mismatch | 401, `WEBHOOK_SIGNATURE_INVALID` warning log |
| E | No false positive: logs emitted AND secrets absent | `WEBHOOK_RECEIVED` present; raw token NOT in log output |

### Mocking approach

- Tests B and C: `patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_paypal)`
- Test D: Set `TOSS_WEBHOOK_SECRET` env var + provide wrong `X-TossPayments-Signature`
- Test E: No mock needed — PAYPAL_CLIENT_ID not configured in CI → WEBHOOK_PROVIDER_MISCONFIG

### Quick verification

```bash
# P5.7 only
pytest apps/api/tests/test_rc10_webhook_error_semantics.py -v -o addopts=

# All P5 gates
pytest apps/api/tests/test_rc10_logging_masking_and_worm_audit.py \
       apps/api/tests/test_rc10_webhook_error_semantics.py \
       apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py \
       apps/api/tests/test_rc12_tls_drift_gate.py \
       apps/api/tests/test_rc13_worm_required_guard.py \
       -v -o addopts=
```

### FAIL criteria

Any of these causes P5.7 to FAIL:
- Signature mismatch returns 5xx (retry storm risk)
- Network/upstream error returns 4xx (wrong taxonomy)
- 5xx response missing `Retry-After` header
- Log not emitted on failure (silent swallow)
- Raw payload content or tokens appear in any log line

---

## RC-10.P5.8: WORM Mode Hardening (P5.8)

### Purpose

Ensure the S3 WORM audit sink is truly immutable by enforcing explicit WORM mode selection
(`GOVERNANCE` or `COMPLIANCE`) and preventing any bypass behavior in PutObject calls.

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| A | `put_record()` PutObject call | Both `ObjectLockMode` AND `ObjectLockRetainUntilDate` present as a paired set; RetainUntilDate is UTC-aware |
| B[GOVERNANCE] | `S3WormAuditSink(mode="GOVERNANCE")` | `put_object` kwargs `ObjectLockMode == "GOVERNANCE"` |
| B[COMPLIANCE] | `S3WormAuditSink(mode="COMPLIANCE")` | `put_object` kwargs `ObjectLockMode == "COMPLIANCE"` |
| C | `REQUIRED=1` + bucket set + `WORM_MODE` absent | `AuditSinkConfigError` with `WORM_MODE_REQUIRED_BUT_NOT_SET` |
| C2 | `REQUIRED=1` + invalid `WORM_MODE` value | `AuditSinkConfigError` with `INVALID_WORM_MODE` |
| D | `put_record()` bypass audit | No `BypassGovernanceRetention` in kwargs; all params in allowed prefix set |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `KILL_SWITCH_AUDIT_WORM_MODE` | `GOVERNANCE` (non-required mode) | `GOVERNANCE` or `COMPLIANCE`. **Mandatory** when `REQUIRED=1`. |
| `KILL_SWITCH_AUDIT_REQUIRED` | `0` | `1` = WORM is mandatory; explicit WORM_MODE required |

### Quick verification

```bash
# P5.8 only
pytest apps/api/tests/test_rc10_worm_mode_hardening.py -v -o addopts=

# All P5 gates
pytest apps/api/tests/test_rc10_logging_masking_and_worm_audit.py \
       apps/api/tests/test_rc10_webhook_error_semantics.py \
       apps/api/tests/test_rc10_worm_mode_hardening.py \
       apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py \
       apps/api/tests/test_rc12_tls_drift_gate.py \
       apps/api/tests/test_rc13_worm_required_guard.py \
       -v -o addopts=
```

### FAIL criteria

Any of these causes P5.8 to FAIL:
- `S3WormAuditSink` does not accept `mode` parameter
- `put_object` call missing `ObjectLockMode` or `ObjectLockRetainUntilDate`
- `put_object` includes `BypassGovernanceRetention` or any unexpected bypass parameter
- `REQUIRED=1` + absent `WORM_MODE` does NOT raise `AuditSinkConfigError`
- `REQUIRED=1` + invalid `WORM_MODE` does NOT raise `AuditSinkConfigError`

---

## RC-10.P5.9: Fingerprint HMAC + Key-ID Prefix (P5.9)

### Purpose

Replace the plain SHA-256 actor token fingerprint with HMAC-SHA256(pepper) and a Key-ID
prefix. Format: `<kid>:<12 hex chars>`. The kid prefix identifies the pepper epoch, enabling
safe key rotation without losing historical record verifiability.

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| T1 | `fingerprint_token("tok_live_ABC123")` with kid + pepper set | Result matches `^kid_test:[0-9a-f]{12}$` |
| T2 | Same token + same pepper (×2); same token + different pepper | Identical for same; different for different pepper; exact HMAC cross-check |
| T3 | `REQUIRED=1` + no pepper | `RuntimeError("FINGERPRINT_PEPPER_NOT_SET")` |
| T3b | `STRICT=1` + no pepper | Same RuntimeError |
| T4 | `build_kill_switch_audit_record(actor_token=raw_sensitive_token)` | raw token absent from JSON; fingerprint present and matches `<kid>:[0-9a-f]{12}` |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `KILL_SWITCH_AUDIT_FINGERPRINT_KID` | `kid_dev` | Key-ID prefix. Must change when pepper changes. Use `kid_YYYYMM` convention. |
| `KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64` | _(unset)_ | Base64 pepper (preferred). **Mandatory** when REQUIRED=1 or STRICT=1. |
| `KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER` | _(unset)_ | UTF-8 pepper (fallback). Same mandatory condition. |

### Quick verification

```bash
# P5.9 only
pytest apps/api/tests/test_rc10_p59_fingerprint_hmac_kid.py -v -o addopts=

# All P5 gates (32 tests)
pytest apps/api/tests/test_rc10_logging_masking_and_worm_audit.py \
       apps/api/tests/test_rc10_webhook_error_semantics.py \
       apps/api/tests/test_rc10_worm_mode_hardening.py \
       apps/api/tests/test_rc10_p59_fingerprint_hmac_kid.py \
       apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py \
       apps/api/tests/test_rc12_tls_drift_gate.py \
       apps/api/tests/test_rc13_worm_required_guard.py \
       -v -o addopts=
```

### FAIL criteria

Any of these causes P5.9 to FAIL:
- `fingerprint_token()` does not exist or cannot be imported
- Fingerprint format does not include kid prefix (e.g., returns plain hex)
- HMAC is not pepper-sensitive (same result for different peppers)
- Missing pepper with REQUIRED=1 does NOT raise RuntimeError
- Raw token appears in audit record JSON
- `actor.token_fingerprint` is absent or does not match `<kid>:[0-9a-f]{12}`

---

## RC-13: WORM Required Guard + Break-glass Alert Plan (P5.6)

### Purpose

Prevent silent `FileAuditSink` fallback in production by enforcing the
`KILL_SWITCH_AUDIT_REQUIRED=1` configuration guard. When required mode is on,
the application must fail closed rather than silently downgrading to local file logging.

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| T1 | `REQUIRED=1` + missing `KILL_SWITCH_AUDIT_BUCKET` | `validate_audit_required_config()` raises `AuditSinkConfigError` with `AUDIT_SINK_REQUIRED_BUT_NOT_CONFIGURED` |
| T2 | `REQUIRED=1` + no bucket → `POST /admin/kill-switch` | HTTP 500, kill-switch mode **unchanged**, no raw token in response body |
| T3 | `REQUIRED=1` + bucket set | `get_default_audit_sink()` returns `S3WormAuditSink`, never `FileAuditSink` |
| T4 | Break-glass runbook file exists | `ops/runbooks/kill_switch_audit_break_glass_alerts.md` contains: `Break-glass`, `CloudTrail`, `EventBridge`, `SNS`, `bypassGovernanceRetention` |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `KILL_SWITCH_AUDIT_REQUIRED` | `0` | `1` = WORM is mandatory; `FileAuditSink` fallback forbidden |
| `KILL_SWITCH_AUDIT_BUCKET` | _(unset)_ | Required when `AUDIT_REQUIRED=1` |

### Quick verification

```bash
# RC-13 only
pytest apps/api/tests/test_rc13_worm_required_guard.py -v
```

### FAIL criteria

RC-13 FAILS when any of:
- `AuditSinkConfigError` is not raised when `REQUIRED=1` and bucket is unset
- `/admin/kill-switch` returns non-500 when audit is required but not configured
- Kill-switch state changes despite audit misconfiguration
- Break-glass runbook missing or lacking required headings

---

---

## RC-14: Mini Demo Marketplace Gate — P0 Lockdown

### Purpose

Enforce Fail-Closed auth, openapi-demo surface LOCK, and poll rate limiting
for the public Mini Demo API surface (`POST /v1/demo/runs`, `GET /v1/demo/runs/{run_id}`).
Prevents silent auth bypass when `RAPIDAPI_PROXY_SECRET` is accidentally unset.

### What it tests

| Test | Scenario | Pass Condition |
|------|----------|----------------|
| (1) openapi-demo LOCK | `GET /.well-known/openapi-demo.json` | 200, `servers.length==1`, `servers[0].url==https://api.decisionproof.io.kr`, `paths=={/v1/demo/runs, /v1/demo/runs/{run_id}}` |
| (2) Fail-Closed POST | `RAPIDAPI_PROXY_SECRET` empty, POST demo run | **503** `application/problem+json` — never 202/200/401 |
| (2) Fail-Closed GET | `RAPIDAPI_PROXY_SECRET` empty, GET demo run | **503** `application/problem+json` |
| (2) 503 detail | 503 body | `detail` contains `"RAPIDAPI_PROXY_SECRET"` (operator guidance, no secret value) |
| (3) Auth: missing proxy header | env set, no `X-RapidAPI-Proxy-Secret` | **401** `application/problem+json` |
| (3) Auth: wrong proxy header | env set, wrong `X-RapidAPI-Proxy-Secret` | **401** `application/problem+json` |
| (3) Auth: missing Bearer | proxy secret correct, no `Authorization` | **401** `application/problem+json` |
| (3) Auth: wrong Bearer | proxy secret correct, wrong Bearer | **401** `application/problem+json` |
| (4) Poll rate limit | POST → GET → immediate GET | **429** `application/problem+json` |
| (4) Retry-After | 429 response | `Retry-After` header present and is positive integer |

### STOP RULES (CI BLOCKED if any fail)

- `RAPIDAPI_PROXY_SECRET` missing → demo returns `202/200/401` (not `503`)
- Any error response is NOT `application/problem+json` with `type/title/status/detail/instance`
- `429` response missing `Retry-After` header
- openapi-demo paths drift from exactly `{/v1/demo/runs, /v1/demo/runs/{run_id}}`

### Quick verification

```bash
pytest -q -o addopts= apps/api/tests/test_rc14_demo_marketplace_gate.py
```

### FAIL criteria

Any of these causes RC-14 to FAIL:
- `RAPIDAPI_PROXY_SECRET` empty/unset → demo endpoint returns non-503
- Auth header wrong/missing → response is not 401 or not `problem+json`
- Double poll → response is not 429 or `Retry-After` header absent
- openapi-demo `servers` length != 1 or URL != `https://api.decisionproof.io.kr`
- openapi-demo `paths` contains any key outside `{/v1/demo/runs, /v1/demo/runs/{run_id}}`

---

## Usage

**Mac/Linux:**
```bash
cd /path/to/dpp
bash tools/run_rc_gates.sh
```

**Windows (PowerShell):**
```powershell
cd C:\path\to\dpp
powershell -ExecutionPolicy Bypass -File tools\run_rc_gates.ps1
```

## Output

All execution evidence is automatically saved to:
```
evidence/01_ci/<timestamp>/
├── rc_run_cmd.txt          # Exact pytest command executed
├── rc_run_stdout.log       # Test output
├── rc_run_stderr.log       # Error output
├── rc_run_env.txt          # Environment snapshot (Python/Docker/Compose versions)
└── dump_logs/              # Auto-generated on failure only
    ├── docker_compose_ps.txt
    ├── docker_postgres.log
    ├── docker_redis.log
    ├── docker_localstack.log
    └── docker_ps_all.txt
```

## Common Failures and Immediate Actions

### (A) Postgres/Redis Connection Failure

**Symptoms:**
- `connection refused`
- `could not connect to server`
- `redis connection error`

**Immediate Action:**
1. Check service status:
   ```bash
   docker compose -f infra/docker-compose.yml ps
   ```
2. View recent logs (last 200 lines):
   ```bash
   docker compose -f infra/docker-compose.yml logs --tail 200 --timestamps postgres
   docker compose -f infra/docker-compose.yml logs --tail 200 --timestamps redis
   docker compose -f infra/docker-compose.yml logs --tail 200 --timestamps localstack
   ```
3. Restart services if unhealthy:
   ```bash
   docker compose -f infra/docker-compose.yml down
   docker compose -f infra/docker-compose.yml up -d
   ```

### (B) Docker Daemon/Image Failure (RC-5)

**Symptoms:**
- `Cannot connect to the Docker daemon`
- `image not found: dpp-api:rc-test`
- `Error response from daemon`

**Immediate Action:**
1. Verify Docker is running:
   ```bash
   docker info
   ```
2. Check existing images:
   ```bash
   docker images | grep dpp
   ```
3. Rebuild required images:
   ```bash
   docker build -f Dockerfile.api -t dpp-api:rc-test .
   docker build -f Dockerfile.worker -t dpp-worker:rc-test .
   docker build -f Dockerfile.reaper -t dpp-reaper:rc-test .
   ```

### (C) Pytest Option Conflict

**Symptoms:**
- `pytest: error: unrecognized arguments: --cov`
- `pytest: error: unrecognized arguments: --xdist`

**Immediate Action:**
1. The script already includes `-o addopts=` to bypass `pytest.ini` / `pyproject.toml` addopts.
2. If error persists, check plugin installation:
   ```bash
   pip install -e '.[dev]'  # Reinstall dev dependencies
   ```
3. Manual fallback (without script):
   ```bash
   pytest -q -o addopts= --maxfail=1 \
     apps/api/tests/test_rc1_contract.py \
     apps/api/tests/test_rc2_error_format.py \
     apps/api/tests/test_rc3_rate_limit_headers.py \
     apps/api/tests/test_rc4_billing_invariants.py \
     apps/worker/tests/test_rc4_finalize_invariants.py \
     apps/api/tests/test_rc5_gate.py \
     apps/api/tests/test_rc6_observability.py \
     apps/api/tests/test_rc7_otel_contract.py \
     apps/api/tests/test_rc8_release_packet_gate.py \
     apps/api/tests/test_rc9_ops_pack_gate.py \
     apps/api/tests/test_rc10_logging_masking_and_worm_audit.py
   ```

## Script Behavior

- **Fail-fast:** Stops at first failure (`--maxfail=1`)
- **Auto-dump:** Captures Docker logs automatically on any failure
- **Deterministic:** Uses `-o addopts=` to ignore pytest.ini configuration
- **Evidence:** All runs create timestamped evidence folder, pass or fail

## Dependencies

- Python 3.12+
- Docker + Docker Compose
- pytest (installed via `pip install -e '.[dev]'`)
- Services: postgres, redis, localstack (auto-started by script)

## Troubleshooting

If script fails to execute:
1. Ensure you're in the repo root (`dpp/`)
2. Check script permissions (Mac/Linux): `chmod +x tools/run_rc_gates.sh`
3. Verify Docker is running: `docker ps`
4. Check Python environment: `python --version` (should be 3.12+)

For detailed RC acceptance criteria, see: `/docs/RC_ACCEPTANCE.md`

For complete execution workflow (local 2x + CI + archive), see: `/docs/rc/RC_RUNBOOK.md`

## CI Integration

RC Gates run automatically in GitHub Actions on every pull request and push to `master` or `release/**` branches. The CI workflow uses the same `tools/run_rc_gates.sh` script, ensuring identical behavior between local and CI environments.

**Evidence artifacts** are automatically uploaded to GitHub Actions artifacts (retention: 7 days by default) for every CI run, regardless of PASS/FAIL status. Download artifacts from the workflow run page to inspect test outputs and logs.

**PR comments** are automatically posted/updated on pull requests (non-fork only) with a summary of RC results, including the latest evidence directory path and the first failure nodeid (if any).

For manual CI runs with custom options, use the "workflow_dispatch" trigger in GitHub Actions UI.
