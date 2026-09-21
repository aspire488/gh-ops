"""Developer intelligence for gh-ops Phase 6.

Provides factual, descriptive activity reports. No inference, no scoring,
no personality analysis. Answers: what happened, when, where.
"""
from src.developer.activity import (
    ActivityRecord,
    ActivityType,
    DataQuality,
    extract_activity,
    filter_activity,
    deduplicate_activity,
    summarize_activity,
)
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
from src.developer.reports import (
    DeveloperReport,
    ReportPeriod,
    generate_report,
    format_summary,
)

__all__ = [
    # Activity models
    "ActivityRecord",
    "ActivityType",
    "DataQuality",
    "extract_activity",
    "filter_activity",
    "deduplicate_activity",
    "summarize_activity",
    # Statistics models
    "ActivityCounts",
    "RepoActivity",
    "PeriodActivity",
    "compute_counts",
    "compute_by_repository",
    "compute_by_period",
    "compute_active_repositories",
    "compute_active_days",
    "compute_date_range",
    "summarize_data_quality",
    # Report models
    "DeveloperReport",
    "ReportPeriod",
    "generate_report",
    "format_summary",
]
