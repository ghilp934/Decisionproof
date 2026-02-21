"""Token lifecycle management for P0-3.

Opaque Bearer token generation, hashing, and verification.

SECURITY:
- Tokens are HMAC-SHA256 hashed with a secret PEPPER
- Raw tokens are NEVER stored in database
- Display-once: tokens returned only at issuance/rotation
- Pepper versioning for key rotation support
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
from typing import Tuple

logger = logging.getLogger(__name__)


def get_pepper(version: int = 1) -> str:
    """Get pepper by version for HMAC hashing.

    Environment Variables:
    - TOKEN_PEPPER_V1: Required for version 1 (default)
    - TOKEN_PEPPER_V2: Optional for version 2 (future rotation)

    Args:
        version: Pepper version number (1 or 2)

    Returns:
        Pepper string

    Raises:
        ValueError: If pepper not found for version
    """
    env_key = f"TOKEN_PEPPER_V{version}"
    pepper = os.getenv(env_key)

    if not pepper:
        raise ValueError(
            f"{env_key} environment variable is required for token hashing. "
            f"Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )

    return pepper


def generate_token(prefix: str = "dp_live") -> Tuple[str, str]:
    """Generate a new opaque Bearer token.

    Token format: {prefix}_{base64url(32_random_bytes)}
    Example: dp_live_Kx7jQ2mN9pL1Rz8wV3yU4tS5aB6cD7eF8gH9iJ0kL1mN2

    Args:
        prefix: Token prefix (dp_live or dp_test)

    Returns:
        Tuple of (full_token, last4)
        - full_token: Complete token string (display once)
        - last4: Last 4 characters for display

    Security:
    - Uses secrets.token_bytes() for CSPRNG
    - 32 bytes = 256 bits of entropy
    - base64url encoding (no padding) for URL safety
    """
    # Generate 32 random bytes (256 bits)
    random_bytes = secrets.token_bytes(32)

    # Encode as base64url without padding
    random_part = base64.urlsafe_b64encode(random_bytes).decode("ascii").rstrip("=")

    # Construct full token
    full_token = f"{prefix}_{random_part}"

    # Last 4 characters for display
    last4 = full_token[-4:]

    logger.info(
        "Token generated",
        extra={
            "event": "token.generated",
            "prefix": prefix,
            "last4": last4,
            # Do NOT log full token
        },
    )

    return full_token, last4


def hash_token(raw_token: str, pepper_version: int = 1) -> str:
    """Hash token using HMAC-SHA256 with pepper.

    Args:
        raw_token: Full token string (e.g., dp_live_xxx)
        pepper_version: Pepper version to use (default 1)

    Returns:
        Base64url-encoded HMAC-SHA256 hash (no padding)

    Security:
    - HMAC-SHA256 prevents length extension attacks
    - Pepper prevents rainbow table attacks
    - base64url encoding for database storage
    """
    pepper = get_pepper(pepper_version)

    # HMAC-SHA256
    hmac_digest = hmac.new(
        key=pepper.encode("utf-8"),
        msg=raw_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    # Encode as base64url without padding
    token_hash = base64.urlsafe_b64encode(hmac_digest).decode("ascii").rstrip("=")

    return token_hash


def verify_token_hash(raw_token: str, expected_hash: str, pepper_version: int = 1) -> bool:
    """Verify token hash matches expected value.

    Args:
        raw_token: Full token string
        expected_hash: Expected hash from database
        pepper_version: Pepper version used for hashing

    Returns:
        True if hash matches, False otherwise

    Security:
    - Uses constant-time comparison (hmac.compare_digest)
    - Prevents timing attacks
    """
    computed_hash = hash_token(raw_token, pepper_version)

    # Constant-time comparison
    return hmac.compare_digest(computed_hash, expected_hash)


def parse_token_prefix(raw_token: str) -> str:
    """Extract prefix from token.

    Args:
        raw_token: Full token string

    Returns:
        Prefix (e.g., 'dp_live', 'dp_test')

    Raises:
        ValueError: If token format is invalid
    """
    if "_" not in raw_token:
        raise ValueError("Invalid token format: missing prefix separator")

    parts = raw_token.split("_", 2)
    if len(parts) < 2:
        raise ValueError("Invalid token format: invalid structure")

    # Reconstruct prefix (e.g., dp_live)
    prefix = f"{parts[0]}_{parts[1]}"

    return prefix


def hash_for_logging(value: str) -> str:
    """Hash value for privacy-preserving logging.

    Used for IP addresses and User-Agent strings in auth_request_log.

    Args:
        value: Value to hash (IP or UA string)

    Returns:
        Hex-encoded SHA256 hash

    Security:
    - Uses separate LOG_PEPPER from token pepper
    - SHA256 for one-way hashing
    """
    log_pepper = os.getenv("LOG_PEPPER", "default-log-pepper-change-me")

    if log_pepper == "default-log-pepper-change-me":
        logger.warning(
            "LOG_PEPPER not set, using default (INSECURE). "
            "Set LOG_PEPPER environment variable."
        )

    hash_digest = hashlib.sha256(f"{log_pepper}{value}".encode("utf-8")).digest()
    return hash_digest.hex()
