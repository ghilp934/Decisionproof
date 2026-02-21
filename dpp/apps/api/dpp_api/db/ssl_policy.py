"""SSL Policy SSOT — Decisionproof DB connection SSL enforcement.

Single source of truth for sslmode and sslrootcert across:
  - dpp_api.db.engine              (API runtime, SQLAlchemy)
  - dpp/alembic/env.py             (migration runner, via build_engine)
  - worker_ses_feedback/worker.py  (inline equivalent; standalone container)

ENV Contract (Spec Lock)
─────────────────────────
DPP_DB_SSLMODE      : Target SSL mode.
                       Default: "verify-full" (PROD + Supabase host)
                               "require"      (all other envs/hosts)
                       Allowed: require | verify-ca | verify-full
                       Forbidden in PROD: disable | allow | prefer
DPP_DB_SSLROOTCERT  : Path to CA bundle file.
                       Required when sslmode = verify-ca or verify-full.
                       Example: /etc/ssl/certs/supabase-ca/supabase-ca.crt
DATABASE_SSL_ROOT_CERT : Legacy alias for DPP_DB_SSLROOTCERT (lower precedence).

ENV SSOT Rule (Spec Lock)
──────────────────────────
  DPP_DB_SSLMODE set   → ENV is authoritative.
                           URL sslmode conflict + PROD → RuntimeError (fail-fast).
                           URL sslmode conflict + non-PROD → WARNING log only.
  DPP_DB_SSLMODE unset → URL sslmode (if present) used verbatim (legacy compat).
                           If URL also has none → compute default per env/host.

PROD + Supabase host   → sslmode must be "verify-full" (enforced by guardrail).
CA Required Modes      → verify-ca, verify-full require a readable DPP_DB_SSLROOTCERT.

Used by engine.py which calls resolve_ssl_settings(); worker uses inline mirror.
"""

import logging
import os
from typing import Optional

from dpp_api.db.url_policy import get_sslmode_from_url, is_supabase_host

logger = logging.getLogger(__name__)

# Modes that provide wire encryption — all acceptable at the transport layer.
SAFE_SSL_MODES: frozenset = frozenset({"require", "verify-ca", "verify-full"})

# Modes that require a CA bundle to perform certificate chain validation.
CA_REQUIRED_MODES: frozenset = frozenset({"verify-ca", "verify-full"})

# Modes that indicate no/degraded encryption — forbidden in production.
UNSAFE_SSL_MODES: frozenset = frozenset({"disable", "allow", "prefer"})


def get_sslrootcert() -> Optional[str]:
    """Return the CA bundle path from ENV.

    DPP_DB_SSLROOTCERT takes precedence over the DATABASE_SSL_ROOT_CERT alias.

    Returns:
        File path string or None if neither env var is set.
    """
    return os.getenv("DPP_DB_SSLROOTCERT") or os.getenv("DATABASE_SSL_ROOT_CERT")


def _default_sslmode(database_url: str, dp_env: str) -> str:
    """Compute the baseline sslmode when no explicit override is present.

    Spec Lock:
      PROD + Supabase host → "verify-full"
      Everything else      → "require"

    Args:
        database_url: Connection URL (checked for Supabase host pattern).
        dp_env: Deployment environment string (e.g. "prod", "dev", "staging").

    Returns:
        Default sslmode string.
    """
    is_prod = dp_env.lower() in {"prod", "production"}
    if is_prod and is_supabase_host(database_url):
        return "verify-full"
    return "require"


def effective_sslmode(database_url: str, dp_env: str) -> str:
    """Determine the effective sslmode following ENV-SSOT precedence.

    Precedence order (Spec Lock):
      1. DPP_DB_SSLMODE set → ENV is authoritative.
            - URL sslmode disagrees + PROD → RuntimeError.
            - URL sslmode disagrees + non-PROD → WARNING, ENV still wins.
      2. DPP_DB_SSLMODE not set, URL has sslmode → use URL value (legacy compat).
      3. Neither set → compute via _default_sslmode() (PROD+Supabase = verify-full).

    Args:
        database_url: Database connection URL (may include ?sslmode=).
        dp_env: Deployment environment string.

    Returns:
        Effective SSL mode string (lowercase).

    Raises:
        RuntimeError: ENV and URL sslmode conflict in PROD environment.
    """
    env_sslmode = os.getenv("DPP_DB_SSLMODE")
    url_sslmode = get_sslmode_from_url(database_url)
    is_prod = dp_env.lower() in {"prod", "production"}

    if env_sslmode:
        mode = env_sslmode.lower()
        # Check for URL conflict — ENV is SSOT but we must alert on discrepancies.
        if url_sslmode and url_sslmode.lower() != mode:
            conflict_msg = (
                f"SSL mode conflict: URL contains sslmode={url_sslmode!r} but "
                f"DPP_DB_SSLMODE={mode!r}. ENV is SSOT — ENV value will be used. "
                "Remove sslmode from DATABASE_URL to avoid confusion."
            )
            if is_prod:
                raise RuntimeError(conflict_msg)
            else:
                logger.warning(conflict_msg)
        return mode

    # ENV not set: fall back to URL value or computed default.
    if url_sslmode:
        return url_sslmode.lower()

    return _default_sslmode(database_url, dp_env)


def validate_ssl_settings(sslmode: str, sslrootcert: Optional[str]) -> None:
    """Validate that the SSL settings are internally consistent and operational.

    Spec Lock (fail-fast):
      verify-ca / verify-full without sslrootcert  → RuntimeError.
      verify-ca / verify-full with unreadable file → RuntimeError.

    Non-CA modes (require) pass without a cert file.

    Args:
        sslmode: The effective SSL mode string.
        sslrootcert: Path to the CA bundle file, or None.

    Raises:
        RuntimeError: sslrootcert missing or unreadable for CA-required modes.
    """
    if sslmode not in CA_REQUIRED_MODES:
        return  # No cert needed for "require".

    if not sslrootcert:
        raise RuntimeError(
            f"SSL POLICY: sslmode={sslmode!r} requires a CA bundle. "
            "Set DPP_DB_SSLROOTCERT=/etc/ssl/certs/supabase-ca/supabase-ca.crt "
            "and mount the Supabase CA ConfigMap. "
            "See ops/runbooks/db_ssl_verify_full.md for setup instructions."
        )

    if not os.path.isfile(sslrootcert):
        raise RuntimeError(
            f"SSL POLICY: sslmode={sslmode!r} is configured but the CA bundle "
            f"file is not found or not readable: {sslrootcert!r}. "
            "Verify that the Supabase CA ConfigMap is mounted at the expected path "
            "and that DPP_DB_SSLROOTCERT points to the correct file. "
            "See ops/runbooks/db_ssl_verify_full.md for setup instructions."
        )


def resolve_ssl_settings(database_url: str, dp_env: str) -> dict:
    """Compute, validate, and return the SSL connect_args dict.

    This is the primary SSOT entry point consumed by dpp_api.db.engine.build_engine().
    Returns a dict suitable for passing as SQLAlchemy ``connect_args`` or psycopg2
    keyword arguments.

    Spec Lock:
      - Non-Supabase host → returns {} (no SSL enforcement from this layer).
      - Supabase host → returns at minimum {"sslmode": <effective_mode>}.
      - sslrootcert available → also adds {"sslrootcert": <path>}.
      - verify-ca / verify-full without readable sslrootcert → RuntimeError (fail-fast).

    Args:
        database_url: Database connection URL.
        dp_env: Deployment environment (used to compute verify-full default in PROD).

    Returns:
        Dict with SSL connect_args keys:
          {"sslmode": str}                              — always present for Supabase
          {"sslmode": str, "sslrootcert": str}         — when DPP_DB_SSLROOTCERT set

    Raises:
        RuntimeError: ENV/URL conflict in PROD, or verify-full without cert.

    Examples:
        >>> # PROD, Supabase, DPP_DB_SSLMODE=verify-full, DPP_DB_SSLROOTCERT=/path/ca.crt
        >>> resolve_ssl_settings("postgresql://host.pooler.supabase.com/db", "prod")
        {'sslmode': 'verify-full', 'sslrootcert': '/path/ca.crt'}

        >>> # Non-Supabase host (no SSL enforcement)
        >>> resolve_ssl_settings("postgresql://localhost/mydb", "prod")
        {}
    """
    if not is_supabase_host(database_url):
        return {}

    mode = effective_sslmode(database_url, dp_env)
    sslrootcert = get_sslrootcert()

    # Fail-fast: CA-required modes need a readable cert file.
    validate_ssl_settings(mode, sslrootcert)

    result: dict = {"sslmode": mode}
    if sslrootcert:
        result["sslrootcert"] = sslrootcert

    return result
