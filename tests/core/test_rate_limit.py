"""Tests for src.core.rate_limit"""
import time
import pytest
from src.core.rate_limit import (
    RateLimitState,
    RateLimitTracker,
    parse_retry_after,
    calculate_backoff,
)


class TestRateLimitState:
    def test_default_state(self):
        state = RateLimitState()
        assert state.is_depleted
        assert state.seconds_until_reset == 0.0

    def test_not_depleted(self):
        state = RateLimitState(limit=5000, remaining=4000)
        assert not state.is_depleted
        assert not state.is_near_limit

    def test_near_limit(self):
        state = RateLimitState(limit=5000, remaining=400)
        assert state.is_near_limit

    def test_seconds_until_reset(self):
        state = RateLimitState(reset_timestamp=time.time() + 120)
        seconds = state.seconds_until_reset
        assert 119 <= seconds <= 121


class TestRateLimitTracker:
    def test_update_from_headers(self):
        tracker = RateLimitTracker()
        headers = {
            "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": "4999",
            "x-ratelimit-reset": str(time.time() + 3600),
            "x-ratelimit-used": "1",
        }
        tracker.update_from_headers(headers, "core")
        assert tracker.core.limit == 5000
        assert tracker.core.remaining == 4999
        assert tracker.core.used == 1

    def test_search_endpoint(self):
        tracker = RateLimitTracker()
        headers = {
            "x-ratelimit-limit": "30",
            "x-ratelimit-remaining": "25",
            "x-ratelimit-reset": str(time.time() + 60),
        }
        tracker.update_from_headers(headers, "search")
        assert tracker.search.limit == 30
        assert tracker.search.remaining == 25

    def test_should_backoff_when_depleted(self):
        tracker = RateLimitTracker()
        tracker.core = RateLimitState(remaining=0)
        assert tracker.should_backoff("core")

    def test_should_not_backoff_when_available(self):
        tracker = RateLimitTracker()
        tracker.core = RateLimitState(remaining=100)
        assert not tracker.should_backoff("core")

    def test_is_degraded(self):
        tracker = RateLimitTracker()
        tracker.core = RateLimitState(remaining=50)
        assert tracker.is_degraded()

    def test_summary(self):
        tracker = RateLimitTracker()
        summary = tracker.summary()
        assert "core" in summary
        assert "search" in summary
        assert "code_search" in summary


class TestParseRetryAfter:
    def test_parse_integer(self):
        headers = {"retry-after": "60"}
        result = parse_retry_after(headers)
        assert result == 60.0

    def test_parse_float(self):
        headers = {"retry-after": "30.5"}
        result = parse_retry_after(headers)
        assert result == 30.5

    def test_missing_header(self):
        result = parse_retry_after({})
        assert result is None

    def test_invalid_value(self):
        headers = {"retry-after": "not-a-number"}
        result = parse_retry_after(headers)
        assert result is None


class TestCalculateBackoff:
    def test_first_attempt(self):
        delay = calculate_backoff(0, base_seconds=60, max_seconds=600)
        assert 30 <= delay <= 60

    def test_exponential_growth(self):
        delays = [calculate_backoff(i, base_seconds=60, max_seconds=600) for i in range(4)]
        # Each delay should generally be larger than the previous
        assert delays[1] >= delays[0] * 0.5
        assert delays[3] >= delays[2] * 0.5

    def test_max_cap(self):
        delay = calculate_backoff(10, base_seconds=60, max_seconds=600)
        assert delay <= 600
