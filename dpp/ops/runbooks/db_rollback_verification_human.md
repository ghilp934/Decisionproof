# Runbook: DB Rollback Verification — HUMAN STEP (P6.4)

**Version**: 1.0
**Phase**: 6.4
**Classification**: HUMAN ONLY — this runbook must NOT be automated by any agent

---

> ## ⚠️ HUMAN ONLY
>
> This runbook describes a **DB restore/PITR procedure** that must be performed
> **manually by a human operator**. No agent or automation system may claim to
> have performed or completed these steps on behalf of a human.
>
> After completing all steps, sign off in:
> `dpp/evidence/phase6_4_cutover/<TS>/55_db_rollback_human_checkpoint.txt`

---

## Purpose

Verify that:
1. The database can be successfully restored to a point-in-time before cutover
2. Critical data integrity is preserved after restore
3. The restored DB is isolated from production (no data cross-contamination)

This is the **DB component of the rollback drill** (P4.5).

---

## Section A: Supabase (Recommended for DPP)

### A.1 "Restore to a New Project" (Recommended for Drill)

This method restores a full backup to a brand-new Supabase project, leaving
production untouched.

**Steps:**

1. Log in to [app.supabase.com](https://app.supabase.com) as an admin
2. Navigate to **your production project** → Settings → Database → Backups
3. Select the most recent backup before the cutover timestamp
4. Click **"Restore to a New Project"**
   - Project name: `dpp-rollback-drill-<YYYYMMDD>` (never reuse names)
   - Region: same as production
   - Plan: same tier as production
5. Wait for restore to complete (typically 5–15 minutes for small DBs)
6. Once green, note the new project URL and connection string

> **Security**: The new project will have its own credentials.
> Never connect with production credentials.

**Evidence to capture:**

```
Screenshot 1: Backup selection screen (show timestamp selected)
Screenshot 2: Restore-to-new-project confirmation dialog
Screenshot 3: New project dashboard showing "Active" status
```

Save to: `dpp/evidence/phase6_4_cutover/<TS>/56_db_restore_screenshots/`

---

### A.2 PITR — Point-in-Time Recovery (Supabase Pro/Team)

For granular recovery to a specific second:

1. Navigate to **production project** → Settings → Database → Point in Time Recovery
2. Select the recovery timestamp (must be BEFORE the event being rolled back)
3. Choose target: **new project** (never overwrite production during a drill)
4. Confirm and wait

> **Note**: PITR requires Supabase Pro or Team plan. PITR is available for the
> last 7 days (Pro) or 30 days (Team).

---

### A.3 Smoke Verification Queries (Supabase)

Connect to the **restored project only** using the Table Editor or psql:

```sql
-- Query 1: Tenant integrity
SELECT COUNT(*) AS tenant_count FROM tenants;
-- Expected: > 0 and matches production count at recovery time

-- Query 2: API key integrity
SELECT COUNT(*) AS api_key_count FROM api_keys;

-- Query 3: Billing events spot check
SELECT id, provider, event_type, created_at
FROM billing_events
ORDER BY created_at DESC
LIMIT 5;

-- Query 4: Webhook dedup events (P6.3 table)
SELECT status, COUNT(*)
FROM webhook_dedup_events
GROUP BY status;

-- Query 5: CRITICAL — Confirm you are NOT on production
SELECT current_database(), current_user, inet_server_addr();
-- Result must NOT match production DB name/user
```

Record each result in the checkpoint file.

---

## Section B: AWS RDS / Aurora (Alternative)

### B.1 Automated Snapshot Restore

```bash
# List available snapshots
aws rds describe-db-snapshots \
    --db-instance-identifier dpp-production \
    --query 'DBSnapshots[*].[DBSnapshotIdentifier,SnapshotCreateTime,Status]' \
    --output table \
    --profile dpp-admin

# Restore to a NEW DB instance (never overwrite production)
aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier dpp-rollback-drill-$(date +%Y%m%d) \
    --db-snapshot-identifier <snapshot-id> \
    --db-instance-class db.t3.micro \
    --no-multi-az \
    --profile dpp-admin
```

> Wait for `DBInstanceStatus: available` before connecting.

### B.2 PITR (RDS Point-in-Time Recovery)

```bash
# Restore to specific UTC timestamp
aws rds restore-db-instance-to-point-in-time \
    --source-db-instance-identifier dpp-production \
    --target-db-instance-identifier dpp-rollback-drill-$(date +%Y%m%d) \
    --restore-time "2026-02-21T11:00:00Z" \
    --db-instance-class db.t3.micro \
    --no-multi-az \
    --profile dpp-admin
```

**Evidence to capture:**

```
Screenshot 1: RDS console showing snapshot or PITR target selection
Screenshot 2: New DB instance "available" status
```

Save to: `dpp/evidence/phase6_4_cutover/<TS>/56_db_restore_screenshots/`

---

### B.3 Smoke Verification (RDS)

```bash
# Connect to RESTORED instance only (use new endpoint)
PGPASSWORD=<restored-password> psql \
    -h <restored-endpoint> \
    -U dpp_user \
    -d dpp \
    -c "SELECT COUNT(*) FROM tenants;"

# CRITICAL: verify you are NOT on production
PGPASSWORD=<restored-password> psql \
    -h <restored-endpoint> \
    -U dpp_user \
    -d dpp \
    -c "SELECT inet_server_addr(), current_database();"
```

---

## Section C: Common Verification Checklist

Regardless of DB type, verify ALL of the following:

### C.1 Evidence File Locations

| Evidence Item | File Path |
|---|---|
| Restore screenshots | `<TS>/56_db_restore_screenshots/` |
| Smoke query results | `<TS>/55_db_rollback_human_checkpoint.txt` |
| PITR timestamp selected | `<TS>/55_db_rollback_human_checkpoint.txt` |

### C.2 Cleanup After Drill

```bash
# After verifying, IMMEDIATELY delete the restored instance
# to avoid additional costs and data exposure

# Supabase: Settings → General → Danger Zone → Delete Project
# RDS:
aws rds delete-db-instance \
    --db-instance-identifier dpp-rollback-drill-$(date +%Y%m%d) \
    --skip-final-snapshot \
    --profile dpp-admin
```

---

## Section D: Sign-off Template

Copy this into `55_db_rollback_human_checkpoint.txt` when complete:

```
Status: COMPLETED

Completed by: [full name]
Date (UTC): [YYYY-MM-DDThh:mm:ssZ]
Recovery method: [Supabase restore-to-new-project / PITR / RDS snapshot / PITR]
Recovery point selected (UTC): [timestamp]
Target DB/Project: [name — not production]
Smoke queries: ALL PASSED
Cleanup (restored DB deleted): YES / NO (if NO, explain)

Evidence screenshots:
  56_db_restore_screenshots/01_backup_selection.png
  56_db_restore_screenshots/02_restore_complete.png
  56_db_restore_screenshots/03_smoke_queries.png
```

---

## Security Constraints

- **Never** restore to the production DB (always restore to a new project/instance)
- **Never** expose connection strings in evidence files (use screenshots, not CLI output)
- **Never** leave the restored DB running after the drill (delete within 2 hours)
- **Never** share restored DB credentials in Slack/email/tickets

---

*Generated: 2026-02-21 | DPP v0.4.2.2 | Phase 6.4*
