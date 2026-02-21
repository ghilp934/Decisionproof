"""Supabase client configuration for auth operations.

Phase 2: Email onboarding with Supabase Auth + AWS SES SMTP.

SECURITY NOTICE:
- SB_SECRET_KEY is server-only (NEVER exposed to clients)
- SB_PUBLISHABLE_KEY is for client-side auth (used in server for signUp/signIn)
- All auth operations use PUBLISHABLE_KEY (respects RLS)
- Admin operations (if needed) use SECRET_KEY (bypasses RLS)

KEY NAMING TRANSITION:
- New Supabase UI (2024+): SB_PUBLISHABLE_KEY / SB_SECRET_KEY
- Legacy (pre-2024): SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY
- Backward compatibility: Falls back to legacy names if new names not set
"""

import logging
import os
from functools import lru_cache

from supabase import Client, create_client

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase_url() -> str:
    """Get Supabase project URL from environment.

    Returns:
        str: Supabase URL (https://[project_ref].supabase.co)

    Raises:
        RuntimeError: If SUPABASE_URL not set
    """
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_URL environment variable not set. "
            "Required for Phase 2 email onboarding."
        )
    return url


@lru_cache(maxsize=1)
def get_supabase_api_key() -> str:
    """Get Supabase publishable (anon) key from environment.

    Priority:
    1. SB_PUBLISHABLE_KEY (new standard, Supabase UI 2024+)
    2. SUPABASE_ANON_KEY (legacy, backward compatibility)

    This key is used for client-side auth operations (signUp, signIn).
    It respects RLS policies.

    Returns:
        str: Supabase publishable key

    Raises:
        RuntimeError: If neither key is set
    """
    # Try new standard first
    key = os.getenv("SB_PUBLISHABLE_KEY")
    if key:
        return key

    # Fallback to legacy
    key = os.getenv("SUPABASE_ANON_KEY")
    if key:
        logger.info(
            "Using legacy SUPABASE_ANON_KEY (consider migrating to SB_PUBLISHABLE_KEY)"
        )
        return key

    # Neither set
    raise RuntimeError(
        "Neither SB_PUBLISHABLE_KEY nor SUPABASE_ANON_KEY environment variable is set. "
        "Required for Phase 2 email onboarding. "
        "Set SB_PUBLISHABLE_KEY (recommended) or SUPABASE_ANON_KEY (legacy)."
    )


@lru_cache(maxsize=1)
def get_supabase_anon_key() -> str:
    """Get Supabase anonymous key from environment.

    DEPRECATED: Use get_supabase_api_key() instead.
    This function is kept for backward compatibility.

    Returns:
        str: Supabase publishable key

    Raises:
        RuntimeError: If neither key is set
    """
    return get_supabase_api_key()


@lru_cache(maxsize=1)
def get_supabase_secret_key() -> str:
    """Get Supabase secret (service role) key from environment.

    Priority:
    1. SB_SECRET_KEY (new standard, Supabase UI 2024+)
    2. SUPABASE_SERVICE_ROLE_KEY (legacy, backward compatibility)

    SECRET_KEY bypasses RLS and is for admin operations only.
    NEVER expose this to clients.

    Returns:
        str: Supabase secret key

    Raises:
        RuntimeError: If neither key is set
    """
    # Try new standard first
    key = os.getenv("SB_SECRET_KEY")
    if key:
        return key

    # Fallback to legacy
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if key:
        logger.info(
            "Using legacy SUPABASE_SERVICE_ROLE_KEY (consider migrating to SB_SECRET_KEY)"
        )
        return key

    # Neither set
    raise RuntimeError(
        "Neither SB_SECRET_KEY nor SUPABASE_SERVICE_ROLE_KEY environment variable is set. "
        "Required for server-side admin operations. "
        "Set SB_SECRET_KEY (recommended) or SUPABASE_SERVICE_ROLE_KEY (legacy)."
    )


@lru_cache(maxsize=1)
def get_supabase_service_role_key() -> str:
    """Get Supabase service role key from environment.

    DEPRECATED: Use get_supabase_secret_key() instead.
    This function is kept for backward compatibility.

    Returns:
        str: Supabase secret key

    Raises:
        RuntimeError: If neither key is set
    """
    return get_supabase_secret_key()


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Get Supabase client for auth operations.

    Uses PUBLISHABLE_KEY for standard auth operations (signUp, signIn).
    This client respects RLS policies.

    Returns:
        Client: Supabase client instance

    Raises:
        RuntimeError: If environment variables not set
    """
    url = get_supabase_url()
    api_key = get_supabase_api_key()

    # Log initialization (without exposing keys)
    logger.info(
        "Initializing Supabase client",
        extra={
            "supabase_url": url,
            "key_type": "publishable",
            "phase": "phase2_auth",
        },
    )

    return create_client(url, api_key)


@lru_cache(maxsize=1)
def get_supabase_admin_client() -> Client:
    """Get Supabase admin client for server-side operations.

    Uses SECRET_KEY which bypasses RLS.
    Use with extreme caution - for admin operations only.

    Returns:
        Client: Supabase admin client instance

    Raises:
        RuntimeError: If environment variables not set
    """
    url = get_supabase_url()
    secret_key = get_supabase_secret_key()

    # Log initialization (without exposing keys)
    logger.info(
        "Initializing Supabase admin client",
        extra={
            "supabase_url": url,
            "key_type": "secret",
            "phase": "phase2_auth",
        },
    )

    return create_client(url, secret_key)
