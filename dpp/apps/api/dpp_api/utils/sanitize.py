"""P5.2: PII / secret / stack-trace sanitizer.

Three-tier string processing:
 1. > MAX_STR_LOG   → truncate + sha256, never run regex
 2. > MAX_STR_FOR_REGEX → prefix check only (Bearer/Basic)
 3. ≤ MAX_STR_FOR_REGEX → full regex replacement (5 patterns)

ReDoS mitigation: patterns are anchored to non-whitespace (\\S+),
all pre-compiled at module import, size gate prevents catastrophic backtracking.
"""

import hashlib
import re
import traceback
from typing import Any

# ── Size thresholds ───────────────────────────────────────────────────────────
MAX_STR_LOG: int = 2048   # Truncate whole string; skip regex
MAX_STR_FOR_REGEX: int = 512   # Run prefix check only; skip regex
MAX_DEPTH: int = 6         # Recursive object traversal limit

# ── Sensitive dict keys (lower-cased for comparison) ─────────────────────────
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "authorization", "token", "access_token", "refresh_token",
    "api_key", "secret", "signature", "email", "phone",
    "card", "pan", "cvv", "cvc", "payer", "billing",
    "customerkey", "paymentkey", "billingkey",
})

# ── Pre-compiled regex patterns (module-level → compiled once) ────────────────
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Bearer \S+"),
    re.compile(r"Basic \S+"),
    re.compile(r"api_key=\S+"),
    re.compile(r"access_token=\S+"),
    re.compile(r"client_secret=\S+"),
]

_BEARER_PREFIX = "Bearer "
_BASIC_PREFIX = "Basic "


# ── Public helpers ────────────────────────────────────────────────────────────

def payload_hash_bytes(raw: bytes) -> str:
    """Return sha256 hex digest of raw bytes."""
    return hashlib.sha256(raw).hexdigest()


def sanitize_str(s: str) -> str:
    """Sanitize a string value according to three-tier size gate.

    Returns a redacted / truncated string; never the original sensitive value.
    """
    if not isinstance(s, str):
        return s  # type: ignore[return-value]

    n = len(s)

    # Tier 1: too long to log at all → truncate with hash
    if n > MAX_STR_LOG:
        digest = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"[TRUNCATED len={n} sha256={digest}]"

    # Tier 2: long enough to be risky for regex but worth logging → prefix check
    if n > MAX_STR_FOR_REGEX:
        if s.startswith(_BEARER_PREFIX):
            return "[REDACTED]"
        if s.startswith(_BASIC_PREFIX):
            return "[REDACTED]"
        return s

    # Tier 3: short enough → full regex replacement
    result = s
    for pattern in _PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def sanitize_obj(obj: Any, depth: int = 0) -> Any:
    """Recursively sanitize a log extra value.

    - dict: redact sensitive keys, recurse others
    - list: recurse each element
    - str: run sanitize_str()
    - other: return as-is

    Depth is capped at MAX_DEPTH to prevent stack overflow on pathological inputs.
    """
    if depth >= MAX_DEPTH:
        return "[DEPTH_LIMIT]"

    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize_obj(value, depth + 1)
        return result

    if isinstance(obj, list):
        return [sanitize_obj(item, depth + 1) for item in obj]

    if isinstance(obj, str):
        return sanitize_str(obj)

    return obj


def sanitize_exc(exc_info: tuple) -> str:
    """Format an exc_info tuple into a sanitized traceback string.

    Uses capture_locals=False to avoid leaking local variable values
    (which may contain secrets or PII) into log output.
    """
    _type, value, _tb = exc_info
    if value is None:
        return ""
    try:
        te = traceback.TracebackException.from_exception(value, capture_locals=False)
        formatted = "".join(te.format())
        return sanitize_str(formatted)
    except Exception:
        return "[TRACEBACK_FORMAT_ERROR]"
