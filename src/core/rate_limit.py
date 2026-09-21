"""Rate-limit tracking and backoff for GitHub API requests.

Parses X-RateLimit-* headers, tracks remaining quota, and implements
exponential backoff when limits are hit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from src.utils.logging import get_logger

logger = get_logger("core.rate_limit")


@dataclass
class RateLimitState:
    """Tracks rate limit status for a GitHub API endpoint.

    Attributes:
        limit: Maximum requests allowed.
        remaining: Requests remaining.
        reset_timestamp: Unix timestamp when the limit resets.
        used: Requests used so far.
    """

    limit: int = 0
    remaining: int = 0
    reset_timestamp: float = 0.0
    used: int = 0

    @property
    def is_depleted(self) -> bool:
        """True if no requests remaining."""
        return self.remaining <= 0

    @property
    def seconds_until_reset(self) -> float:
        """Seconds until the rate limit resets."""
        now = time.time()
        if self.reset_timestamp <= now:
            return 0.0
        return self.reset_timestamp - now

    @property
    def is_near_limit(self) -> bool:
        """True if remaining is below 10% of limit."""
        if self.limit <= 0:
            return True
        return self.remaining < (self.limit * 0.1)


@dataclass
class RateLimitTracker:
    """Manages rate limit state across multiple endpoints.

    GitHub has separate rate limits for:
    - Core API: 5000/hr (authenticated)
    - Search API: 30/min (authenticated)
    - Code Search: 10/min (authenticated)
    """

    core: RateLimitState = field(default_factory=RateLimitState)
    search: RateLimitState = field(default_factory=RateLimitState)
    code_search: RateLimitState = field(default_factory=RateLimitState)

    def update_from_headers(
        self,
        headers: dict[str, str],
        endpoint: str = "core",
    ) -> None:
        """Parse rate limit headers and update the appropriate tracker.

        Args:
            headers: HTTP response headers.
            endpoint: Which rate limit bucket ("core", "search", "code_search").
        """
        state = self._get_state(endpoint)

        if "x-ratelimit-limit" in headers:
            state.limit = int(headers["x-ratelimit-limit"])
        if "x-ratelimit-remaining" in headers:
            state.remaining = int(headers["x-ratelimit-remaining"])
        if "x-ratelimit-reset" in headers:
            state.reset_timestamp = float(headers["x-ratelimit-reset"])
        if "x-ratelimit-used" in headers:
            state.used = int(headers["x-ratelimit-used"])

        if state.is_depleted:
            logger.warning(
                "Rate limit depleted for %s. Resets in %.0fs",
                endpoint,
                state.seconds_until_reset,
            )
        elif state.is_near_limit:
            logger.info(
                "Rate limit near threshold for %s: %d/%d remaining",
                endpoint,
                state.remaining,
                state.limit,
            )

    def _get_state(self, endpoint: str) -> RateLimitState:
        """Get the rate limit state for an endpoint."""
        if endpoint == "search":
            return self.search
        elif endpoint == "code_search":
            return self.code_search
        return self.core

    def should_backoff(self, endpoint: str = "core") -> bool:
        """Check if we should back off before making a request.

        Args:
            endpoint: Which rate limit bucket to check.

        Returns:
            True if we should wait before making a request.
        """
        state = self._get_state(endpoint)
        return state.is_depleted

    def backoff_seconds(self, endpoint: str = "core") -> float:
        """Calculate how long to wait before retrying.

        Uses the reset timestamp from headers, or a conservative default.

        Args:
            endpoint: Which rate limit bucket.

        Returns:
            Seconds to wait.
        """
        state = self._get_state(endpoint)
        if state.seconds_until_reset > 0:
            return state.seconds_until_reset + 1  # Add 1s buffer
        return 0.0

    def is_degraded(self, threshold: int = 100) -> bool:
        """Check if we're in degraded mode (low remaining quota).

        Args:
            threshold: Minimum remaining requests to avoid degraded mode.

        Returns:
            True if any endpoint is below threshold.
        """
        return (
            self.core.remaining < threshold
            or self.search.remaining < 10
            or self.code_search.remaining < 3
        )

    def summary(self) -> dict[str, dict[str, int]]:
        """Return a summary of all rate limit states."""
        return {
            "core": {
                "limit": self.core.limit,
                "remaining": self.core.remaining,
                "used": self.core.used,
            },
            "search": {
                "limit": self.search.limit,
                "remaining": self.search.remaining,
                "used": self.search.used,
            },
            "code_search": {
                "limit": self.code_search.limit,
                "remaining": self.code_search.remaining,
                "used": self.code_search.used,
            },
        }


def parse_retry_after(headers: dict[str, str]) -> Optional[float]:
    """Parse the Retry-After header from a 429 response.

    Args:
        headers: HTTP response headers.

    Returns:
        Seconds to wait, or None if header not present.
    """
    retry_after = headers.get("retry-after")
    if retry_after is None:
        return None

    try:
        return float(retry_after)
    except ValueError:
        return None


def calculate_backoff(
    attempt: int,
    base_seconds: float = 60.0,
    max_seconds: float = 600.0,
) -> float:
    """Calculate exponential backoff with jitter.

    Args:
        attempt: Current attempt number (0-based).
        base_seconds: Base delay.
        max_seconds: Maximum delay.

    Returns:
        Seconds to wait.
    """
    import random

    delay = min(base_seconds * (2 ** attempt), max_seconds)
    # Add jitter: 50-100% of calculated delay
    jitter = delay * (0.5 + random.random() * 0.5)
    return min(jitter, max_seconds)
