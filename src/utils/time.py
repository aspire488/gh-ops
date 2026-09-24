"""Timezone and date utilities for gh-ops.

All internal timestamps use UTC. This module provides helpers for
parsing GitHub API timestamps and formatting for display.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# ISO 8601 formats commonly used by GitHub API
_GITHUB_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%fZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
]

#: Fixed India Standard Time offset (UTC+5:30). No tzdata dependency.
IST_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_utc() -> datetime:
    """Get the current UTC datetime."""
    return datetime.now(timezone.utc)


def to_ist(dt: datetime) -> datetime:
    """Convert a datetime to IST. Naive values are treated as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TIMEZONE)


def format_ist_datetime(dt: datetime) -> str:
    """Format as ``Sep 23 · 20:42 IST`` (user-facing timestamps)."""
    ist = to_ist(dt)
    return f"{ist.strftime('%b %d')} · {ist.strftime('%H:%M')} IST"


def format_ist_time(dt: datetime) -> str:
    """Format as ``20:42 IST``."""
    return f"{to_ist(dt).strftime('%H:%M')} IST"


def format_ist_date(dt: datetime) -> str:
    """Format as ``Sep 23``."""
    return to_ist(dt).strftime("%b %d")


def format_ist_coverage(start: datetime, end: datetime) -> str:
    """Format a coverage window, e.g. ``09:00–21:00 IST`` or with dates."""
    s, e = to_ist(start), to_ist(end)
    if s.date() == e.date():
        return f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')} IST"
    return (
        f"{s.strftime('%b %d · %H:%M')}–"
        f"{e.strftime('%b %d · %H:%M')} IST"
    )


def parse_event_timestamp(value: Any) -> datetime | None:
    """Best-effort parse of a ledger/event timestamp; None when unusable."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_github_timestamp(text)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


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


def start_of_day(dt: datetime | None = None) -> datetime:
    """Get the start of day (00:00:00 UTC) for a given datetime.

    Args:
        dt: Datetime to truncate. Defaults to now.

    Returns:
        Start of day in UTC.
    """
    if dt is None:
        dt = now_utc()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def end_of_day(dt: datetime | None = None) -> datetime:
    """Get the end of day (23:59:59 UTC) for a given datetime.

    Args:
        dt: Datetime to truncate. Defaults to now.

    Returns:
        End of day in UTC.
    """
    if dt is None:
        dt = now_utc()
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
