"""Tests for developer statistics computation (Phase 6)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest

from src.developer.activity import ActivityRecord, ActivityType, DataQuality
from src.developer.statistics import (
    ActivityCounts,
    RepoActivity,
    PeriodActivity,
    compute_counts,
    compute_by_repository,
    compute_by_period,
    compute_active_repositories,
    compute_active_days,
    compute_date_range,
    summarize_data_quality,
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


# ── ActivityCounts ───────────────────────────────────────────────


class TestActivityCounts:
    def test_defaults_are_zero(self):
        counts = ActivityCounts()
        assert counts.total == 0
        assert counts.issues_created == 0

    def test_total_sums_all(self):
        counts = ActivityCounts(
            issues_created=3,
            issues_closed=2,
            prs_opened=1,
            prs_merged=1,
        )
        assert counts.total == 7

    def test_to_dict_includes_total(self):
        counts = ActivityCounts(issues_created=5)
        d = counts.to_dict()
        assert d["issues_created"] == 5
        assert d["total"] == 5


# ── compute_counts ───────────────────────────────────────────────


class TestComputeCounts:
    def test_empty_records(self):
        counts = compute_counts([])
        assert counts.total == 0

    def test_counts_by_type(self):
        records = [
            _record(ActivityType.ISSUE_CREATED),
            _record(ActivityType.ISSUE_CREATED),
            _record(ActivityType.PR_OPENED),
            _record(ActivityType.PR_MERGED),
            _record(ActivityType.RELEASE_PUBLISHED),
        ]
        counts = compute_counts(records)
        assert counts.issues_created == 2
        assert counts.prs_opened == 1
        assert counts.prs_merged == 1
        assert counts.releases_published == 1
        assert counts.total == 5


# ── compute_by_repository ────────────────────────────────────────


class TestComputeByRepository:
    def test_empty_records(self):
        result = compute_by_repository([])
        assert result == []

    def test_groups_by_repo(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/repo-a"),
            _record(ActivityType.ISSUE_CREATED, repository="owner/repo-a"),
            _record(ActivityType.PR_OPENED, repository="owner/repo-b"),
        ]
        result = compute_by_repository(records)
        assert len(result) == 2
        # repo-a has 2 activities, repo-b has 1
        assert result[0].repository == "owner/repo-a"
        assert result[0].counts.total == 2
        assert result[1].repository == "owner/repo-b"
        assert result[1].counts.total == 1

    def test_sorts_by_total_descending(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/small"),
            _record(ActivityType.PR_OPENED, repository="owner/big"),
            _record(ActivityType.PR_MERGED, repository="owner/big"),
            _record(ActivityType.RELEASE_PUBLISHED, repository="owner/big"),
        ]
        result = compute_by_repository(records)
        assert result[0].repository == "owner/big"
        assert result[0].counts.total == 3

    def test_items_tracked(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, item_id="owner/repo#1"),
            _record(ActivityType.ISSUE_CREATED, item_id="owner/repo#2"),
        ]
        result = compute_by_repository(records)
        assert len(result[0].items) == 2


# ── compute_by_period ────────────────────────────────────────────


class TestComputeByPeriod:
    def test_empty_records(self):
        result = compute_by_period([])
        assert result == []

    def test_groups_by_period(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-10T10:00:00Z"),
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-12T10:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-20T10:00:00Z"),
        ]
        result = compute_by_period(records, period_days=7)
        assert len(result) >= 2

    def test_period_boundaries(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-01T00:00:00Z"),
        ]
        ref = datetime(2025, 1, 8, 0, 0, 0, tzinfo=timezone.utc)
        result = compute_by_period(records, period_days=7, reference_time=ref)
        assert len(result) == 1
        assert result[0].counts.issues_created == 1


# ── compute_active_repositories ──────────────────────────────────


class TestComputeActiveRepositories:
    def test_empty_records(self):
        assert compute_active_repositories([]) == []

    def test_sorted_unique(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, repository="owner/b-repo"),
            _record(ActivityType.ISSUE_CREATED, repository="owner/a-repo"),
            _record(ActivityType.ISSUE_CREATED, repository="owner/b-repo"),
        ]
        result = compute_active_repositories(records)
        assert result == ["owner/a-repo", "owner/b-repo"]


# ── compute_active_days ──────────────────────────────────────────


class TestComputeActiveDays:
    def test_empty_records(self):
        assert compute_active_days([]) == 0

    def test_counts_unique_days(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-10T10:00:00Z"),
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-10T14:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-12T10:00:00Z"),
        ]
        assert compute_active_days(records) == 2


# ── compute_date_range ───────────────────────────────────────────


class TestComputeDateRange:
    def test_empty_records(self):
        assert compute_date_range([]) is None

    def test_returns_earliest_and_latest(self):
        records = [
            _record(ActivityType.ISSUE_CREATED, ts="2025-01-10T10:00:00Z"),
            _record(ActivityType.PR_OPENED, ts="2025-01-20T10:00:00Z"),
        ]
        earliest, latest = compute_date_range(records)
        assert earliest.day == 10
        assert latest.day == 20


# ── summarize_data_quality ───────────────────────────────────────


class TestSummarizeDataQuality:
    def test_empty_quality(self):
        result = summarize_data_quality([])
        assert result["collectors"] == 0
        assert result["completeness_pct"] == 100.0

    def test_all_complete(self):
        quality = [
            DataQuality(collector="issues", is_complete=True),
            DataQuality(collector="pulls", is_complete=True),
        ]
        result = summarize_data_quality(quality)
        assert result["complete"] == 2
        assert result["incomplete"] == 0
        assert result["completeness_pct"] == 100.0

    def test_partial_failure(self):
        quality = [
            DataQuality(collector="issues", is_complete=True),
            DataQuality(collector="pulls", is_complete=False, error="timeout"),
        ]
        result = summarize_data_quality(quality)
        assert result["complete"] == 1
        assert result["incomplete"] == 1
        assert result["completeness_pct"] == 50.0
