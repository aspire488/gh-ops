"""The six gh-ops jobs.

Each job is a thin sequence of calls into existing Phase 1-7 public APIs.
No business logic lives here: collectors fetch, the state engine diffs and
persists, monitors/intelligence/developer interpret, notifications deliver.

Failure semantics (deliberate and documented):

- A **collector** failure is partial by design. State keeps the previous valid
  data (Phase 3), the failure is recorded in ``update_log``, and the job still
  completes. "One bad repository does not kill the run."
- A **fatal** failure — bad config, missing credentials, unreadable state —
  fails the job and returns a non-zero exit code.
- Notification failures never fail a job. ``NotificationResult`` already reports
  them, and a delivery problem must not discard collected state.
"""
from __future__ import annotations

from typing import Any, Callable

from src.core.dispatcher import Dispatcher, JobResult, JobFunc, get_dispatcher
from src.core.errors import ErrorCode, ErrorCollector, ErrorSeverity, GhOpsError
from src.developer.reports import generate_report
from src.developer.activity import extract_activity
from src.github.client import GitHubClient
from src.intelligence.oss.hunter import HunterConfig, run_hunter
from src.jobs.pipeline import (
    apply_and_diff,
    authenticated_username,
    build_snapshot,
    failed_collectors,
    hunter_config,
    load_previous_state,
    load_runtime_config,
    monitored_repository_names,
    monitor_config,
    persist_state,
    state_summary,
)
from src.monitors import run_monitors, summarize_results
from src.notifications.telegram import (
    NotificationResult,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
)
from src.utils.logging import get_logger

logger = get_logger("jobs")

#: Job names, matching .github/workflows/*.yml one-to-one.
JOB_DAILY = "daily"
JOB_MONITORING = "monitoring"
JOB_WEEKLY_REPORT = "weekly-report"
JOB_OSS_HUNT = "oss-hunt"
JOB_SECURITY = "security"
JOB_STATUS = "status"

ALL_JOB_NAMES = (
    JOB_DAILY,
    JOB_MONITORING,
    JOB_WEEKLY_REPORT,
    JOB_OSS_HUNT,
    JOB_SECURITY,
    JOB_STATUS,
)


# ── Helpers ──────────────────────────────────────────────────────


def _fatal(job: str, code: ErrorCode, message: str, exc: Exception | None = None) -> JobResult:
    """Build a failed JobResult for a fatal condition."""
    errors = ErrorCollector()
    errors.add(GhOpsError(
        code=code,
        message=message,
        module=f"jobs.{job}",
        severity=ErrorSeverity.CRITICAL,
        cause=exc,
    ))
    logger.error("%s: %s", job, message)
    return JobResult(job=job, success=False, errors=errors)


def _notification_data(result: NotificationResult | None) -> dict[str, Any]:
    """Summarize a notification outcome for the job's data payload."""
    if result is None:
        return {"notification": {"error": "delivery raised an unexpected exception"}}
    return {
        "notification": result.to_dict(),
    }


def _safe_notify(
    job: str,
    deliver: Callable[[], NotificationResult],
) -> NotificationResult | None:
    """Deliver a notification without ever failing the job.

    Delivery is the last step of every job and the state has already been
    persisted by this point. If Telegram is unreachable or misconfigured, the
    collected state must still survive, so delivery failures are logged and
    reported in the job's data rather than raised.

    Args:
        job: Job name, for the log message.
        deliver: Zero-argument callable performing the delivery.

    Returns:
        The NotificationResult, or None if delivery raised.
    """
    try:
        return deliver()
    except Exception as exc:
        # Only the exception type is logged: delivery errors can embed the
        # request URL, which contains the bot token.
        logger.error("%s: notification delivery failed (%s)", job, type(exc).__name__)
        return None


def _collect_and_persist(
    job: str,
    resources: tuple[str, ...],
) -> tuple[JobResult | None, Any, Any, tuple]:
    """Shared collect → apply → persist sequence.

    The configured ``Config`` is returned so the caller does not have to parse
    the YAML files a second time.

    Returns:
        ``(failure, config, state, events)``. When ``failure`` is not None the
        caller must return it immediately, and the remaining values are None.
    """
    try:
        config = load_runtime_config()
    except Exception as exc:
        return _fatal(job, ErrorCode.CONFIG_INVALID, "Could not load configuration", exc), None, None, ()

    try:
        client = GitHubClient()
    except Exception as exc:
        return (
            _fatal(job, ErrorCode.API_AUTH_FAILED, "Could not resolve GitHub credentials", exc),
            None,
            None,
            (),
        )

    repositories = monitored_repository_names(config)
    logger.info("%s: collecting %s across %d repositories", job, ",".join(resources), len(repositories))

    try:
        previous = load_previous_state()
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_LOAD_FAILED, "Could not load state", exc), None, None, ()

    try:
        snapshot = build_snapshot(client, config, resources)
    except Exception as exc:
        return _fatal(job, ErrorCode.COLLECTOR_FAILED, "Collection failed", exc), None, None, ()

    try:
        state, events = apply_and_diff(previous, snapshot)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_VALIDATION_FAILED, "Could not apply snapshot", exc), None, None, ()

    try:
        persist_state(state)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc), None, None, ()

    failed = failed_collectors(snapshot)
    if failed:
        logger.warning("%s: collector(s) failed: %s", job, ", ".join(failed))

    return None, config, state, events


def _run_monitors_and_notify(
    state: Any,
    config: Any,
) -> tuple[dict[str, Any], NotificationResult | None]:
    """Run Phase 4 monitors and deliver any alerts.

    Returns:
        ``(summary, delivery)``. ``delivery`` is None only when the delivery
        call itself raised, which is contained rather than propagated.
    """
    results = run_monitors(state, monitor_config(config))
    summary = summarize_results(results)

    delivery = _safe_notify("monitors", lambda: notify_monitor_alerts(results, config=config))
    if delivery is not None and not delivery.skipped and not delivery.ok:
        logger.warning("monitor alert delivery reported failures: %s", delivery.failed_chats)
    elif delivery is None:
        logger.warning("monitor alert delivery raised; alerts were not delivered")

    return summary, delivery


# ── Jobs ─────────────────────────────────────────────────────────


def job_daily(**kwargs: Any) -> JobResult:
    """Repository collection, release/CI detection, and a daily alert digest.

    Collects repos, issues, pulls, releases, and workflow runs for every
    configured repository, then runs Phase 4 monitors over the resulting events.
    """
    failure, config, state, _events = _collect_and_persist(
        JOB_DAILY, ("repos", "issues", "pulls", "releases", "workflows")
    )
    if failure is not None:
        return failure

    summary, delivery = _run_monitors_and_notify(state, config)

    return JobResult(
        job=JOB_DAILY,
        success=True,
        data={
            "state": state_summary(state),
            "monitors": summary,
            **_notification_data(delivery),
        },
    )


def job_monitoring(**kwargs: Any) -> JobResult:
    """CI failure detection plus security alert checks.

    Intended for a frequent schedule; collects workflow runs and security
    alerts only.
    """
    failure, config, state, _events = _collect_and_persist(
        JOB_MONITORING, ("workflows", "security")
    )
    if failure is not None:
        return failure

    summary, delivery = _run_monitors_and_notify(state, config)

    return JobResult(
        job=JOB_MONITORING,
        success=True,
        data={
            "state": state_summary(state),
            "monitors": summary,
            **_notification_data(delivery),
        },
    )


def job_weekly_report(**kwargs: Any) -> JobResult:
    """Personal activity, statistics, and the weekly developer report.

    Collects the authenticated user plus issues and pulls, converts Phase 3
    events into Phase 6 activity, and delivers a DeveloperReport.
    """
    failure, config, state, events = _collect_and_persist(
        JOB_WEEKLY_REPORT, ("user", "issues", "pulls")
    )
    if failure is not None:
        return failure

    username = authenticated_username(state)

    records, quality = extract_activity(state, events, username)
    report = generate_report(records, quality, username=username)
    delivery = _safe_notify(
        JOB_WEEKLY_REPORT, lambda: notify_developer_report(report, config=config)
    )

    return JobResult(
        job=JOB_WEEKLY_REPORT,
        success=True,
        data={
            "username": username,
            "records": len(records),
            "report": report.to_dict(),
            **_notification_data(delivery),
        },
    )


def job_oss_hunt(**kwargs: Any) -> JobResult:
    """OSS opportunity discovery, scoring, and delivery.

    Delegates entirely to Phase 5's hunter. Persisting discovered opportunities
    is deliberately not done here: there is no Phase 5 state layer, and inventing
    one would duplicate Phase 3.
    """
    try:
        config = load_runtime_config()
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.CONFIG_INVALID, "Could not load configuration", exc)

    try:
        client = GitHubClient()
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.API_AUTH_FAILED, "Could not resolve GitHub credentials", exc)

    hunter_settings = HunterConfig.from_dict(hunter_config(config))
    if not hunter_settings.enabled:
        logger.info("%s: hunter disabled in configuration", JOB_OSS_HUNT)
        return JobResult(job=JOB_OSS_HUNT, success=True, data={"disabled": True})

    try:
        hunter_result = run_hunter(client, hunter_settings)
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.SEARCH_FAILED, "OSS hunter failed", exc)

    delivery = _safe_notify(
        JOB_OSS_HUNT,
        lambda: notify_oss_opportunities(
            hunter_result.opportunities,
            config=config,
            limit=hunter_settings.max_results,
        ),
    )

    return JobResult(
        job=JOB_OSS_HUNT,
        success=True,
        data={
            "opportunities": len(hunter_result.opportunities),
            "queries_run": hunter_result.queries_run,
            "queries_failed": hunter_result.queries_failed,
            **_notification_data(delivery),
        },
    )


def job_security(**kwargs: Any) -> JobResult:
    """Dependabot and code-scanning alert collection."""
    failure, _config, state, _events = _collect_and_persist(JOB_SECURITY, ("security",))
    if failure is not None:
        return failure

    alerts = state.resources.get("security", {})
    return JobResult(
        job=JOB_SECURITY,
        success=True,
        data={
            "state": state_summary(state),
            "alerts": len(alerts),
        },
    )


def job_status(**kwargs: Any) -> JobResult:
    """Read-only report of current state and configuration.

    Performs no collection and no writes, so it is safe to run at any time.
    """
    try:
        config = load_runtime_config()
        state = load_previous_state()
    except Exception as exc:
        return _fatal(JOB_STATUS, ErrorCode.STATE_LOAD_FAILED, "Could not read state", exc)

    return JobResult(
        job=JOB_STATUS,
        success=True,
        data={
            "state": state_summary(state),
            "repositories": monitored_repository_names(config),
            "jobs": list(ALL_JOB_NAMES),
        },
    )


_JOB_FUNCTIONS: dict[str, JobFunc] = {
    JOB_DAILY: job_daily,
    JOB_MONITORING: job_monitoring,
    JOB_WEEKLY_REPORT: job_weekly_report,
    JOB_OSS_HUNT: job_oss_hunt,
    JOB_SECURITY: job_security,
    JOB_STATUS: job_status,
}


def register_all_jobs(dispatcher: Dispatcher | None = None) -> Dispatcher:
    """Register every job on a dispatcher.

    Args:
        dispatcher: Target dispatcher. Defaults to the shared instance.

    Returns:
        The dispatcher with all jobs registered.
    """
    target = dispatcher if dispatcher is not None else get_dispatcher()
    for name, func in _JOB_FUNCTIONS.items():
        target.register(name, func)
    return target
