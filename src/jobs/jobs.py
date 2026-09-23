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

from collections.abc import Callable
from typing import Any

from src.core.dispatcher import Dispatcher, JobFunc, JobResult, get_dispatcher
from src.core.errors import ErrorCode, ErrorCollector, ErrorSeverity, GhOpsError
from src.core.models import CurrentState
from src.developer.activity import extract_activity
from src.developer.reports import generate_report
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
    monitor_config,
    monitored_repository_names,
    persist_state,
    state_summary,
)
from src.monitors import run_monitors, summarize_results
from src.notifications.telegram import (
    NotificationResult,
    OssRunSummary,
    RunSummary,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
    notify_oss_run_summary,
    notify_run_summary,
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
) -> tuple[JobResult | None, Any, Any, Any, tuple]:
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
        return _fatal(job, ErrorCode.CONFIG_INVALID, "Could not load configuration", exc), None, None, None, ()

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
        return _fatal(job, ErrorCode.STATE_LOAD_FAILED, "Could not load state", exc), None, None, None, ()

    try:
        snapshot = build_snapshot(client, config, resources)
    except Exception as exc:
        return _fatal(job, ErrorCode.COLLECTOR_FAILED, "Collection failed", exc), None, None, None, ()

    try:
        state, events = apply_and_diff(previous, snapshot)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_VALIDATION_FAILED, "Could not apply snapshot", exc), None, None, None, ()

    try:
        persist_state(state)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc), None, None, None, ()

    failed = failed_collectors(snapshot)
    if failed:
        logger.warning("%s: collector(s) failed: %s", job, ", ".join(failed))

    return None, config, state, snapshot, events


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



def _run_summary(job: str, config: Any, snapshot: Any, monitor_summary: dict[str, Any]) -> NotificationResult | None:
    summary = RunSummary(
        run_type=job,
        repositories_checked=len(monitored_repository_names(config)),
        items_collected=sum(result.item_count for result in snapshot.results.values()),
        alerts_generated=len(monitor_summary.get("alerts", [])),
        state_persisted=True,
    )
    return _safe_notify(job, lambda: notify_run_summary(summary, config=config))

def job_daily(**kwargs: Any) -> JobResult:
    """Repository collection, release/CI detection, and a daily alert digest.

    Collects repos, issues, pulls, releases, and workflow runs for every
    configured repository, then runs Phase 4 monitors over the resulting events.
    """
    failure, config, state, snapshot, _events = _collect_and_persist(
        JOB_DAILY, ("repos", "issues", "pulls", "releases", "workflows")
    )
    if failure is not None:
        return failure

    summary, delivery = _run_monitors_and_notify(state, config)
    run_summary_delivery = _run_summary(JOB_DAILY, config, snapshot, summary)

    return JobResult(
        job=JOB_DAILY,
        success=True,
        data={
            "state": state_summary(state),
            "monitors": summary,
            **_notification_data(delivery),
            "run_summary_notification": _notification_data(run_summary_delivery)["notification"],
        },
    )


def job_monitoring(**kwargs: Any) -> JobResult:
    """CI failure detection plus security alert checks.

    Intended for a frequent schedule; collects workflow runs and security
    alerts only.
    """
    failure, config, state, snapshot, _events = _collect_and_persist(
        JOB_MONITORING, ("workflows", "security")
    )
    if failure is not None:
        return failure

    summary, delivery = _run_monitors_and_notify(state, config)
    run_summary_delivery = _run_summary(JOB_MONITORING, config, snapshot, summary)

    return JobResult(
        job=JOB_MONITORING,
        success=True,
        data={
            "state": state_summary(state),
            "monitors": summary,
            **_notification_data(delivery),
            "run_summary_notification": _notification_data(run_summary_delivery)["notification"],
        },
    )


def job_weekly_report(**kwargs: Any) -> JobResult:
    """Personal activity, statistics, and the weekly developer report.

    Collects the authenticated user plus issues and pulls, converts Phase 3
    events into Phase 6 activity, and delivers a DeveloperReport.
    """
    failure, config, state, _snapshot, events = _collect_and_persist(
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


def _oss_identity(opportunity: Any) -> str:
    """Stable cross-run OSS identity, matching Phase 5 deduplication."""
    repo, number = opportunity.identity()
    return f"{repo}#{number}"


def _notified_oss_identities(state: CurrentState) -> set[str]:
    """Identities already successfully delivered in a previous run."""
    return set(state.resources.get("oss_opportunities", {}))


def _state_with_notified_oss(state: CurrentState, identities: set[str]) -> CurrentState:
    """Return state with ``oss_opportunities`` updated.

    Preserves every other resource type so a concurrent daily snapshot is not
    discarded (same partial-update discipline as Phase 3).
    """
    resources = dict(state.resources)
    resources["oss_opportunities"] = {
        identity: {"notified": True} for identity in sorted(identities)
    }
    return CurrentState(
        resources=resources,
        etags=state.etags,
        update_log=state.update_log,
        last_updated=state.last_updated,
        schema_version=state.schema_version,
    )


def job_oss_hunt(**kwargs: Any) -> JobResult:
    """OSS opportunity discovery, scoring, and delivery.

    Delegates to Phase 5's hunter, then delivers only opportunities that have
    not been successfully notified before. Cross-run dedup state lives under
    the ``oss_opportunities`` resource key in Phase 3 state (at-least-once
    delivery: identities are marked only when Telegram reports success).
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
        previous_state = load_previous_state()
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.STATE_LOAD_FAILED, "Could not load state", exc)

    try:
        hunter_result = run_hunter(client, hunter_settings)
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.SEARCH_FAILED, "OSS hunter failed", exc)

    notified = _notified_oss_identities(previous_state)
    new_opportunities = [
        opp
        for opp in hunter_result.opportunities
        if _oss_identity(opp) not in notified
    ]

    # Exactly one batch delivery per run, even when the list is empty (the
    # formatter emits nothing and the notifier reports "nothing to send").
    delivery = _safe_notify(
        JOB_OSS_HUNT,
        lambda: notify_oss_opportunities(
            new_opportunities,
            config=config,
            limit=hunter_settings.max_results,
        ),
    )
    if delivery is not None and delivery.ok:
        notified.update(_oss_identity(opp) for opp in new_opportunities)
    elif delivery is None:
        logger.warning("%s: opportunity delivery raised; not marking identities", JOB_OSS_HUNT)
    elif not delivery.ok:
        logger.warning(
            "%s: opportunity delivery failed; not marking identities (%s)",
            JOB_OSS_HUNT,
            delivery.failed_chats,
        )

    try:
        persist_state(_state_with_notified_oss(previous_state, notified))
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc)

    summary_delivery = _safe_notify(
        JOB_OSS_HUNT,
        lambda: notify_oss_run_summary(
            OssRunSummary(
                queries_run=hunter_result.queries_run,
                total_issues_found=hunter_result.total_issues_found,
                opportunities=len(hunter_result.opportunities),
                duplicates=hunter_result.total_duplicates,
            ),
            config=config,
        ),
    )

    return JobResult(
        job=JOB_OSS_HUNT,
        success=True,
        data={
            "opportunities": len(hunter_result.opportunities),
            "new_opportunities": len(new_opportunities),
            "queries_run": hunter_result.queries_run,
            "queries_failed": hunter_result.queries_failed,
            **_notification_data(delivery),
            "oss_summary_notification": _notification_data(summary_delivery)["notification"],
        },
    )


def job_security(**kwargs: Any) -> JobResult:
    """Dependabot and code-scanning alert collection."""
    failure, _config, state, _snapshot, _events = _collect_and_persist(JOB_SECURITY, ("security",))
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
