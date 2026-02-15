# Pilot Readiness Monitor Runbook

## Purpose

The Pilot Readiness Monitor continuously detects **readiness regression** during Paid Pilot operations. "Readiness regression" means critical customer-facing endpoints become unavailable or degraded, potentially blocking pilot users.

This monitor runs:
- **Scheduled**: Daily at 01:30 KST (16:30 UTC)
- **On-demand**: Manual workflow_dispatch trigger
- **Locally**: For debugging and pre-deployment validation

## What It Checks

The monitor validates 10 critical endpoints:

1. `GET /health` → 200
2. `GET /readyz` → 200
3. `GET /.well-known/openapi.json` → 200
4. `GET /llms.txt` → 200
5. `GET /api-docs` → 200
6. `GET /redoc` → 200
7. `GET /pricing/ssot.json` → 200
8. `GET /docs/quickstart.md` → 200
9. `GET /v1/runs` → 401 (no token) or 200 (with token)
10. `GET /metrics` → 200 (optional, enabled via `CHECK_METRICS=1`)

**Success Criteria**: ≥ 80% of checks must pass (configurable via `PASS_THRESHOLD`)

## Enable/Disable Monitor

### Enable Monitor

1. Set repository variable:
   ```bash
   gh variable set PILOT_MONITOR_ENABLED --body "1"
   ```

2. Configure base URL (choose one):
   ```bash
   # Option 1: Secret (recommended for production)
   gh secret set PILOT_BASE_URL --body "https://staging-api.decisionproof.ai"

   # Option 2: Variable (for non-sensitive URLs)
   gh variable set PILOT_BASE_URL --body "https://staging-api.decisionproof.ai"
   ```

3. (Optional) Configure authentication token:
   ```bash
   gh secret set PILOT_TOKEN --body "your-bearer-token-here"
   ```

### Disable Monitor

```bash
gh variable set PILOT_MONITOR_ENABLED --body "0"
```

The workflow will skip execution cleanly without failing.

## Run Locally

### Prerequisites

- `curl`
- `jq`
- `bc`

### Basic Run

```bash
PILOT_BASE_URL=https://staging-api.decisionproof.ai \
bash dpp/tools/pilot_readiness_monitor.sh
```

### With Authentication

```bash
PILOT_BASE_URL=https://staging-api.decisionproof.ai \
PILOT_TOKEN=your-bearer-token \
bash dpp/tools/pilot_readiness_monitor.sh
```

### Custom Configuration

```bash
PILOT_BASE_URL=https://staging-api.decisionproof.ai \
PILOT_TOKEN=your-bearer-token \
PASS_THRESHOLD=0.9 \
RETRIES=5 \
RETRY_SLEEP_SEC=3 \
CHECK_METRICS=1 \
bash dpp/tools/pilot_readiness_monitor.sh
```

## Run Manually in GitHub Actions

1. Go to: **Actions** → **Pilot Readiness Monitor** → **Run workflow**

2. Configure inputs:
   - **base_url** (optional): Override default base URL
   - **open_issue_on_fail** (optional): Create GitHub issue on failure

3. Click **Run workflow**

## Evidence Location

### Local Runs

Evidence is saved to:
```
dpp/evidence/pilot_monitor/YYYYMMDD_HHMMSS/
├── manifest.json           # Summary: ok, checks_passed, threshold, etc.
├── preflight/
│   └── versions.txt        # Git SHA, OS, tool versions
├── meta/
│   └── event.txt           # Event type, GitHub context
├── smoke/
│   ├── results.json        # Detailed check results (array)
│   └── summary.txt         # Human-readable summary
└── dump_logs/
    └── diagnostics.txt     # Created only on error
```

### CI Runs

Evidence is uploaded as artifact:
- **Artifact name**: `pilot-readiness-monitor-evidence-<sha>`
- **Retention**: 30 days
- **Download**: Via Actions UI or `gh run download`

## Success Criteria

### PASS

- **Exit code**: 0
- **Pass ratio**: ≥ threshold (default 80%)
- **Evidence**: All required files present

Example `manifest.json`:
```json
{
  "ok": true,
  "checks_passed": 9,
  "checks_total": 10,
  "pass_ratio": 0.90,
  "threshold": 0.80,
  "base_url": "https://staging-api.decisionproof.ai",
  "event": "schedule",
  "mode": "ci",
  "commit_sha": "abc123...",
  "date_utc": "2026-02-15T16:30:00Z",
  "date_kst": "2026-02-16 01:30:00 KST"
}
```

### FAIL

- **Exit code**: 1
- **Pass ratio**: < threshold
- **Evidence**: Contains `smoke/summary.txt` with failed checks list

## Failure Handling

### 1. Check Step Summary

GitHub Actions run → **Summary** tab shows:
- Event, SHA, base URL
- Checks passed/total
- First failing check name
- Evidence directory path

### 2. Download Evidence

```bash
# Find run ID
gh run list --workflow "Pilot Readiness Monitor" --limit 5

# Download evidence
gh run download <run_id> --name pilot-readiness-monitor-evidence-<sha>
```

### 3. Inspect Failed Check

Read `smoke/results.json` to find details:
```bash
cat evidence/pilot_monitor/*/smoke/results.json | jq '.[] | select(.ok == false)'
```

Example failed check:
```json
{
  "name": "health",
  "method": "GET",
  "url": "https://staging-api.decisionproof.ai/health",
  "expected": "200",
  "actual_status": "503",
  "ok": false,
  "latency_ms": 1523,
  "attempts_used": 3,
  "error": "Expected 200, got 503 after 3 attempts"
}
```

### 4. Rerun After Fix

#### Locally
```bash
PILOT_BASE_URL=https://staging-api.decisionproof.ai \
bash dpp/tools/pilot_readiness_monitor.sh
```

#### CI
```bash
# Re-run failed workflow
gh run rerun <run_id>

# Or trigger new run
gh workflow run "Pilot Readiness Monitor"
```

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PILOT_BASE_URL` | ✅ | - | Base URL of pilot environment |
| `PILOT_TOKEN` | ❌ | - | Bearer token for authenticated endpoints |
| `PASS_THRESHOLD` | ❌ | 0.8 | Minimum pass ratio (0.0-1.0) |
| `RETRIES` | ❌ | 3 | Retry attempts per check |
| `RETRY_SLEEP_SEC` | ❌ | 2 | Sleep seconds between retries |
| `EXPECT_V1_RUNS` | ❌ | 401_or_200 | Expected status for /v1/runs |
| `MODE` | ❌ | ci | Run mode: ci or local |
| `EVENT_NAME` | ❌ | local | Event type for metadata |
| `CHECK_METRICS` | ❌ | 0 | Enable /metrics check (1=on) |

### Repository Secrets/Variables

Set via GitHub UI or `gh` CLI:

**Required**:
- `PILOT_MONITOR_ENABLED` (variable): "1" to enable, "0" to disable

**Recommended**:
- `PILOT_BASE_URL` (secret or variable): Target environment URL
- `PILOT_TOKEN` (secret): Authentication token

## Scheduled Run Details

- **Frequency**: Daily
- **Cron**: `30 16 * * *` (UTC)
- **Local time**: 01:30 KST (next day)
- **Concurrency**: Only one run at a time (cancel in-progress on new trigger)
- **Timeout**: 15 minutes

## Issue Creation

When `open_issue_on_fail` is enabled:
- Monitor creates/updates GitHub issue labeled `pilot-monitor`, `incident`
- Issue title: `[Pilot Monitor] Readiness FAIL - <date_kst>`
- Issue body includes: first failing check, run URL, artifact name
- If open issue exists, adds comment instead of creating new

## Troubleshooting

### Monitor not running on schedule

1. Check `PILOT_MONITOR_ENABLED`:
   ```bash
   gh variable list | grep PILOT_MONITOR_ENABLED
   ```

2. Check workflow file syntax:
   ```bash
   gh workflow view "Pilot Readiness Monitor"
   ```

### All checks failing

1. Verify base URL is reachable:
   ```bash
   curl -I https://staging-api.decisionproof.ai/health
   ```

2. Check network/firewall rules (GitHub Actions runners use specific IP ranges)

3. Review `dump_logs/diagnostics.txt` in evidence

### Authentication failures on /v1/runs

1. Verify token is set:
   ```bash
   gh secret list | grep PILOT_TOKEN
   ```

2. Test token locally:
   ```bash
   curl -H "Authorization: Bearer $PILOT_TOKEN" \
     https://staging-api.decisionproof.ai/v1/runs
   ```

### Evidence artifact not uploaded

1. Check if evidence directory was created:
   - Review workflow logs: "Run Pilot Readiness Monitor" step
   - Look for "Evidence: dpp/evidence/pilot_monitor/..." in output

2. Verify `if-no-files-found: error` didn't trigger:
   - Should only error if `dpp/evidence/pilot_monitor/**` is empty

## Integration with Release Process

The Pilot Readiness Monitor complements RC Gates:
- **RC Gates**: Code quality, tests, Docker builds
- **Pilot Monitor**: Live environment health, customer-facing endpoints

**Recommended workflow**:
1. Merge to master → RC Gates + Pilot Packet CI run
2. Deploy to staging → Manual pilot monitor run
3. Daily schedule monitors for regression
4. Before production deploy → Final pilot monitor PASS required

## Related Documentation

- RC Gates: `dpp/docs/RC_MASTER_CHECKLIST.md`
- Pilot Packet: `dpp/tools/rehearse_customer_packet.sh`
- Release Evidence: `dpp/tools/archive_release_evidence.sh`
