# DEC-P05-FINGERPRINT-HMAC

**Decision**: P5.9 — Kill-switch Audit Fingerprint HMAC(pepper) + Key-ID Prefix
**Status**: Accepted
**Date**: 2026-02-21
**Phase**: Pilot Cutover Security Hardening

---

## Context

DPP's kill-switch audit records store a fingerprint of the actor's admin token to enable
post-hoc identification without exposing the token itself. Before P5.9, `_fingerprint()` used
plain SHA-256 without a secret key:

```python
hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
```

Two security gaps existed:

1. **No pepper (keyed hash)**: SHA-256 without a secret key is a public derivation. An
   adversary with read access to the audit records and a brute-force tool can reverse-map
   a 12-char SHA-256 suffix to the original token if the token space is small or predictable.
   HMAC-SHA256 with a secret pepper prevents this: without the pepper, the fingerprint is
   computationally irreversible.

2. **No key rotation support**: If the fingerprint scheme needs to change (e.g., rotate the
   pepper, upgrade truncation length, change hash algorithm), historical records contain no
   indicator of which scheme was used. Verification becomes ambiguous. A Key-ID (kid) prefix
   solves this by embedding the pepper epoch directly in every fingerprint.

---

## Decision

Replace `_fingerprint(token)` with `fingerprint_token(token)` which computes:

```
HMAC-SHA256(pepper, token)  →  hexdigest[:12]  →  "{kid}:{hexdigest[:12]}"
```

### Format specification

```
fingerprint := kid ":" trunc_hex
kid         := 1*32( ALPHA / DIGIT / "." / "_" / "-" )   ; no colon allowed
trunc_hex   := 12HEXDIG                                   ; lowercase
```

Example output: `kid_202602:3a9f7c1b042e`

### HMAC vs plain SHA-256

| Property | SHA-256 | HMAC-SHA256(pepper) |
|----------|---------|---------------------|
| Adversary can brute-force from record | Yes (if token space small) | No (requires pepper) |
| Requires secret key | No | Yes (pepper) |
| Rotation without re-issuing tokens | Not applicable | Yes (via kid prefix) |
| Deterministic for same inputs | Yes | Yes (given same pepper) |

### Kid prefix

The kid (Key Identifier) prefix records which pepper version generated the fingerprint.
This enables safe key rotation:
- Historical records remain verifiable: use old kid → old pepper for verification
- New records use new kid → new pepper from day of rotation
- kid must change whenever pepper changes (hard rule — see Operational Guidance)

---

## Error Codes

| Code | Condition | Action |
|------|-----------|--------|
| `FINGERPRINT_PEPPER_NOT_SET` | Pepper env var absent when REQUIRED=1 or STRICT=1 | Set pepper in AWS Secrets Manager; inject at runtime |
| `INVALID_FINGERPRINT_KID` | KID contains colon or illegal chars, or is empty/too long | Fix `KILL_SWITCH_AUDIT_FINGERPRINT_KID` env var |
| `KILL_SWITCH_AUDIT_RECORD_BUILD_FAILED` | `fingerprint_token()` raised in admin endpoint step 4 | HTTP 500, state unchanged (fail-closed) |

---

## Configuration Reference

| Env Var | Required? | Valid Values | Default | Notes |
|---------|-----------|--------------|---------|-------|
| `KILL_SWITCH_AUDIT_FINGERPRINT_KID` | No | `[A-Za-z0-9._-]{1,32}`, no colon | `kid_dev` | Must change when pepper changes |
| `KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64` | When REQUIRED/STRICT=1 | Base64-encoded bytes | _(unset)_ | **Preferred** for production |
| `KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER` | When REQUIRED/STRICT=1 | UTF-8 string | _(unset)_ | Fallback / dev; prefer B64 in production |

**Note**: If both B64 and plain are set, B64 takes priority.

---

## Security Properties

### Pepper confidentiality

- The pepper is a secret key. It must be treated with the same level of protection as an
  API signing key.
- **Never commit pepper values to the repository.**
- **Never log pepper bytes or the decoded value.**
- Store in AWS Secrets Manager; inject as env var at pod startup via IRSA + ASCP sidecar.
- Rotate pepper annually or after any suspected compromise.

### Service role policy

The service role (`dpp-api`) must NOT hold permissions to read the Secrets Manager secret
containing the pepper directly from application code in a way that could be leaked.
Use IRSA + ASCP sidecar: the secret is injected as a file/env var at startup, not fetched
at request time.

### Truncation to 12 hex chars

12 hex chars = 6 bytes = 48 bits of the HMAC output. Given a 256-bit pepper and a
non-trivial token, this provides:
- Collision resistance sufficient for an internal audit identifier
- Compact storage in WORM records
- Consistency with the P5.3 fingerprint length

---

## Operational Guidance: Key Rotation

### Step-by-step rotation procedure

1. **Generate new pepper**: `openssl rand -base64 32` → store in Secrets Manager as a new
   secret version.

2. **Choose new kid**: Use `kid_YYYYMM` convention matching the rotation month.
   Example: rotating in March 2026 → `kid_202603`.

3. **Deploy**: Update deployment manifest to set:
   ```
   KILL_SWITCH_AUDIT_FINGERPRINT_KID=kid_202603
   KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64=<new_b64_value>
   ```

4. **Verify**: After deployment, confirm audit records written show the new kid prefix.

5. **Archive old pepper**: Record the mapping `kid_202602 → <old_pepper_secret_arn>` in the
   internal key registry (Notion/Confluence). The old pepper must be kept offline
   (not deleted) for historical verification.

6. **Do NOT re-encrypt historical records**: WORM records are immutable. Historical records
   retain their `kid_202602:...` prefix and can be verified using the archived old pepper.

### Kid naming convention

| Period | kid | Example |
|--------|-----|---------|
| Initial deployment (Feb 2026) | `kid_202602` | `kid_202602:3a9f7c1b042e` |
| Rotation (Mar 2026) | `kid_202603` | `kid_202603:d87a241f6b93` |
| Rotation (Feb 2027) | `kid_202702` | `kid_202702:...` |

**Hard rule**: kid MUST change when pepper changes. Using the same kid with a different pepper
makes historical records unverifiable (you cannot tell which pepper to use for a given record).

### Dev/CI mode (no pepper set)

When neither pepper env var is set AND REQUIRED=0 AND STRICT=0:
- `fingerprint_token()` returns `None`
- Audit record stores `actor.token_fingerprint = null`
- This is acceptable for local development and CI where admin tokens are ephemeral test values
- Never deploy with `fingerprint_token = null` in production-required mode

---

## Fail-Closed Semantics

`fingerprint_token()` raises `RuntimeError("FINGERPRINT_PEPPER_NOT_SET")` when pepper is absent
in REQUIRED or STRICT mode. This propagates from `build_kill_switch_audit_record()` (step 4 of
the admin endpoint) to a catch block in `admin.py` which returns HTTP 500 before reaching step 6
(state mutation). Kill-switch state is guaranteed unchanged.

Flow:
```
POST /admin/kill-switch
  → step 4: build_kill_switch_audit_record()
      → fingerprint_token()
          → _load_pepper()
              → raises RuntimeError("FINGERPRINT_PEPPER_NOT_SET")
          ← RuntimeError propagates
      ← RuntimeError propagates
  → catch RuntimeError in admin.py
  → logger.error("KILL_SWITCH_AUDIT_RECORD_BUILD_FAILED")
  → raise HTTPException(500)
  → state NOT mutated (step 6 never reached)
```

---

## Alternatives Considered

### Alt-1: Keep SHA-256 and add pepper only

**Rejected**: SHA-256(pepper + token) is not HMAC. HMAC provides a stronger construction
(length-extension attack resistant). The overhead is negligible; there is no reason to use
a weaker construction.

### Alt-2: Use a separate key-registry service for kid→pepper lookup

**Rejected**: Adds a runtime dependency on a key management service for every kill-switch
request. The kid prefix in the stored record + offline pepper archive is sufficient for the
audit verification use case without adding availability risk.

### Alt-3: Encrypt the full token instead of HMAC fingerprint

**Rejected**: Encryption is reversible and requires key management for decryption. The audit
purpose is to identify the actor, not to recover the token. A one-way HMAC fingerprint is
the minimal necessary disclosure; full token storage would violate the principle of minimum
necessary data.

---

## Acceptance Criteria

- [x] RC-10.P5.9 gate: 5/5 PASSED
- [x] `fingerprint_token()` format is `<kid>:<12 lowercase hex chars>`
- [x] Deterministic: same token + same pepper → same fingerprint
- [x] Pepper-sensitive: different pepper → different fingerprint
- [x] `FINGERPRINT_PEPPER_NOT_SET` raised when REQUIRED=1 or STRICT=1 and pepper absent
- [x] `INVALID_FINGERPRINT_KID` raised for malformed kid (colon, wrong chars, wrong length)
- [x] Raw token never appears in `build_kill_switch_audit_record()` output
- [x] `admin.py` catches RuntimeError from step 4 and returns HTTP 500 fail-closed
- [x] Dev/CI mode: no pepper → `fingerprint_token()` returns `None` (no crash)
- [x] HMAC uses `hmac.new(pepper_bytes, token_bytes, hashlib.sha256)` — not SHA-256 alone

---

## References

- `dpp/apps/api/dpp_api/audit/kill_switch_audit.py` — P5.9 implementation
- `dpp/apps/api/dpp_api/routers/admin.py` — Step 4 RuntimeError catch
- `dpp/apps/api/tests/test_rc10_p59_fingerprint_hmac_kid.py` — RC-10.P5.9 test gate
- `dpp/ops/runbooks/kill_switch_audit_break_glass_alerts.md` — Break-glass procedures
- `dpp/docs/decisions/DEC-P05-LOG-MASKING-WORM.md` — P5.3 original fingerprint design
- `dpp/docs/decisions/DEC-P05-WORM-MODE.md` — P5.8 WORM mode design
