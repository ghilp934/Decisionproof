"""Supabase URL policy helpers — SSL mode injection and host detection.

SSOT shared by:
  - dpp_api.db.engine             (API runtime)
  - alembic/env.py                (migrations, via sys.path)
  - worker_ses_feedback/worker.py (uses inline equivalent)

SSL Mode Precedence (Spec Lock):
  URL-embedded sslmode > ENV default (DPP_DB_SSLMODE)
  If URL has no sslmode AND host is Supabase -> inject default_mode via ensure_sslmode().
"""

from typing import Optional
from urllib.parse import parse_qs, urlparse

# SSL modes that provide wire encryption — safe for Supabase production.
SAFE_SSL_MODES: frozenset = frozenset({"require", "verify-ca", "verify-full"})

# SSL modes that are explicitly unsafe (no encryption or degraded).
UNSAFE_SSL_MODES: frozenset = frozenset({"disable", "allow", "prefer"})


def is_supabase_host(url: str) -> bool:
    """Return True if the URL points to a Supabase-managed host.

    Matches:
      - *.supabase.co             (direct / session pooler)
      - *.pooler.supabase.com     (transaction pooler / PgBouncer, port 6543)

    Args:
        url: Database connection URL string.

    Returns:
        True if the host is a known Supabase endpoint.
    """
    return ".supabase.co" in url or ".pooler.supabase.com" in url


def get_sslmode_from_url(url: str) -> Optional[str]:
    """Extract sslmode value from URL query string.

    Returns None if sslmode is not present in the URL.

    Args:
        url: Database connection URL.

    Returns:
        sslmode string (e.g. "require", "verify-full") or None.

    Examples:
        >>> get_sslmode_from_url("postgresql://host/db?sslmode=require")
        'require'
        >>> get_sslmode_from_url("postgresql://host/db")
        None
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        modes = qs.get("sslmode", [])
        return modes[0] if modes else None
    except Exception:
        return None


def ensure_sslmode(url: str, default_mode: str = "require") -> str:
    """Ensure sslmode is present in the URL for Supabase hosts.

    Spec Lock (URL Precedence):
      - Non-Supabase host                     -> URL returned unchanged.
      - Supabase host, sslmode already in URL -> URL returned unchanged (URL is SSOT).
      - Supabase host, no sslmode in URL      -> append ?sslmode=<default_mode>.

    Used by Alembic env.py and worker_ses_feedback to guarantee SSL before
    passing the URL to engine_from_config() or psycopg2.connect().
    The API runtime (engine.py / build_engine) applies SSL via connect_args instead,
    but still calls this in its guardrail checks.

    Args:
        url: Database connection URL.
        default_mode: SSL mode to inject when URL has none.
                      Defaults to "require".
                      Use "verify-full" after CA bundle is mounted (DPP_DB_SSLROOTCERT).

    Returns:
        URL with sslmode guaranteed for Supabase hosts.

    Examples:
        >>> ensure_sslmode("postgresql://host.pooler.supabase.com:6543/db")
        'postgresql://host.pooler.supabase.com:6543/db?sslmode=require'

        >>> ensure_sslmode("postgresql://host.pooler.supabase.com:6543/db?sslmode=verify-full")
        'postgresql://host.pooler.supabase.com:6543/db?sslmode=verify-full'

        >>> ensure_sslmode("postgresql://localhost:5432/db")
        'postgresql://localhost:5432/db'
    """
    if not is_supabase_host(url):
        return url

    if "sslmode=" in url:
        return url  # URL is SSOT; preserve caller-specified value.

    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode={default_mode}"
