"""Timezone and date utilities for gh-ops.

All internal timestamps use UTC. This module provides helpers for
parsing GitHub API timestamps and formatting for display.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

# ISO 8601 formats commonly used by GitHub API
_GITHUB_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%fZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
]


def now_utc() -> datetime:
    """Get the current UTC datetime."""
    return datetime.now(timezone.utc)


def parse_github_timestamp(ts: str) -> datetime:
    """Parse a GitHub API timestamp string to UTC datetime.

    Args:
        ts: ISO 8601 timestamp from GitHub API.

    Returns:
        UTC datetime.

    Raises:
        ValueError: If the timestamp format is not recognized.
    """
    # Normalize Z suffix
    normalized = ts.rstrip("Z") + "Z" if not ts.endswith("Z") else ts.rstrip("Z")

    for fmt in _GITHUB_FORMATS:
        try:
            dt = datetime.strptime(normalized + ("Z" if not normalized.endswith("Z") else ""), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Try parsing with fromisoformat (Python 3.7+)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    raise ValueError(f"Unrecognized GitHub timestamp format: {ts}")


def days_ago(timestamp: datetime) -> int:
    """Calculate how many days ago a timestamp was.

    Args:
        timestamp: The timestamp to compare.

    Returns:
        Number of whole days elapsed.
    """
    delta = now_utc() - timestamp
    return delta.days


def format_relative_time(timestamp: datetime) -> str:
    """Format a timestamp as a human-readable relative time.

    Args:
        timestamp: The timestamp to format.

    Returns:
        Relative time string like "3 days ago", "2 hours ago".
    """
    delta = now_utc() - timestamp
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"


def format_github_timestamp(dt: datetime) -> str:
    """Format a datetime for GitHub API compatibility.

    Args:
        dt: UTC datetime.

    Returns:
        ISO 8601 string with Z suffix.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_stale(timestamp: datetime, max_age_days: int) -> bool:
    """Check if a timestamp is older than max_age_days.

    Args:
        timestamp: The timestamp to check.
        max_age_days: Maximum age in days.

    Returns:
        True if the timestamp is older than max_age_days.
    """
    return days_ago(timestamp) > max_age_days


def start_of_day(dt: Optional[datetime] = None) -> datetime:
    """Get the start of day (00:00:00 UTC) for a given datetime.

    Args:
        dt: Datetime to truncate. Defaults to now.

    Returns:
        Start of day in UTC.
    """
    if dt is None:
        dt = now_utc()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def end_of_day(dt: Optional[datetime] = None) -> datetime:
    """Get the end of day (23:59:59 UTC) for a given datetime.

    Args:
        dt: Datetime to truncate. Defaults to now.

    Returns:
        End of day in UTC.
    """
    if dt is None:
        dt = now_utc()
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
