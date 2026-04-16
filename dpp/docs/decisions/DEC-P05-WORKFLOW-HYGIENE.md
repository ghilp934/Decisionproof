# DEC-P05-WORKFLOW-HYGIENE

**Decision**: P5.4 Workflow Hygiene — Ephemeral Per-Run Password in Public CI
**Status**: Accepted
**Date**: 2026-02-21
**Phase**: Pilot Cutover Security Hardening

---

## Context

The Phase 4.5 DB rollback drill workflow (`.github/workflows/phase45_db_rollback_drill.yml`)
used a static literal `drillpw2026` as the Postgres service container password. This string
was stored in plaintext in a public GitHub repository, violating the principle that no
credential — even a throwaway CI password — should be hardcoded in source code.

Although the container is ephemeral (destroyed after each workflow run) and the password
grants access only to a local `dpp_drill` database with no production data, the presence
of any password-like literal in public source creates a pattern that:

1. Fails automated secrets-scanning tools (GitHub Secret Scanning, truffleHog, etc.).
2. Establishes a bad precedent that developers may copy for more sensitive credentials.
3. Provides no audit trail of which run used which credential.

---

## Decision

Replace the static literal with an **ephemeral per-run password** derived from GitHub's
built-in `run_id` and `run_attempt` context variables:

```
drill-${{ github.run_id }}-${{ github.run_attempt }}
```

This expression is evaluated by GitHub Actions at runtime. It is:
- **Unique per run**: `run_id` is a monotonically increasing integer per repository.
- **Unique per retry**: `run_attempt` distinguishes re-runs of the same `run_id`.
- **Never static**: Cannot be guessed or extracted from the source file alone.
- **Self-documenting**: The prefix `drill-` makes clear this is a drill-scope credential.

### Why `run_id` + `run_attempt` is sufficient for CI

The Postgres service container exists only for the duration of a single GitHub Actions job.
The container is not network-accessible outside the runner, and it is destroyed when the job
completes. A password that is unique-per-run provides stronger isolation than a static literal
while adding zero operational overhead (no secrets rotation, no Vault, no SSM lookup).

### Masking

The ephemeral password is masked via `echo "::add-mask::$DRILL_PG_PASSWORD"` before it is
used. This prevents the password from appearing in GitHub Actions log output, even if a step
accidentally echoes the `$LOCAL_DB_URL` that contains it.

### Services constraint

GitHub Actions does not expose the `env` context inside `services.*.env` blocks. Therefore,
the same expression is duplicated in both the job-level `env` block and the service
definition. This duplication is intentional and correct; DRY does not apply here because
correctness requires the service to receive the value at container-start time.

---

## Consequences

**Positive**:
- No static password-like literal in public repository.
- Automated secrets scanners will not flag the workflow.
- Each CI run has a demonstrably unique, non-reusable credential.
- Password is masked in logs; no leakage even on accidental `echo`.

**Negative / Trade-offs**:
- Minor YAML duplication: the expression appears twice (job env + service env).
- The password is still deterministic if an attacker knows the `run_id` and `run_attempt`.
  This is acceptable because: (a) the container is not externally reachable, and (b) the
  password is masked in logs making correlation harder.

---

## References

- `.github/workflows/phase45_db_rollback_drill.yml` — patched workflow
- `dpp/apps/api/tests/test_rc11_workflow_hygiene_ephemeral_password.py` — RC-11 regression gate
- `dpp/tools/README_RC_GATES.md` — RC-11 test documentation
