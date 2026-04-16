# DEC-P05-TLS-DRIFT

**Decision**: P5.5 TLS / DB URL Drift Guard — Negative RC Gate for sslmode=disable
**Status**: Accepted
**Date**: 2026-02-21
**Phase**: Pilot Cutover Security Hardening

---

## Context

DPP connects to PostgreSQL (Supabase Pooler) over TLS. The production and staging
databases enforce `sslmode=require` or `sslmode=verify-full`. Locally and in CI,
`sslmode=disable` is used for ephemeral containers where no real certificate exists.

The risk is **config drift**: a developer copies a working CI database URL
(which contains `sslmode=disable`) verbatim into a Kubernetes manifest or
infrastructure template that targets staging or production. This disables TLS
server-certificate verification silently — the connection succeeds, no error is
raised, but the system is now vulnerable to man-in-the-middle attacks.

This drift is hard to detect by code review because the manifest looks "valid" and
the application behaves correctly. It can persist undetected for months.

---

## Decision

Add a deterministic negative-scan RC Gate (RC-12) that:

1. **Scans a tight, explicit scope** of staging/prod deployment assets:
   - `k8s/**/*.yml, k8s/**/*.yaml` — Kubernetes manifests
   - `infra/**/*.tf, infra/**/*.json, infra/**/*.yml, infra/**/*.yaml` — IaC
   - `ops/scripts/**/*.sh, *.template, *.sql` — Operational scripts

2. **Explicitly excludes** files where `sslmode=disable` is legitimate:
   - `evidence/**` — CI evidence archives (contain test output verbatim)
   - `**/.git/**` — Git internals
   - `.github/workflows/**` — CI-only workflows (ephemeral Postgres containers)
   - `**/*.md` — Documentation (explanatory examples)
   - `infra/docker-compose.yml` — Local-dev container definition

3. **Returns exit 2** when any hit is found, blocking the RC gate pipeline
   (Fail-Fast, Stop-Rule: P0 failure halts all subsequent gates).

4. **Prints only safe metadata** to logs: file path, line number, and the constant
   string `"sslmode=disable"` as the excerpt. Full line content — which may contain
   `DATABASE_URL` credentials — is never stored in reports, evidence, or stdout.

5. **Avoids regex** for the core detection (`"sslmode=disable" in normalize_line(s)`),
   eliminating ReDoS-class risk entirely. Detection is O(line length), bounded and
   deterministic.

### Normalization strategy

```python
def normalize_line(s: str) -> str:
    s = s.lower()                             # case-insensitive
    s = s.replace("'", "").replace('"', "").replace("`", "")  # strip quotes
    s = " ".join(s.split())                  # collapse whitespace
    s = s.replace(" = ", "=").replace(" =", "=").replace("= ", "=")
    return s
```

This catches `sslmode=disable`, `sslmode = disable`, `"sslmode=disable"`, etc.

---

## Alternatives Considered

### Alt-1: Scan all files including docs (rejected)

**Rejected**: `ops/runbooks/*.md` documents legitimately mention `sslmode=disable`
as a forbidden example. Scanning them produces false positives that permanently
suppress the gate's signal value. Developers learn to ignore CI failures.

### Alt-2: Scan everything except `.git/` (rejected)

**Rejected**: The DPP codebase has Python test files that use `sslmode=disable` for
in-process ephemeral test databases (`TEST_DATABASE_URL`). Including them in the scan
would produce permanent false positives on legitimate test infrastructure.
Tight scope = zero false positives = gate that developers trust.

### Alt-3: Regex-based detection (rejected)

**Rejected**: The OWASP ReDoS guidance warns against applying unreviewed regex to
arbitrary file content. A regex like `sslmode\s*=\s*disable` applied to large files
from untrusted sources (e.g., vendored Helm charts) carries backtracking risk.
Simple substring matching after whitespace normalisation is equally expressive
for this use case and carries zero ReDoS risk.

### Alt-4: Grep in CI shell step only (rejected)

**Rejected**: A bare `grep -r sslmode=disable k8s/` in a shell step is not testable,
not reusable, and produces no structured evidence. The Python tool provides a typed
JSON report, deterministic exit codes, and a local pytest-runnable test that can be
iterated without pushing to CI.

---

## Acceptance Criteria

- [x] RC-12 `test_t1_repo_scan_has_no_sslmode_disable_in_scope` passes on clean tree.
- [x] RC-12 `test_t2_fixture_with_disable_fails` detects the synthetic hit correctly.
- [x] No secrets or credential fragments printed in any path (stdout, report, evidence).
- [x] Gate is integrated into `run_rc_gates.sh`, `run_rc_gates.ps1`, and CI `rc_gates.yml`.
- [x] Scan completes in < 2 s on the current repository size (actual: ~1 s).

---

## Consequences

**Positive**:
- `sslmode=disable` in any k8s manifest or ops script is caught before merge.
- Evidence JSON report provides structured audit trail for security review.
- Zero false positives on current tree (confirmed: 29 files scanned, 0 hits).
- No external dependencies — stdlib only, runs anywhere Python ≥ 3.10 is available.

**Negative / Trade-offs**:
- Scope must be maintained if new deployment asset directories are introduced.
  Adding a new `staging/` directory outside `k8s/`, `infra/`, or `ops/scripts/`
  requires updating `DEFAULT_INCLUDE_GLOBS` in `tls_drift_gate.py` and RC-12.
- The gate does not verify that TLS is **correctly** configured (e.g., that
  `verify-full` is used rather than `require`). That enforcement belongs to the
  runtime `db_url_verify_full_check.py` tool and the app's SSL policy module.

---

## References

- `dpp/tools/security/tls_drift_gate.py` — scanner implementation
- `dpp/apps/api/tests/test_rc12_tls_drift_gate.py` — RC-12 test gate
- `dpp/apps/api/dpp_api/db/ssl_policy.py` — runtime SSL enforcement
- `dpp/tools/security/db_url_verify_full_check.py` — DB URL TLS verifier
- `dpp/tools/README_RC_GATES.md` — RC-12 test documentation
