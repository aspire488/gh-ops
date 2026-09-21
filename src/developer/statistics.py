"""Descriptive statistics for developer activity.

Computes factual, descriptive statistics from normalized activity records.
No inference, no scoring, no personality analysis. Numbers only.

CRITICAL: Statistics answer "what happened" — not "how good is the developer."
No productivity scores, no burnout detection, no behavioral inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.developer.activity import (
    ActivityRecord,
    ActivityType,
    DataQuality,
)
from src.utils.logging import get_logger

logger = get_logger("developer.statistics")


# ── Activity counts ──────────────────────────────────────────────


@dataclass(frozen=True)
class ActivityCounts:
    """Raw counts of each activity type.

    Attributes:
        issues_created: Number of issues created.
        issues_closed: Number of issues closed.
        issues_commented: Number of issue comments.
        prs_opened: Number of PRs opened.
        prs_merged: Number of PRs merged.
        prs_closed: Number of PRs closed (not merged).
        releases_published: Number of releases published.
        workflow_runs: Number of workflow runs.
    """

    issues_created: int = 0
    issues_closed: int = 0
    issues_commented: int = 0
    prs_opened: int = 0
    prs_merged: int = 0
    prs_closed: int = 0
    releases_published: int = 0
    workflow_runs: int = 0

    @property
    def total(self) -> int:
        """Total activity count."""
        return (
            self.issues_created
            + self.issues_closed
            + self.issues_commented
            + self.prs_opened
            + self.prs_merged
            + self.prs_closed
            + self.releases_published
            + self.workflow_runs
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "issues_created": self.issues_created,
            "issues_closed": self.issues_closed,
            "issues_commented": self.issues_commented,
            "prs_opened": self.prs_opened,
            "prs_merged": self.prs_merged,
            "prs_closed": self.prs_closed,
            "releases_published": self.releases_published,
            "workflow_runs": self.workflow_runs,
            "total": self.total,
        }


# ── Repository breakdown ─────────────────────────────────────────


@dataclass(frozen=True)
class RepoActivity:
    """Activity counts for a single repository.

    Attributes:
        repository: Repository full name.
        counts: Activity counts for this repo.
        items: Set of item IDs active in this repo.
    """

    repository: str
    counts: ActivityCounts
    items: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "counts": self.counts.to_dict(),
            "items": sorted(self.items),
        }


# ── Time period breakdown ────────────────────────────────────────


@dataclass(frozen=True)
class PeriodActivity:
    """Activity within a specific time period.

    Attributes:
        period_start: Start of the period (inclusive).
        period_end: End of the period (exclusive).
        counts: Activity counts for this period.
    """

    period_start: datetime
    period_end: datetime
    counts: ActivityCounts

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "counts": self.counts.to_dict(),
        }


# ── Statistics computation ───────────────────────────────────────


def _count_activity(records: list[ActivityRecord]) -> ActivityCounts:
    """Count activity records by type."""
    counts: dict[str, int] = {}
    for record in records:
        key = record.activity_type.value
        counts[key] = counts.get(key, 0) + 1

    return ActivityCounts(
        issues_created=counts.get(ActivityType.ISSUE_CREATED.value, 0),
        issues_closed=counts.get(ActivityType.ISSUE_CLOSED.value, 0),
        issues_commented=counts.get(ActivityType.ISSUE_COMMENTED.value, 0),
        prs_opened=counts.get(ActivityType.PR_OPENED.value, 0),
        prs_merged=counts.get(ActivityType.PR_MERGED.value, 0),
        prs_closed=counts.get(ActivityType.PR_CLOSED.value, 0),
        releases_published=counts.get(ActivityType.RELEASE_PUBLISHED.value, 0),
        workflow_runs=counts.get(ActivityType.WORKFLOW_RUN.value, 0),
    )


def compute_counts(records: list[ActivityRecord]) -> ActivityCounts:
    """Compute activity counts from records.

    Args:
        records: Activity records to count.

    Returns:
        ActivityCounts with totals per type.
    """
    return _count_activity(records)


def compute_by_repository(records: list[ActivityRecord]) -> list[RepoActivity]:
    """Compute activity breakdown by repository.

    Args:
        records: Activity records.

    Returns:
        List of RepoActivity, sorted by total activity descending,
        then by repository name for deterministic ordering.
    """
    by_repo: dict[str, list[ActivityRecord]] = {}
    for record in records:
        repo = record.repository or "(unknown)"
        by_repo.setdefault(repo, []).append(record)

    result: list[RepoActivity] = []
    for repo, repo_records in by_repo.items():
        counts = _count_activity(repo_records)
        items = frozenset(r.item_id for r in repo_records)
        result.append(RepoActivity(
            repository=repo,
            counts=counts,
            items=items,
        ))

    # Sort by total descending, then name ascending
    result.sort(key=lambda r: (-r.counts.total, r.repository))
    return result


def compute_by_period(
    records: list[ActivityRecord],
    period_days: int = 7,
    reference_time: datetime | None = None,
) -> list[PeriodActivity]:
    """Compute activity breakdown by time periods.

    Divides the activity timeline into fixed-width periods of `period_days`
    days, counted backward from `reference_time`.

    Args:
        records: Activity records.
        period_days: Width of each period in days.
        reference_time: End reference point (defaults to now UTC).

    Returns:
        List of PeriodActivity, one per period, sorted chronologically.
    """
    if not records:
        return []

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    # Find the earliest activity
    earliest = min(r.timestamp for r in records)

    # Generate period boundaries
    periods: list[PeriodActivity] = []
    current_start = earliest.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )

    while current_start < reference_time:
        current_end = current_start + timedelta(days=period_days)
        if current_end > reference_time:
            current_end = reference_time

        # Filter records in this period
        period_records = [
            r for r in records
            if current_start <= r.timestamp < current_end
        ]
        counts = _count_activity(period_records)
        periods.append(PeriodActivity(
            period_start=current_start,
            period_end=current_end,
            counts=counts,
        ))

        current_start = current_end

    return periods


def compute_active_repositories(records: list[ActivityRecord]) -> list[str]:
    """List repositories with activity, sorted by name.

    Args:
        records: Activity records.

    Returns:
        Sorted list of repository names with activity.
    """
    repos = {r.repository for r in records if r.repository}
    return sorted(repos)


def compute_active_days(records: list[ActivityRecord]) -> int:
    """Count the number of unique days with activity.

    Args:
        records: Activity records.

    Returns:
        Number of distinct days with at least one activity.
    """
    days: set[str] = set()
    for record in records:
        days.add(record.timestamp.strftime("%Y-%m-%d"))
    return len(days)


def compute_date_range(
    records: list[ActivityRecord],
) -> tuple[datetime, datetime] | None:
    """Get the date range of activity.

    Args:
        records: Activity records.

    Returns:
        Tuple of (earliest, latest) timestamps, or None if no records.
    """
    if not records:
        return None

    timestamps = [r.timestamp for r in records]
    return (min(timestamps), max(timestamps))


def summarize_data_quality(quality: list[DataQuality]) -> dict[str, Any]:
    """Summarize data quality across collectors.

    Args:
        quality: Data quality entries.

    Returns:
        Summary dict with completeness and completeness_pct.
    """
    if not quality:
        return {"collectors": 0, "complete": 0, "incomplete": 0, "completeness_pct": 100.0}

    complete = sum(1 for q in quality if q.is_complete)
    total = len(quality)

    return {
        "collectors": total,
        "complete": complete,
        "incomplete": total - complete,
        "completeness_pct": round((complete / total) * 100, 1) if total > 0 else 100.0,
        "details": [q.to_dict() for q in quality],
    }
