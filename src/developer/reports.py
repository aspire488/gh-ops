"""Developer activity reports for gh-ops Phase 6.

Generates structured DeveloperReports from activity records and statistics.
Reports answer factual questions with data quality context.

CRITICAL: Reports are DESCRIPTIVE, not evaluative.
- "3 PRs merged this week" — factual
- "Productivity increased 20%" — NOT allowed (evaluative)
- "Activity lower than average" — NOT allowed (evaluative)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.developer.activity import (
    ActivityRecord,
    ActivityType,
    DataQuality,
    filter_activity,
    deduplicate_activity,
)
from src.developer.statistics import (
    ActivityCounts,
    PeriodActivity,
    RepoActivity,
    compute_counts,
    compute_by_repository,
    compute_by_period,
    compute_active_days,
    compute_date_range,
    summarize_data_quality,
)
from src.utils.logging import get_logger

logger = get_logger("developer.reports")


# ── Period presets ────────────────────────────────────────────────


class ReportPeriod:
    """Predefined time periods for reports."""

    @staticmethod
    def last(days: int, reference: datetime | None = None) -> tuple[datetime, datetime]:
        """Get (start, end) for the last N days.

        Args:
            days: Number of days.
            reference: End reference point (defaults to now UTC).

        Returns:
            Tuple of (start, end) datetimes.
        """
        if reference is None:
            reference = datetime.now(timezone.utc)
        end = reference
        start = end - timedelta(days=days)
        return (start, end)

    @staticmethod
    def this_week(reference: datetime | None = None) -> tuple[datetime, datetime]:
        """Get (start, end) for the current week (Monday–Sunday)."""
        if reference is None:
            reference = datetime.now(timezone.utc)
        start = reference - timedelta(days=reference.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        return (start, end)

    @staticmethod
    def this_month(reference: datetime | None = None) -> tuple[datetime, datetime]:
        """Get (start, end) for the current month."""
        if reference is None:
            reference = datetime.now(timezone.utc)
        start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return (start, end)


# ── Developer report ─────────────────────────────────────────────


@dataclass(frozen=True)
class DeveloperReport:
    """Structured developer activity report.

    Answers factual questions with data quality context.
    No inference, no scoring, no personality analysis.

    Attributes:
        username: Developer username (or None for repo-level report).
        period_start: Report period start (inclusive).
        period_end: Report period end (exclusive).
        counts: Total activity counts for the period.
        by_repository: Activity breakdown by repository.
        by_period: Activity breakdown by time periods (daily/weekly).
        activity_records: The raw activity records in this period.
        data_quality: Data completeness information.
        generated_at: When this report was generated.
    """

    username: str | None
    period_start: datetime
    period_end: datetime
    counts: ActivityCounts
    by_repository: tuple[RepoActivity, ...]
    by_period: tuple[PeriodActivity, ...]
    activity_records: tuple[ActivityRecord, ...]
    data_quality: dict[str, Any]
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_complete(self) -> bool:
        """True if all data sources are complete."""
        return self.data_quality.get("completeness_pct", 0) == 100.0

    @property
    def active_days(self) -> int:
        """Number of unique days with activity."""
        return compute_active_days(list(self.activity_records))

    @property
    def active_repositories(self) -> list[str]:
        """Sorted list of repositories with activity."""
        return sorted({r.repository for r in self.activity_records if r.repository})

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        return {
            "username": self.username,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "counts": self.counts.to_dict(),
            "by_repository": [r.to_dict() for r in self.by_repository],
            "by_period": [p.to_dict() for p in self.by_period],
            "active_days": self.active_days,
            "active_repositories": self.active_repositories,
            "data_quality": self.data_quality,
            "record_count": len(self.activity_records),
        }


# ── Report generation ────────────────────────────────────────────


def generate_report(
    records: list[ActivityRecord],
    quality: list[DataQuality],
    *,
    username: str | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    period_days: int = 7,
) -> DeveloperReport:
    """Generate a developer activity report.

    This is the main entry point for report generation.

    Args:
        records: Activity records (will be deduplicated).
        quality: Data quality entries from extract_activity.
        username: Filter to this developer's activity.
        period_start: Report period start (inclusive). If None, uses earliest record.
        period_end: Report period end (exclusive). If None, uses latest record + 1 day.
        period_days: Width of period breakdown buckets.

    Returns:
        DeveloperReport with all statistics and data quality info.
    """
    # Deduplicate records
    deduped = deduplicate_activity(records)

    # Determine period boundaries
    if period_start is None or period_end is None:
        date_range = compute_date_range(deduped)
        if date_range:
            earliest, latest = date_range
            if period_start is None:
                period_start = earliest
            if period_end is None:
                period_end = latest + timedelta(days=1)
        else:
            now = datetime.now(timezone.utc)
            period_start = period_start or now
            period_end = period_end or now + timedelta(days=1)

    # Filter to period
    period_records = filter_activity(
        deduped, since=period_start, until=period_end
    )

    # Compute statistics
    counts = compute_counts(period_records)
    by_repo = compute_by_repository(period_records)
    by_period = compute_by_period(period_records, period_days=period_days)
    data_quality = summarize_data_quality(quality)

    report = DeveloperReport(
        username=username,
        period_start=period_start,
        period_end=period_end,
        counts=counts,
        by_repository=tuple(by_repo),
        by_period=tuple(by_period),
        activity_records=tuple(period_records),
        data_quality=data_quality,
    )

    logger.info(
        "Generated report for %s: %d activities, %d repos, %d active days",
        username or "(all)",
        counts.total,
        len(by_repo),
        report.active_days,
    )

    return report


def format_summary(report: DeveloperReport) -> str:
    """Format a report as a human-readable summary.

    Args:
        report: DeveloperReport to format.

    Returns:
        Multi-line summary string.
    """
    lines: list[str] = []

    # Header
    if report.username:
        lines.append(f"Activity Report: @{report.username}")
    else:
        lines.append("Activity Report")
    lines.append(
        f"Period: {report.period_start.strftime('%Y-%m-%d')} to "
        f"{report.period_end.strftime('%Y-%m-%d')}"
    )
    lines.append("")

    # Counts
    counts = report.counts
    lines.append(f"Total activities: {counts.total}")
    lines.append(f"Active days: {report.active_days}")
    lines.append(f"Active repositories: {len(report.active_repositories)}")
    lines.append("")

    # Breakdown
    if counts.issues_created or counts.issues_closed:
        lines.append(
            f"Issues: {counts.issues_created} created, "
            f"{counts.issues_closed} closed, "
            f"{counts.issues_commented} comments"
        )
    if counts.prs_opened or counts.prs_merged or counts.prs_closed:
        lines.append(
            f"PRs: {counts.prs_opened} opened, "
            f"{counts.prs_merged} merged, "
            f"{counts.prs_closed} closed"
        )
    if counts.releases_published:
        lines.append(f"Releases: {counts.releases_published} published")
    if counts.workflow_runs:
        lines.append(f"Workflow runs: {counts.workflow_runs}")

    # Repository breakdown
    if report.by_repository:
        lines.append("")
        lines.append("By repository:")
        for repo in report.by_repository[:10]:
            lines.append(
                f"  {repo.repository}: {repo.counts.total} activities"
            )

    # Data quality
    completeness = report.data_quality.get("completeness_pct", 100.0)
    if completeness < 100.0:
        lines.append("")
        lines.append(
            f"Data completeness: {completeness}% "
            f"({report.data_quality.get('incomplete', 0)} collectors failed)"
        )

    return "\n".join(lines)
