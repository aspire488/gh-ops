"""Tests for developer report generation (Phase 6)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest

from src.developer.activity import ActivityRecord, ActivityType, DataQuality
from src.developer.reports import (
    DeveloperReport,
    ReportPeriod,
    generate_report,
    format_summary,
)


# ── Helpers ──────────────────────────────────────────────────────


def _record(
    activity_type: ActivityType,
    repository: str = "owner/repo",
    item_id: str = "owner/repo#1",
    ts: str = "2025-01-15T10:00:00Z",
) -> ActivityRecord:
    return ActivityRecord(
        activity_type=activity_type,
        timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        repository=repository,
        item_id=item_id,
    )


def _quality(collector: str = "issues", complete: bool = True) -> DataQuality:
    return DataQuality(collector=collector, is_complete=complete, item_count=10)


# ── ReportPeriod ─────────────────────────────────────────────────


class TestReportPeriod:
    def test_last_7_days(self):
        ref = datetime(2025, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
        start, end = ReportPeriod.last(7, reference=ref)
        assert start.day == 13
        assert end.day == 20

    def test_this_week(self):
        # 2025-01-15 is Wednesday (weekday=2)
        ref = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        start, end = ReportPeriod.this_week(reference=ref)
        assert start.weekday() == 0  # Monday
        assert (end - start).days == 7

    def test_this_month(self):
        ref = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        start, end = ReportPeriod.this_month(reference=ref)
        assert start.day == 1
        assert start.month == 1
        assert end.month == 2


# ── generate_report ──────────────────────────────────────────────


class TestGenerateReport:
    def test_empty_records(self):
        report = generate_report([], [])
        assert report.counts.total == 0
        assert report.is_complete is True

    def test_with_records(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-15T10:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-16T10:00:00Z"),
            _record(ActivityType.PR_MERGED, ts="2025-01-17T10:00:00Z"),
        ]
        quality = [_quality("issues"), _quality("pulls")]
        report = generate_report(records, quality, username="testuser")

        assert report.username == "testuser"
        assert report.counts.total == 3
        assert report.counts.issues_created == 1
        assert report.counts.prs_opened == 1
        assert report.counts.prs_merged == 1

    def test_filters_to_period(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-05T10:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-15T10:00:00Z"),
        ]
        report = generate_report(
            records, [],
            period_start=datetime(2025, 1, 10, tzinfo=timezone.utc),
            period_end=datetime(2025, 1, 20, tzinfo=timezone.utc),
        )
        assert report.counts.total == 1

    def test_deduplicates_records(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-15T10:00:00Z"),
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-15T10:00:00Z"),
        ]
        report = generate_report(records, [])
        assert report.counts.total == 1

    def test_by_repository(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/repo-a"),
            _record(ActivityType.PR_OPENED, repository="owner/repo-b"),
        ]
        report = generate_report(records, [])
        assert len(report.by_repository) == 2

    def test_data_quality_included(self):
        quality = [_quality("issues", complete=True), _quality("pulls", complete=False)]
        report = generate_report([], quality)
        assert report.data_quality["completeness_pct"] == 50.0
        assert report.is_complete is False

    def test_active_days(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-10T10:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-10T14:00:00Z"),
            _record(ActivityType.PR_MERGED, ts="2025-01-12T10:00:00Z"),
        ]
        report = generate_report(records, [])
        assert report.active_days == 2

    def test_active_repositories(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/a"),
            _record(ActivityType.PR_OPENED, repository="owner/b"),
        ]
        report = generate_report(records, [])
        assert report.active_repositories == ["owner/a", "owner/b"]

    def test_to_dict_roundtrip(self):
        records = [_record(ActivityType.ISSUE_CREATED)]
        report = generate_report(records, [_quality()])
        d = report.to_dict()
        assert d["counts"]["issues_created"] == 1
        assert "by_repository" in d
        assert "by_period" in d
        assert "data_quality" in d


# ── format_summary ───────────────────────────────────────────────


class TestFormatSummary:
    def test_empty_report(self):
        report = generate_report([], [])
        text = format_summary(report)
        assert "Activity Report" in text
        assert "Total activities: 0" in text

    def test_with_activity(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/repo"),
            _record(ActivityType.PR_OPENED, repository="owner/repo"),
            _record(ActivityType.PR_MERGED, repository="owner/repo"),
        ]
        report = generate_report(records, [], username="testuser")
        text = format_summary(report)
        assert "@testuser" in text
        assert "Issues:" in text
        assert "PRs:" in text

    def test_includes_data_quality_warning(self):
        quality = [_quality("issues", complete=False)]
        report = generate_report([], quality)
        text = format_summary(report)
        assert "Data completeness" in text

    def test_includes_repository_breakdown(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/repo"),
        ]
        report = generate_report(records, [])
        text = format_summary(report)
        assert "By repository:" in text
        assert "owner/repo" in text
