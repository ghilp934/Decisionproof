"""Redis client configuration for DPP."""

import os
import redis
from typing import Optional
from urllib.parse import urlparse


class RedisClient:
    """Singleton Redis client."""

    _instance: Optional[redis.Redis] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
        """
        Get Redis client instance.

        P0-1: Production-configurable Redis with REDIS_URL/REDIS_PASSWORD.
        - Priority: REDIS_URL env var (e.g., redis://host:6379/0 or rediss://...)
        - Fallback: redis://localhost:6379/0 for local development
        - REDIS_PASSWORD: Applied only if URL has no password

        Returns:
            redis.Redis: Redis client
        """
        if cls._instance is None:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            redis_password = os.getenv("REDIS_PASSWORD")

            # Parse URL to check if password is already present
            parsed = urlparse(redis_url)

            # Build connection kwargs
            kwargs = {
                "decode_responses": True,
                "socket_connect_timeout": 5,
                "socket_timeout": 5,
                "health_check_interval": 30,  # P0-1: Stability option
            }

            # If password not in URL and REDIS_PASSWORD is set, add it
            if not parsed.password and redis_password:
                kwargs["password"] = redis_password

            # Create client from URL
            cls._instance = redis.from_url(redis_url, **kwargs)

        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset Redis client (for testing)."""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None


def get_redis() -> redis.Redis:
    """
    Get Redis client for dependency injection.

    Returns:
        redis.Redis: Redis client
    """
    return RedisClient.get_client()
