"""Job orchestration for gh-ops Phase 8.

Thin composition layer between GitHub Actions and the existing Phase 1-7
runtime. Each job is a short sequence of calls into public APIs that already
exist; no intelligence, scoring, state, or formatting logic lives here.

Public API:
    - register_all_jobs: register every job on a dispatcher
    - JOB_* / ALL_JOB_NAMES: the canonical job names, matching the workflows
    - pipeline: shared helpers for config, collection, state, and delivery

The GitHub Actions workflows call ``python -m src.core.dispatcher <job>``,
which routes through this layer.
"""
from src.jobs.jobs import (
    ALL_JOB_NAMES,
    JOB_DAILY,
    JOB_MONITORING,
    JOB_OSS_HUNT,
    JOB_SECURITY,
    JOB_STATUS,
    JOB_WEEKLY_REPORT,
    job_daily,
    job_monitoring,
    job_oss_hunt,
    job_security,
    job_status,
    job_weekly_report,
    register_all_jobs,
)

__all__ = [
    "ALL_JOB_NAMES",
    "JOB_DAILY",
    "JOB_MONITORING",
    "JOB_OSS_HUNT",
    "JOB_SECURITY",
    "JOB_STATUS",
    "JOB_WEEKLY_REPORT",
    "job_daily",
    "job_monitoring",
    "job_oss_hunt",
    "job_security",
    "job_status",
    "job_weekly_report",
    "register_all_jobs",
]
