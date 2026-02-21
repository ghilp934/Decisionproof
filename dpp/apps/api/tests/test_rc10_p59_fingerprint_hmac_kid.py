"""RC-10.P5.9 Contract Gate: Kill-switch audit fingerprint HMAC(pepper) + Key-ID prefix.

Ensures the kill-switch audit sink stores actor_token fingerprint as:
  "<KID>:<TRUNC_HEX>"
where TRUNC_HEX is a truncated HMAC-SHA256 digest with a secret pepper.
This enables safe key rotation without breaking historical record verification.

Test matrix:
  T1 – fingerprint_token() format is exactly <kid>:<12 lowercase hex chars>
  T2 – HMAC is deterministic (same inputs → same output) and pepper-sensitive
  T3 – Missing pepper with REQUIRED=1 or STRICT=1 → RuntimeError(FINGERPRINT_PEPPER_NOT_SET)
       Kill-switch state unchanged — fail-closed semantics preserved.
  T3b – STRICT=1 path also fails with same deterministic error code.
  T4 – Audit record JSON contains NO raw token string; fingerprint field is present and safe.

Facts (documented in DEC-P05-FINGERPRINT-HMAC.md):
  - HMAC pepper must never be logged or committed to repo; inject at runtime.
  - kid prefix enables key rotation: historical records stay verifiable with old pepper.
  - kid must change when pepper changes; kid naming convention: "kid_YYYYMM".
  - TRUNC_LEN = 12 hex chars (consistent with prior P5.3 fingerprint length).
"""

import hashlib
import hmac
import json
import os
import re
from typing import Generator

import pytest

from dpp_api.audit.kill_switch_audit import (
    build_kill_switch_audit_record,
    fingerprint_token,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_FP_ENV_KEYS = [
    "KILL_SWITCH_AUDIT_FINGERPRINT_KID",
    "KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER",
    "KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64",
    "KILL_SWITCH_AUDIT_REQUIRED",
    "KILL_SWITCH_AUDIT_STRICT",
]

_TRUNC_LEN = 12  # must match kill_switch_audit._TRUNC_LEN


class TestRC10P59FingerprintHmacKid:
    """RC-10.P5.9: HMAC fingerprint with Key-ID prefix hardening."""

    @pytest.fixture(autouse=True)
    def restore_env(self) -> Generator:
        """Snapshot and restore fingerprint-related env vars after each test."""
        snapshot = {k: os.environ.get(k) for k in _FP_ENV_KEYS}
        yield
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ── T1: Format verification ────────────────────────────────────────────────

    def test_t1_fingerprint_format_includes_kid_prefix(self) -> None:
        """T1: fingerprint_token() returns exactly '<kid>:<12 lowercase hex chars>'.

        AWS documentation and S3 WORM audit requirements demand the fingerprint
        identifies the key rotation epoch (kid) so historical records remain
        verifiable after pepper rotation.  The 12-char hex suffix is compact but
        collision-resistant enough for an internal audit identifier.
        """
        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_KID"] = "kid_test"
        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER"] = "pepper_test"

        result = fingerprint_token("tok_live_ABC123")

        assert result is not None, (
            "fingerprint_token must return a string (not None) when pepper is configured"
        )
        assert result.startswith("kid_test:"), (
            f"Fingerprint must start with 'kid_test:', got {result!r}"
        )

        suffix = result[len("kid_test:"):]
        assert len(suffix) == _TRUNC_LEN, (
            f"Fingerprint suffix must be exactly {_TRUNC_LEN} hex chars, "
            f"got len={len(suffix)}: {suffix!r}"
        )
        assert re.fullmatch(r"[0-9a-f]+", suffix), (
            f"Fingerprint suffix must be lowercase hex only, got {suffix!r}"
        )

        # Full pattern assertion (belt-and-suspenders)
        assert re.fullmatch(rf"kid_test:[0-9a-f]{{{_TRUNC_LEN}}}", result), (
            f"Full fingerprint must match 'kid_test:[0-9a-f]{{12}}', got {result!r}"
        )

    # ── T2: Determinism + pepper sensitivity ─────────────────────────────────

    def test_t2_fingerprint_deterministic_and_pepper_sensitive(self) -> None:
        """T2: Same token+pepper → identical fingerprint; different pepper → different result.

        Determinism is required for idempotent re-computation of fingerprints.
        Pepper sensitivity is required for the HMAC to be meaningful — if the
        fingerprint were pepper-invariant, the secret key would provide no security.

        The expected values are computed via Python stdlib hmac (independent verification).
        """
        token = "tok_live_SAME_TOKEN_FOR_DETERMINISM"
        pepper_a = "pepper_alpha_2026"
        pepper_b = "pepper_beta_2026"
        kid = "kid_t2"

        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_KID"] = kid

        # Compute expected HMAC values independently (spec-level cross-check)
        expected_a = hmac.new(
            pepper_a.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:_TRUNC_LEN]

        expected_b = hmac.new(
            pepper_b.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:_TRUNC_LEN]

        # Run with pepper_a (twice — must be identical)
        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER"] = pepper_a
        result_a1 = fingerprint_token(token)
        result_a2 = fingerprint_token(token)  # second call must be identical

        # Run with pepper_b
        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER"] = pepper_b
        result_b = fingerprint_token(token)

        # Determinism: two calls with same pepper → same result
        assert result_a1 == result_a2, (
            f"fingerprint_token must be deterministic. "
            f"Call 1: {result_a1!r}, Call 2: {result_a2!r}"
        )

        # Exact match with independently computed HMAC
        assert result_a1 == f"{kid}:{expected_a}", (
            f"Expected {kid}:{expected_a!r} (HMAC-SHA256 with pepper_a), "
            f"got {result_a1!r}"
        )

        # Pepper sensitivity: pepper_b must produce a different fingerprint
        assert result_a1 != result_b, (
            "Different peppers must produce different fingerprints — "
            "got identical results, which means the pepper is NOT being applied. "
            "This is a critical security defect: fingerprints would be identical "
            "before and after key rotation."
        )

        # Verify pepper_b result is also correct
        assert result_b == f"{kid}:{expected_b}", (
            f"Expected {kid}:{expected_b!r} (HMAC-SHA256 with pepper_b), "
            f"got {result_b!r}"
        )

    # ── T3: Missing pepper → fail-closed ──────────────────────────────────────

    def test_t3_missing_pepper_with_required_raises_runtime_error(self) -> None:
        """T3: REQUIRED=1 + no pepper configured → RuntimeError(FINGERPRINT_PEPPER_NOT_SET).

        In production-required mode, a missing pepper is a hard misconfiguration:
        the fingerprint would be None, meaning audit records would store no
        actor identity trace.  Fail-closed is the only safe behaviour.

        Kill-switch state is unchanged because fingerprint_token() (called inside
        build_kill_switch_audit_record(), which runs in step 4 of the admin endpoint
        — before step 6 state mutation) raises before state is mutated.
        """
        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ.pop("KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER", None)
        os.environ.pop("KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64", None)

        with pytest.raises(RuntimeError) as exc_info:
            fingerprint_token("tok_admin_test")

        assert "FINGERPRINT_PEPPER_NOT_SET" in str(exc_info.value), (
            f"RuntimeError must contain 'FINGERPRINT_PEPPER_NOT_SET' for operator diagnostics. "
            f"Got: {exc_info.value!r}"
        )

    def test_t3b_missing_pepper_with_strict_raises_runtime_error(self) -> None:
        """T3b: STRICT=1 (fail-closed audit) + no pepper → RuntimeError.

        The STRICT flag means audit writes are mandatory.  A missing pepper
        implies fingerprints cannot be computed, so STRICT mode must also
        treat this as a hard misconfiguration.
        """
        os.environ["KILL_SWITCH_AUDIT_STRICT"] = "1"
        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "0"
        os.environ.pop("KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER", None)
        os.environ.pop("KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER_B64", None)

        with pytest.raises(RuntimeError) as exc_info:
            fingerprint_token("tok_admin_test")

        assert "FINGERPRINT_PEPPER_NOT_SET" in str(exc_info.value), (
            f"RuntimeError must contain 'FINGERPRINT_PEPPER_NOT_SET', got: {exc_info.value!r}"
        )

    # ── T4: No raw token leakage ───────────────────────────────────────────────

    def test_t4_no_raw_token_in_audit_record(self) -> None:
        """T4: build_kill_switch_audit_record() must not expose raw token in JSON output.

        The raw admin token is a secret credential.  Storing it in a WORM record
        (immutable, long-retention, potentially visible to more principals than
        the secret itself) is a security violation.  Only the HMAC fingerprint
        (a one-way derivative) is stored.

        This test also verifies the fingerprint field is present and correctly
        formatted, providing a false-positive guard: the absence of the raw token
        alone could be satisfied by omitting the actor block entirely, which would
        also be wrong.
        """
        raw_token = "tok_live_SUPERSENSITIVE_DO_NOT_LOG_XYZ987"
        kid = "kid_t4"
        pepper = "pepper_for_t4_hardening_test"

        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_KID"] = kid
        os.environ["KILL_SWITCH_AUDIT_FINGERPRINT_PEPPER"] = pepper

        record = build_kill_switch_audit_record(
            request_id="req-t4-p59-001",
            actor_token=raw_token,
            actor_ip="10.0.0.99",
            mode_from="NORMAL",
            mode_to="SAFE_MODE",
            reason="P5.9 T4 audit record leakage test",
            ttl_minutes=0,
            result="ok",
        )

        record_json = json.dumps(record)

        # PRIMARY: raw token must not appear anywhere in the serialized record
        assert raw_token not in record_json, (
            f"Raw actor_token MUST NOT appear in audit record JSON. "
            f"Security violation: found {raw_token!r} in record."
        )

        # GUARD: actor block and fingerprint field must exist (test is not vacuously true)
        actor = record.get("actor", {})
        token_fp = actor.get("token_fingerprint")

        assert token_fp is not None, (
            "actor.token_fingerprint must be present in audit record. "
            "If it is None, there is no actor trace — not an acceptable fallback."
        )

        # Fingerprint must follow the <kid>:<12hex> format
        assert re.fullmatch(rf"{re.escape(kid)}:[0-9a-f]{{{_TRUNC_LEN}}}", token_fp), (
            f"actor.token_fingerprint must match '{kid}:[0-9a-f]{{12}}', "
            f"got {token_fp!r}"
        )

        # Fingerprint must not equal the raw token (obviously, but belt-and-suspenders)
        assert token_fp != raw_token, (
            "actor.token_fingerprint must not equal the raw token string"
        )

        # Verify HMAC is correct (independent computation)
        expected_hex = hmac.new(
            pepper.encode("utf-8"),
            raw_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:_TRUNC_LEN]
        assert token_fp == f"{kid}:{expected_hex}", (
            f"Fingerprint value is wrong. Expected {kid}:{expected_hex!r}, got {token_fp!r}"
        )
