# Phase 4 Toss Isolation — Pilot Deployment Runbook

**Scope**: v1.0 PayPal-only beta (pilot env)
**Decision**: Option 1 adopted — pilot runs the SAME fail-fast PayPal preflight as production.
**Status**: COMPLETE — `DPP_BILLING_PREFLIGHT_REQUIRED=1` active in pilot overlay.

---

## What Phase 4 changed

| Item | Before | After |
|------|--------|-------|
| `DPP_BILLING_PREFLIGHT_REQUIRED` (pilot) | `"0"` (graceful degradation) | `"1"` (fail-fast, same as prod) |
| Toss in startup preflight | Included — would fail if secrets absent | Excluded (dormant) |
| Toss in SecretProviderClass | Required entries present | Removed from `secretproviderclass-dpp-secrets-pilot.yaml` |
| Toss env in Deployment | `secretKeyRef` (would block pod start) | `optional: true` in base + literal `""` override in pilot patch |
| Pod start behaviour when Toss secrets absent | CrashLoopBackOff | Boots normally — Toss keys are empty strings |

---

## Deploy steps (pilot)

```bash
# 1. Apply overlay (includes secretproviderclass, configmap, deployment patch)
kubectl apply -k k8s/overlays/pilot/

# 2. Force new pods to pick up latest Secrets Manager values
kubectl rollout restart deployment/dpp-api -n dpp-pilot

# 3. Wait for rollout to complete (3 replicas)
kubectl rollout status deployment/dpp-api -n dpp-pilot

# 4. Verify readyz on any running pod
POD=$(kubectl get pods -n dpp-pilot -l app=dpp-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n dpp-pilot "$POD" -- \
  python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/readyz', timeout=35)
import sys; data = json.loads(r.read()); print(json.dumps(data, indent=2))
"
```

---

## Expected `/readyz` output (pilot normal state)

```json
{
  "status": "ready",
  "version": "0.4.2.4",
  "services": {
    "api": "up",
    "database": "up",
    "redis": "up",
    "s3": "up",
    "sqs": "up",
    "billing_secrets": "up"
  }
}
```

`billing_secrets: "up"` reflects the billing preflight cache result `{"paypal": "ok"}`.

**Pilot target state: `billing_preflight.paypal == "ok"`**

`"status": "skipped"` is the pre-startup sentinel returned by `get_billing_preflight_status()`
BEFORE `run_billing_secrets_active_preflight()` has run. It is NOT a valid steady-state for pilot.

---

## Startup behaviour (REQUIRED=1)

On pod startup, `main.py` calls `run_billing_secrets_active_preflight()`:

1. Calls PayPal OAuth token endpoint (`/v1/oauth2/token`) with `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`.
2. If HTTP 200 + `access_token` present → caches `{"paypal": "ok"}`.
3. If check fails and `REQUIRED=1` → raises `RuntimeError("BILLING_SECRET_PREFLIGHT_FAILED:...")` → pod crashes (CrashLoopBackOff).
4. Toss check is **not called** — `_check_toss()` is dormant (Phase 4).

This matches production behaviour exactly (Option 1).

---

## REQUIRED=0 fallback path (NOT the pilot startup path)

`REQUIRED=0` is a development/CI escape hatch:
- PayPal failure → logs CRITICAL + caches `{"paypal": "err:..."}` instead of raising.
- Pod boots but readyz returns `billing_secrets: "down: paypal:err:..."`.

This path is covered by `test_phase4_paypal_only_preflight.py::test_required_0_degrades_gracefully_on_paypal_failure`
for code-branch coverage only. **It is not the pilot startup behaviour.**

---

## Toss reactivation (future, out of Phase 4 scope)

To reactivate Toss as a payment provider:
1. Add real `TOSS_SECRET_KEY` / `TOSS_WEBHOOK_SECRET` values to `pilot_secret_values.env`.
2. Run `create_pilot_secret.ps1` to update Secrets Manager.
3. Re-add `toss-secret-key` / `toss-webhook-secret` entries to `secretproviderclass-dpp-secrets-pilot.yaml`.
4. Remove literal `value: ""` overrides from `patch-api-deployment-pilot.yaml`.
5. Remove `optional: true` from Toss entries in `k8s/base/api-deployment.yaml`.
6. Re-enable `_check_toss()` call in `billing/active_preflight.py`.

---

## Resolved blockers (Phase 4 completion)

| ID | Item | Status |
|----|------|--------|
| B-05 | `beta_private_starter_v1` plan seed (`20260416_03_seed_plans.sql`) | ✅ Executed |
| B-06 | PayPal webhook registration (`PAYPAL_WEBHOOK_ID=3A3743299X630462M`) | ✅ Complete |

No residual Phase 4 human-action blockers remain.

---

*Last updated: 2026-04-16 | Phase 4 Option 1 — COMPLETE*
