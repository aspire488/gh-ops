"""Tests for src.utils.time"""
import pytest
from datetime import datetime, timezone, timedelta

from src.utils.time import (
    now_utc,
    parse_github_timestamp,
    days_ago,
    format_relative_time,
    format_github_timestamp,
    is_stale,
    start_of_day,
    end_of_day,
)


class TestParseGithubTimestamp:
    def test_standard_format(self):
        ts = "2026-09-21T08:00:00Z"
        dt = parse_github_timestamp(ts)
        assert dt.year == 2026
        assert dt.month == 9
        assert dt.day == 21
        assert dt.tzinfo is not None

    def test_with_milliseconds(self):
        ts = "2026-09-21T08:00:00.123456Z"
        dt = parse_github_timestamp(ts)
        assert dt.microsecond == 123456

    def test_with_offset(self):
        ts = "2026-09-21T08:00:00+00:00"
        dt = parse_github_timestamp(ts)
        assert dt.tzinfo is not None

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_github_timestamp("not-a-timestamp")


class TestDaysAgo:
    def test_now(self):
        dt = now_utc()
        assert days_ago(dt) == 0

    def test_yesterday(self):
        dt = now_utc() - timedelta(days=1)
        assert days_ago(dt) == 1


class TestFormatRelativeTime:
    def test_just_now(self):
        dt = now_utc()
        result = format_relative_time(dt)
        assert result == "just now"

    def test_minutes_ago(self):
        dt = now_utc() - timedelta(minutes=5)
        result = format_relative_time(dt)
        assert "5 minutes ago" in result

    def test_hours_ago(self):
        dt = now_utc() - timedelta(hours=3)
        result = format_relative_time(dt)
        assert "3 hours ago" in result

    def test_days_ago(self):
        dt = now_utc() - timedelta(days=7)
        result = format_relative_time(dt)
        assert "7 days ago" in result


class TestFormatGithubTimestamp:
    def test_roundtrip(self):
        dt = datetime(2026, 9, 21, 8, 0, 0, tzinfo=timezone.utc)
        ts = format_github_timestamp(dt)
        assert ts == "2026-09-21T08:00:00Z"

    def test_adds_utc_if_naive(self):
        dt = datetime(2026, 9, 21, 8, 0, 0)
        ts = format_github_timestamp(dt)
        assert ts.endswith("Z")


class TestIsStale:
    def test_recent(self):
        dt = now_utc() - timedelta(days=1)
        assert not is_stale(dt, max_age_days=30)

    def test_old(self):
        dt = now_utc() - timedelta(days=60)
        assert is_stale(dt, max_age_days=30)


class TestStartEndOfDay:
    def test_start_of_day(self):
        dt = now_utc()
        sod = start_of_day(dt)
        assert sod.hour == 0
        assert sod.minute == 0
        assert sod.second == 0

    def test_end_of_day(self):
        dt = now_utc()
        eod = end_of_day(dt)
        assert eod.hour == 23
        assert eod.minute == 59
        assert eod.second == 59
