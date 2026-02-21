# Runbook: Pilot Cutover Run (P6.4)

**Version**: 1.0
**Phase**: 6.4 — Cutover Runbook + Human DB Rollback Verification + WORM Evidence Sealing
**Last Updated**: 2026-02-21
**Owner**: Ops / SRE Lead

---

## Purpose

This runbook defines the **single authoritative execution order** for DPP pilot cutover.
It is executed once per deployment cycle and produces a tamper-evident evidence archive
sealed to an S3 Object Lock (WORM) bucket.

Automation boundary:
- **Automated** (agent): RC Gates, Staging dry-run, App rollback drill, tar/sha256/S3 upload
- **Human only**: DB PITR/restore verification (see `db_rollback_verification_human.md`)

---

## Pre-conditions (Operator Checklist)

Before running the cutover script:

```
[ ] 1. Branch is phase6-preflight (or main if merging post-P6.4)
[ ] 2. All Phase 6.1-6.3 commits are on branch (git log --oneline -5)
[ ] 3. AWS CLI configured: export AWS_PROFILE=dpp-admin
[ ] 4. kubectl context set to STAGING (not prod):
        kubectl config current-context
[ ] 5. S3 WORM bucket exists with Object Lock + versioning enabled:
        aws s3api head-bucket --bucket $DPP_WORM_BUCKET
[ ] 6. WORM env vars exported (see §Required ENV below)
[ ] 7. Staging env vars exported (DPP_STAGING_BASE_URL, DPP_K8S_NAMESPACE)
[ ] 8. No unstaged changes (git status --porcelain should be empty)
```

---

## Required ENV

Set these in your shell before running. **Never commit these values.**

```bash
# AWS / WORM
export AWS_PROFILE="dpp-admin"
export AWS_REGION="ap-northeast-2"          # or your region
export DPP_WORM_BUCKET="dpp-audit-worm-<env>"
export DPP_WORM_PREFIX="evidence/cutover"
export DPP_WORM_OBJECT_LOCK_MODE="GOVERNANCE"   # pilot uses GOVERNANCE
export DPP_WORM_RETENTION_DAYS="365"

# Staging
export DPP_STAGING_BASE_URL="https://staging.api.dpp.example.com"
export DPP_K8S_NAMESPACE="dpp-staging"

# Optional: override RC runner
# export DPP_RC_RUNNER="dpp/tools/rc_double_then_ci_then_archive.sh"
```

---

## Execution Order (One-shot)

### Run 1: Steps 1-3 (automated) + exit 2 (human DB gate)

```bash
cd /path/to/decisionwise_api_platform
./dpp/tools/pilot_cutover_run.sh
```

Expected output:
```
==> Evidence dir: dpp/evidence/phase6_4_cutover/20260221T120000Z/
[12:00:01Z] Step 0: Capturing scope...
[12:00:01Z] ✓ Scope written
[12:00:01Z] Step 1: Running RC Gates...
[12:00:45Z] ✓ Step 1: RC Gates PASS
[12:00:45Z] Step 2: Staging dry-run + P4.3 data isolation...
[12:01:30Z] ✓ Step 2: Staging dry-run PASS
[12:01:30Z] Step 3: App rollback drill...
[12:02:00Z] ✓ Step 3: App rollback drill PASS
[12:02:00Z] Writing DB rollback checkpoint template...

╔══════════════════════════════════════════════════════════════════╗
║  [PAUSE — HUMAN STEP REQUIRED]                                   ║
║  DB Rollback Verification is PENDING (exit code 2).             ║
...
╚══════════════════════════════════════════════════════════════════╝
```

Exit code 2 = normal pause for human DB verification.

---

### Human Step: DB Rollback Verification

Follow: `dpp/ops/runbooks/db_rollback_verification_human.md`

After completing:
1. Edit the checkpoint file:
   `dpp/evidence/phase6_4_cutover/<TS>/55_db_rollback_human_checkpoint.txt`
2. Change `Status: PENDING` → `Status: COMPLETED`
3. Check all boxes `[ ]` → `[x]`

---

### Run 2: Steps 4-5 (seal + WORM upload)

```bash
EVIDENCE_DIR=dpp/evidence/phase6_4_cutover/<TS> \
./dpp/tools/pilot_cutover_run.sh --seal-only
```

Expected output:
```
==> [seal-only] Resuming evidence dir: dpp/evidence/phase6_4_cutover/20260221T120000Z/
[12:05:00Z] ✓ DB rollback checkpoint: COMPLETED
[12:05:00Z] Steps 4-5: Sealing evidence and uploading to WORM...
[seal 12:05:01Z] Step 4a: Creating tarball...
[seal 12:05:02Z]   Tarball: .../evidence.tar.gz (24K)
[seal 12:05:02Z] Step 4b: Computing SHA-256...
[seal 12:05:02Z]   SHA-256: a3f8c2d1...
[seal 12:05:02Z] Step 4c: Computing retain-until (365 days from now)...
[seal 12:05:03Z] Step 5a: Uploading to S3 WORM bucket...
[seal 12:05:05Z]   Upload complete — VersionId=xxxx  ETag=yyyy
[seal 12:05:05Z] Step 5b: Verifying via head-object...
[seal 12:05:06Z]   Verified — VersionId=xxxx  LockMode=GOVERNANCE
[seal 12:05:06Z] Sealing COMPLETE.

Phase 6.4 — Pilot Cutover Run — RESULT SUMMARY
================================================
AC1: RC Gates                         : PASS
AC2: Staging dry-run + P4.3 isolation : PASS
...
VERDICT: PASS
```

---

## Evidence Structure

Each run produces evidence in a timestamped directory:

```
dpp/evidence/phase6_4_cutover/
└── 20260221T120000Z/
    ├── 00_scope.txt                    — git HEAD, branch, env snapshot
    ├── 30_rc_gates.txt                 — full RC gates output
    ├── 40_staging_dry_run.txt          — staging health + P4.3 isolation
    ├── 50_rollback_drill.txt           — app rollback results
    ├── 55_db_rollback_human_checkpoint.txt  — HUMAN sign-off
    ├── 56_db_restore_screenshots/      — HUMAN uploads screenshots here
    ├── 60_evidence_seal_manifest.json  — sha256, s3_key, version_id, retain_until
    ├── 70_worm_upload_stdout.txt       — put-object + head-object raw output
    ├── 90_result_summary.txt           — AC1-AC5 PASS/FAIL
    ├── evidence.tar.gz                 — sealed tarball (uploaded to WORM)
    └── evidence.tar.gz.sha256          — SHA-256 of tarball
```

> Note: `dpp/evidence/` is gitignored. Evidence lives on disk / in WORM S3 only.

---

## Failure Scenarios

### Step 1 fails (RC Gates)
- Exit code 1 — check `30_rc_gates.txt`
- Fix the failing gate, then re-run from scratch (new timestamp)

### Step 2 fails (Staging)
- Exit code 1 — check `40_staging_dry_run.txt`
- If P4.3: staging configmap/SPC references prod — fix before proceeding

### Step 3b: exit 2 (normal — human DB gate)
- Not a failure — expected behavior
- Complete human DB verification, then re-run with `--seal-only`

### Step 5 fails (S3 upload)
- Check `70_worm_upload_stdout.txt`
- Verify bucket name, region, IAM permissions (s3:PutObject + s3:GetObjectVersion)
- Re-run with `--seal-only` (same EVIDENCE_DIR) after fixing

---

## Sign-off

| Role | Name | Date (UTC) | Signature |
|---|---|---|---|
| Operator (automated run) | Agent | 2026-02-21 | automated |
| DB Restore Verifier | *(human)* | | |
| SRE Lead | *(human)* | | |
| Engineering Lead | *(human)* | | |

---

*Generated: 2026-02-21 | DPP v0.4.2.2 | Phase 6.4*
