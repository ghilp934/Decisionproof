"""Rate Limiter for IETF RateLimit Headers (RC-3).

RC-3: Contract Gate - RateLimit Headers
- Standard: IETF "RateLimit header fields for HTTP"
- Headers: RateLimit-Policy, RateLimit (Structured Fields)
- Single policy: "default" with q=60, w=60
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class RateLimitResult:
    """Result of rate limit check.

    Attributes:
        allowed: Whether the request is allowed
        policy_id: Policy identifier (e.g., "default")
        quota: Total quota (q parameter)
        window: Window in seconds (w parameter)
        remaining: Remaining quota (r parameter)
        reset: Seconds until reset (t parameter)
    """
    allowed: bool
    policy_id: str
    quota: int
    window: int
    remaining: int
    reset: int


class RateLimiter(ABC):
    """Abstract base class for rate limiters."""

    @abstractmethod
    def check_rate_limit(self, key: str, path: str) -> RateLimitResult:
        """Check if request is within rate limit.

        Args:
            key: Identifier for the requester (e.g., api_key, user_id)
            path: Request path (e.g., "/v1/usage")

        Returns:
            RateLimitResult with allow/deny decision and header values
        """
        pass


class NoOpRateLimiter(RateLimiter):
    """No-op rate limiter that always allows requests.

    RC-3: Default production limiter (actual rate limiting not implemented yet).
    Returns header values for documentation compliance.
    """

    def __init__(self, quota: int = 60, window: int = 60):
        """Initialize no-op limiter.

        Args:
            quota: Quota per window (default: 60)
            window: Window in seconds (default: 60)
        """
        self.quota = quota
        self.window = window

    def check_rate_limit(self, key: str, path: str) -> RateLimitResult:
        """Always allow, return full quota."""
        return RateLimitResult(
            allowed=True,
            policy_id="default",
            quota=self.quota,
            window=self.window,
            remaining=self.quota - 1,  # Assume 1 request consumed
            reset=self.window,
        )


class DeterministicTestLimiter(RateLimiter):
    """Deterministic in-memory rate limiter for testing.

    RC-3: Test-only limiter with q=1, w=60.
    - First request: allowed (2xx)
    - Second request: denied (429)
    - Partition by (key, path)
    """

    def __init__(self, quota: int = 1, window: int = 60):
        """Initialize test limiter.

        Args:
            quota: Quota per window (default: 1 for deterministic 429)
            window: Window in seconds (default: 60)
        """
        self.quota = quota
        self.window = window
        # In-memory counter: {(key, path): (count, window_start_time)}
        self._counters: Dict[Tuple[str, str], Tuple[int, float]] = {}

    def check_rate_limit(self, key: str, path: str) -> RateLimitResult:
        """Check rate limit with deterministic behavior."""
        partition_key = (key, path)
        current_time = time.time()

        # Get or create counter
        if partition_key not in self._counters:
            # First request in this window
            self._counters[partition_key] = (1, current_time)
            return RateLimitResult(
                allowed=True,
                policy_id="default",
                quota=self.quota,
                window=self.window,
                remaining=self.quota - 1,  # 1 consumed
                reset=self.window,
            )

        count, window_start = self._counters[partition_key]
        elapsed = current_time - window_start

        # Check if window expired
        if elapsed >= self.window:
            # New window
            self._counters[partition_key] = (1, current_time)
            return RateLimitResult(
                allowed=True,
                policy_id="default",
                quota=self.quota,
                window=self.window,
                remaining=self.quota - 1,
                reset=self.window,
            )

        # Within same window
        if count < self.quota:
            # Still has quota
            self._counters[partition_key] = (count + 1, window_start)
            remaining = self.quota - (count + 1)
            time_left = int(self.window - elapsed)
            return RateLimitResult(
                allowed=True,
                policy_id="default",
                quota=self.quota,
                window=self.window,
                remaining=remaining,
                reset=time_left,
            )
        else:
            # Quota exceeded
            time_left = int(self.window - elapsed)
            return RateLimitResult(
                allowed=False,
                policy_id="default",
                quota=self.quota,
                window=self.window,
                remaining=0,
                reset=time_left,
            )

    def reset(self):
        """Reset all counters (test utility)."""
        self._counters.clear()
