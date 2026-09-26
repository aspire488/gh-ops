"""The six gh-ops jobs.

Each job is a thin sequence of calls into existing Phase 1-7 public APIs.
No business logic lives here: collectors fetch, the state engine diffs and
presents, monitors/intelligence/developer interpret, notifications deliver.

Reporting: domain results become ReportEvents in a shared ledger. Immediate
alerts use notify_monitor_alerts; daily/weekly briefs use notify_report on the
run_summary topic. Completion summaries (run/oss/security) are no longer sent.

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
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.dispatcher import Dispatcher, JobFunc, JobResult, get_dispatcher
from src.core.errors import ErrorCode, ErrorCollector, ErrorSeverity, GhOpsError
from src.core.lifecycle import reconcile_repository_lifecycle
from src.core.models import CurrentState, ResourceEvent
from src.developer.activity import extract_activity
from src.developer.reports import generate_report
from src.github.client import GitHubClient
from src.intelligence.oss.hunter import HunterConfig, run_hunter
from src.intelligence.security import (
    SECURITY_NOTIFIED_KEY,
    RadarConfig,
    analyze_security,
)
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
from src.monitors import evaluate_events, run_monitors, summarize_results
from src.notifications.telegram import (
    TOPIC_ALERTS,
    TOPIC_OSS_OPPORTUNITIES,
    TOPIC_RUN_SUMMARY,
    TOPIC_SECURITY,
    NotificationResult,
    OssRunSummary,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
    notify_report,
    notify_security_alerts,
)
from src.reporting.convert import (
    MONITORING_SUBSYSTEMS,
    report_event_from_activity,
    report_event_from_monitor,
)
from src.reporting.ledger import (
    events_for_brief,
    filter_notifiable,
    load_ledger,
    mark_briefed,
    mark_delivered,
    merge_events,
    prune_ledger,
    resolve_conditions,
    state_with_ledger,
    undelivered_events,
)
from src.reporting.model import SUBSYSTEM_DAILY, SUBSYSTEM_DEVELOPER, ReportEvent, Severity
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

    Returns:
        ``(failure, config, state, snapshot, events)``. When ``failure`` is not
        None the caller must return it immediately, and the remaining values are
        None (except events which is empty tuple on early failures).
    """
    empty: tuple = ()
    try:
        config = load_runtime_config()
    except Exception as exc:
        return _fatal(job, ErrorCode.CONFIG_INVALID, "Could not load configuration", exc), None, None, None, empty

    try:
        client = GitHubClient()
    except Exception as exc:
        return (
            _fatal(job, ErrorCode.API_AUTH_FAILED, "Could not resolve GitHub credentials", exc),
            None,
            None,
            None,
            empty,
        )

    repositories = monitored_repository_names(config)
    logger.info("%s: collecting %s across %d repositories", job, ",".join(resources), len(repositories))

    try:
        previous = load_previous_state()
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_LOAD_FAILED, "Could not load state", exc), None, None, None, empty

    # Config is the source of truth: retire repos removed from repositories.yml
    # and prune their state before diffing so monitors never see synthetic
    # REMOVED events for a config change.
    try:
        previous, _retired = reconcile_repository_lifecycle(previous, repositories)
    except Exception as exc:
        return _fatal(
            job, ErrorCode.STATE_VALIDATION_FAILED, "Could not reconcile repository lifecycle", exc
        ), None, None, None, empty

    try:
        snapshot = build_snapshot(client, config, resources)
    except Exception as exc:
        return _fatal(job, ErrorCode.COLLECTOR_FAILED, "Collection failed", exc), None, None, None, empty

    try:
        state, events = apply_and_diff(previous, snapshot)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_VALIDATION_FAILED, "Could not apply snapshot", exc), None, None, None, empty

    try:
        persist_state(state)
    except Exception as exc:
        return _fatal(job, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc), None, None, None, empty

    failed = failed_collectors(snapshot)
    if failed:
        logger.warning("%s: collector(s) failed: %s", job, ", ".join(failed))

    return None, config, state, snapshot, events


def _evaluate_and_monitor(
    state: CurrentState,
    config: Any,
    events: tuple[ResourceEvent, ...],
) -> tuple[list[Any], dict[str, Any]]:
    """Run event evaluators (Phase 4) plus batch monitors; return results + summary."""
    m_config = monitor_config(config)
    try:
        event_results = evaluate_events(state, m_config, list(events))
    except Exception as exc:
        logger.warning("%s: evaluate_events failed (%s)", "monitors", type(exc).__name__)
        event_results = []
    batch_results = run_monitors(state, m_config)
    # Deduplicate by (monitor, resource, status, summary) preserving order.
    seen: set[tuple] = set()
    all_results: list[Any] = []
    for result in event_results + batch_results:
        key = (result.monitor, result.resource, result.status, result.summary)
        if key in seen:
            continue
        seen.add(key)
        all_results.append(result)
    summary = summarize_results(all_results)
    return all_results, summary


def _report_events_from_results(results: list[Any]) -> list[ReportEvent]:
    events: list[ReportEvent] = []
    for result in results:
        event = report_event_from_monitor(result)
        if event is not None:
            events.append(event)
    return events


def _persist_ledger(state: CurrentState, ledger: dict[str, dict]) -> None:
    """Best-effort second persist of the reporting ledger after delivery."""
    try:
        persist_state(state_with_ledger(state, prune_ledger(ledger)))
    except Exception as exc:
        logger.warning("%s: ledger persist failed (%s)", "reporting", type(exc).__name__)


def _deliver_monitoring_alerts(
    job: str,
    state: CurrentState,
    config: Any,
    results: list[Any],
    report_events: list[ReportEvent],
    ledger: dict[str, dict],
) -> tuple[NotificationResult | None, dict[str, dict]]:
    """Deliver immediate alerts and mark delivered ledger entries.

    Persistent-condition suppression: results whose report events are already
    NOTIFIED/OPEN for the same identity are filtered out before Telegram is
    called, so an ongoing CI failure or collection error is announced once.
    Report allocation is the gate: only events allocated to the immediate
    mode reach this path (monitoring subsystems always are).
    """
    from src.intelligence.attention import ReportMode, allocation_for
    from src.reporting.ledger import event_should_notify

    notifiable_events = [
        event
        for event in filter_notifiable(ledger, report_events)
        if ReportMode.IMMEDIATE in allocation_for(event)
    ]
    notifiable_results = [
        result
        for result in results
        if (event := report_event_from_monitor(result)) is not None
        and event_should_notify(ledger, event)
        and ReportMode.IMMEDIATE in allocation_for(event)
    ]

    delivery = None
    if notifiable_results:
        delivery = _safe_notify(
            job, lambda: notify_monitor_alerts(notifiable_results, config=config)
        )
        if delivery is not None and not delivery.skipped and not delivery.ok:
            logger.warning(
                "monitor alert delivery reported failures: %s", delivery.failed_chats
            )
        elif delivery is None:
            logger.warning("monitor alert delivery raised; alerts were not delivered")

    # Retry previously undelivered monitoring events not in this batch.
    current_ids = {event.identity for event in report_events}
    retry = [
        event
        for event in undelivered_events(ledger)
        if event.subsystem in MONITORING_SUBSYSTEMS and event.identity not in current_ids
    ]
    if retry:
        from src.reporting.builders import build_report as _build_report
        from src.reporting.builders import report_name_for_events as _report_name_for_events

        built = _build_report(retry, report_name=_report_name_for_events(retry))
        if built is not None:
            title, blocks = built
            retry_delivery = _safe_notify(
                job,
                lambda: notify_report(title, blocks, topic=TOPIC_ALERTS, config=config),
            )
            if retry_delivery is not None and retry_delivery.ok:
                ledger = mark_delivered(
                    ledger, [event.identity or event.title for event in retry]
                )

    delivered_ids = [
        event.identity for event in notifiable_events if event.identity
    ]
    if (
        delivery is not None
        and (delivery.ok or delivery.skip_reason == "nothing to send")
        and delivered_ids
    ):
        ledger = mark_delivered(ledger, delivered_ids)
        ledger = resolve_conditions(ledger, notifiable_events)

    return delivery, ledger


def _deliver_developer_events(
    job: str,
    config: Any,
    activity_events: list[ReportEvent],
    ledger: dict[str, dict],
) -> tuple[NotificationResult | None, dict[str, dict]]:
    """Deliver undelivered developer report events (daily immediate path)."""
    if not activity_events:
        return None, ledger
    pending = [
        event
        for event in undelivered_events(ledger)
        if event.subsystem == SUBSYSTEM_DEVELOPER
    ]
    # Include newly merged ones not yet in undelivered view.
    for event in activity_events:
        if event.identity and event.identity not in {e.identity for e in pending}:
            pending.append(event)

    if not pending:
        return None, ledger

    from src.reporting.builders import build_report as _build_report

    built = _build_report(pending, report_name="DEVELOPER")
    if built is None:
        return None, ledger
    title, blocks = built
    delivery = _safe_notify(
        job,
        lambda: notify_report(title, blocks, topic="developer_report", config=config),
    )
    if delivery is not None and delivery.ok:
        ledger = mark_delivered(
            ledger, [event.identity for event in pending if event.identity]
        )
    return delivery, ledger


def _data_quality_event(
    quality_id: str,
    incomplete: int,
    collectors: int,
    observed_at: datetime,
) -> ReportEvent:
    """Ledger representation of a data-quality warning for brief gating."""
    return ReportEvent(
        subsystem=SUBSYSTEM_DAILY,
        event_type="data_quality",
        severity=Severity.INFORMATION,
        title=f"Data quality: {incomplete}/{collectors} collectors incomplete",
        identity=quality_id,
        timestamp=observed_at.isoformat(),
        source="daily",
        metadata={"incomplete": incomplete, "collectors": collectors},
    )


def _gate_data_quality(
    ledger: dict[str, dict],
    data_quality: dict[str, Any] | None,
    pending: list[ReportEvent],
    kind: str,
    now: datetime,
) -> tuple[dict[str, dict], bool, list[ReportEvent], str]:
    """Ledger-ize the data-quality note so unchanged issues stay silent.

    The note is merged as an INFORMATION event and immediately marked
    delivered (it is brief content, never an alerts-path retry). It shows
    in a brief only when this window has not yet briefed the same counts;
    a healthy or unchanged run re-sends nothing.

    Returns ``(ledger, show_quality, pending, quality_id)``.
    """
    if not data_quality:
        return ledger, False, pending, ""
    incomplete = int(data_quality.get("incomplete") or 0)
    collectors = int(data_quality.get("collectors") or 0)
    if incomplete <= 0 or collectors <= 0:
        return ledger, False, pending, ""

    quality_id = f"data_quality:{incomplete}/{collectors}"
    ledger = merge_events(
        ledger, [_data_quality_event(quality_id, incomplete, collectors, now)]
    )
    show_quality = not bool(ledger.get(quality_id, {}).get(f"briefed_{kind}"))
    ledger = mark_delivered(ledger, [quality_id])
    pending = [event for event in pending if event.event_type != "data_quality"]
    return ledger, show_quality, pending, quality_id


def _context_headline(
    events: list[ReportEvent],
    ledger: dict[str, dict],
    purpose: str,
    now: datetime,
) -> str | None:
    """Laya System-1 -> cloud LLM System-2 headline for an intelligence context.

    Display-only and fail-soft: any problem returns None and the brief
    renders exactly as the deterministic pipeline produced it.
    """
    try:
        from src.intelligence.context import build_context
        from src.intelligence.interpret import interpret_context

        context = build_context(events, ledger=ledger, purpose=purpose, now=now)
        return interpret_context(context)
    except Exception:
        return None


def _brief_extra_sections(
    pending: list[ReportEvent],
    ledger: dict[str, dict],
    now: datetime,
) -> list[str]:
    """Repository signals, trend lines, and focus blocks for a brief."""
    from src.intelligence.temporal import build_trends
    from src.reporting.narratives import (
        focus_blocks,
        repository_signal_blocks,
        trend_blocks,
    )

    signals = repository_signal_blocks(pending)
    trend_lines = [trend.line for trend in build_trends(ledger, now=now)]
    focus = focus_blocks(pending)
    return [*signals, *trend_blocks(trend_lines), *focus]


def _maybe_send_daily_brief(
    job: str,
    config: Any,
    state: CurrentState,
    ledger: dict[str, dict],
) -> tuple[NotificationResult | None, dict[str, dict]]:
    """Send the daily brief when run_summary is enabled and content exists."""
    resolved_enabled = True
    try:
        from src.notifications.telegram import load_telegram_config

        resolved_enabled = load_telegram_config(config).run_summary
    except Exception:
        resolved_enabled = True
    if not resolved_enabled:
        return None, ledger

    from src.developer.activity import DataQuality
    from src.developer.statistics import summarize_data_quality
    from src.reporting.builders import build_daily_brief

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    pending = events_for_brief(ledger, kind="daily", window=timedelta(hours=24), end=end)
    repository_count = len(monitored_repository_names(config))

    quality_entries: list[DataQuality] = []
    for collector_name, log_entry in (state.update_log or {}).items():
        status = log_entry.get("status", "unknown")
        quality_entries.append(
            DataQuality(
                collector=collector_name,
                is_complete=status == "success",
                observed_at=str(log_entry.get("observed_at") or ""),
                error=log_entry.get("error") if status == "failure" else None,
                item_count=int(log_entry.get("item_count") or 0),
            )
        )
    data_quality = summarize_data_quality(quality_entries)

    ledger, show_quality, pending, quality_id = _gate_data_quality(
        ledger, data_quality, pending, "daily", end
    )
    if not pending and not show_quality:
        return None, ledger

    headline = _context_headline(pending, ledger, "daily", end)
    built = build_daily_brief(
        pending,
        coverage_start=start,
        coverage_end=end,
        repository_count=repository_count,
        data_quality=data_quality if show_quality else None,
        headline=headline,
        extra_sections=_brief_extra_sections(pending, ledger, end),
    )
    if built is None:
        return None, ledger
    title, blocks = built
    delivery = _safe_notify(
        job,
        lambda: notify_report(title, blocks, topic=TOPIC_RUN_SUMMARY, config=config),
    )
    if delivery is not None and (delivery.ok or delivery.skip_reason in ("nothing to send", "nothing to report")):
        briefed = [event.identity for event in pending if event.identity]
        if show_quality and quality_id:
            briefed.append(quality_id)
        ledger = mark_briefed(ledger, briefed, "daily")
    return delivery, ledger


def _maybe_send_weekly_brief(
    job: str,
    config: Any,
    ledger: dict[str, dict],
    *,
    developer_events: int = 0,
    state: CurrentState | None = None,
) -> tuple[NotificationResult | None, dict[str, dict]]:
    """Send the weekly brief when there is content."""
    try:
        from src.notifications.telegram import load_telegram_config

        if not load_telegram_config(config).run_summary:
            return None, ledger
    except Exception:
        pass

    from src.developer.activity import DataQuality
    from src.developer.statistics import summarize_data_quality
    from src.reporting.builders import build_weekly_brief

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    pending = events_for_brief(ledger, kind="weekly", window=timedelta(days=7), end=end)
    repository_count = len(monitored_repository_names(config))

    data_quality: dict[str, Any] | None = None
    if state is not None:
        quality_entries: list[DataQuality] = []
        for collector_name, log_entry in (state.update_log or {}).items():
            status = log_entry.get("status", "unknown")
            quality_entries.append(
                DataQuality(
                    collector=collector_name,
                    is_complete=status == "success",
                    observed_at=str(log_entry.get("observed_at") or ""),
                    error=log_entry.get("error") if status == "failure" else None,
                    item_count=int(log_entry.get("item_count") or 0),
                )
            )
        data_quality = summarize_data_quality(quality_entries)

    ledger, show_quality, pending, quality_id = _gate_data_quality(
        ledger, data_quality, pending, "weekly", end
    )
    if not pending and not show_quality and developer_events <= 0:
        return None, ledger

    headline = _context_headline(pending, ledger, "weekly", end)
    built = build_weekly_brief(
        pending,
        coverage_start=start,
        coverage_end=end,
        repository_count=repository_count,
        developer_events=developer_events,
        data_quality=data_quality if show_quality else None,
        headline=headline,
        extra_sections=_brief_extra_sections(pending, ledger, end),
    )
    if built is None:
        return None, ledger
    title, blocks = built
    delivery = _safe_notify(
        job,
        lambda: notify_report(title, blocks, topic=TOPIC_RUN_SUMMARY, config=config),
    )
    if delivery is not None and (delivery.ok or delivery.skip_reason in ("nothing to send", "nothing to report")):
        briefed = [event.identity for event in pending if event.identity]
        if show_quality and quality_id:
            briefed.append(quality_id)
        ledger = mark_briefed(ledger, briefed, "weekly")
    return delivery, ledger


# ── Jobs ─────────────────────────────────────────────────────────


def job_daily(**kwargs: Any) -> JobResult:
    """Repository collection, release/CI detection, and a daily alert digest.

    Collects repos, issues, pulls, releases, and workflow runs for every
    configured repository, then runs Phase 4 monitors over the resulting events.
    Also extracts developer activity (when username is known) and may send a
    daily brief on the run_summary topic.
    """
    failure, config, state, snapshot, events = _collect_and_persist(
        JOB_DAILY, ("repos", "issues", "pulls", "releases", "workflows")
    )
    if failure is not None:
        return failure

    results, summary = _evaluate_and_monitor(state, config, events)
    report_events = _report_events_from_results(results)

    username = authenticated_username(state)
    activity_events: list[ReportEvent] = []
    if username:
        records, _quality = extract_activity(state, events, username)
        for record in records:
            event = report_event_from_activity(record)
            if event is not None:
                activity_events.append(event)

    ledger = load_ledger(state)
    ledger = merge_events(ledger, report_events + activity_events)

    delivery, ledger = _deliver_monitoring_alerts(
        JOB_DAILY, state, config, results, report_events, ledger
    )
    developer_delivery, ledger = _deliver_developer_events(
        JOB_DAILY, config, activity_events, ledger
    )
    brief_delivery, ledger = _maybe_send_daily_brief(JOB_DAILY, config, state, ledger)
    _persist_ledger(state, ledger)

    data: dict[str, Any] = {
        "state": state_summary(state),
        "monitors": summary,
        **_notification_data(delivery),
    }
    if developer_delivery is not None:
        data["developer_notification"] = developer_delivery.to_dict()
    if brief_delivery is not None:
        data["brief_notification"] = brief_delivery.to_dict()
    return JobResult(job=JOB_DAILY, success=True, data=data)


def job_monitoring(**kwargs: Any) -> JobResult:
    """CI failure detection plus security alert checks.

    Intended for a frequent schedule; collects workflow runs and security
    alerts only.
    """
    failure, config, state, snapshot, events = _collect_and_persist(
        JOB_MONITORING, ("workflows", "security")
    )
    if failure is not None:
        return failure

    results, summary = _evaluate_and_monitor(state, config, events)
    report_events = _report_events_from_results(results)

    ledger = load_ledger(state)
    ledger = merge_events(ledger, report_events)

    delivery, ledger = _deliver_monitoring_alerts(
        JOB_MONITORING, state, config, results, report_events, ledger
    )
    brief_delivery, ledger = _maybe_send_daily_brief(JOB_MONITORING, config, state, ledger)
    _persist_ledger(state, ledger)

    data: dict[str, Any] = {
        "state": state_summary(state),
        "monitors": summary,
        **_notification_data(delivery),
    }
    if brief_delivery is not None:
        data["brief_notification"] = brief_delivery.to_dict()
    return JobResult(job=JOB_MONITORING, success=True, data=data)


def job_weekly_report(**kwargs: Any) -> JobResult:
    """Personal activity, statistics, and the weekly developer report.

    Collects the authenticated user plus issues and pulls, converts Phase 3
    events into Phase 6 activity, and delivers a DeveloperReport plus an
    optional weekly brief.
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

    activity_events: list[ReportEvent] = []
    for record in records:
        event = report_event_from_activity(record)
        if event is not None:
            activity_events.append(event)

    ledger = load_ledger(state)
    ledger = merge_events(ledger, activity_events)
    brief_delivery, ledger = _maybe_send_weekly_brief(
        JOB_WEEKLY_REPORT,
        config,
        ledger,
        developer_events=len(records),
        state=state,
    )
    _persist_ledger(state, ledger)

    data: dict[str, Any] = {
        "username": username,
        "records": len(records),
        "report": report.to_dict(),
        **_notification_data(delivery),
    }
    if brief_delivery is not None:
        data["brief_notification"] = brief_delivery.to_dict()
    return JobResult(job=JOB_WEEKLY_REPORT, success=True, data=data)


def _oss_identity(opportunity: Any) -> str:
    """Stable cross-run OSS identity, matching Phase 5 deduplication."""
    repo, number = opportunity.identity()
    return f"{repo}#{number}"


def _oss_record(opportunity: Any) -> dict[str, Any]:
    """Change-detection baseline for a delivered opportunity."""
    updated_at = getattr(opportunity, "updated_at", None)
    return {
        "notified": True,
        "updated_at": updated_at.isoformat() if updated_at else "",
        "comments": int(getattr(opportunity, "comments", 0) or 0),
        "state": str(getattr(opportunity, "state", "") or ""),
        "repo_stars": int(getattr(opportunity, "repo_stars", 0) or 0),
    }


def _oss_known_records(state: CurrentState) -> dict[str, dict]:
    """Identity → baseline record for opportunities seen in a previous run."""
    raw = state.resources.get("oss_opportunities", {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _oss_changed(record: dict, opportunity: Any) -> bool:
    """True when the stored baseline differs from the current opportunity.

    Legacy records without a baseline never count as changed, so the
    upgrade itself re-announces nothing.
    """
    if "comments" not in record:
        return False
    updated_at = getattr(opportunity, "updated_at", None)
    current_updated = updated_at.isoformat() if updated_at else ""
    return (
        str(record.get("updated_at") or "") != current_updated
        or int(record.get("comments") or 0) != int(getattr(opportunity, "comments", 0) or 0)
        or str(record.get("state") or "") != str(getattr(opportunity, "state", "") or "")
        or int(record.get("repo_stars") or 0) != int(getattr(opportunity, "repo_stars", 0) or 0)
    )


def _state_with_oss_records(state: CurrentState, records: dict[str, dict]) -> CurrentState:
    """Return state with ``oss_opportunities`` baseline records.

    Preserves every other resource type so a concurrent daily snapshot is not
    discarded (same partial-update discipline as Phase 3).
    """
    resources = dict(state.resources)
    resources["oss_opportunities"] = {
        identity: dict(record) for identity, record in sorted(records.items())
    }
    return CurrentState(
        resources=resources,
        etags=state.etags,
        update_log=state.update_log,
        last_updated=state.last_updated,
        schema_version=state.schema_version,
    )


def _state_with_notified_security(state: CurrentState, identities: set[str]) -> CurrentState:
    """Return state with ``security_notified`` updated.

    Preserves every other resource type so a concurrent snapshot is not
    discarded (same partial-update discipline as Phase 3 / OSS dedup).
    """
    resources = dict(state.resources)
    resources[SECURITY_NOTIFIED_KEY] = {
        identity: {"notified": True} for identity in sorted(identities)
    }
    return CurrentState(
        resources=resources,
        etags=state.etags,
        update_log=state.update_log,
        last_updated=state.last_updated,
        schema_version=state.schema_version,
    )


def _security_radar_config(config: Any) -> RadarConfig:
    """Radar/monitor configuration from the runtime config mapping."""
    monitoring = config.get("monitoring") if hasattr(config, "get") else None
    if isinstance(monitoring, dict):
        monitors = monitoring.get("monitors") or {}
        section = monitors.get("security") or {}
        if isinstance(section, dict):
            return RadarConfig.from_dict(section)
    return RadarConfig()


def job_oss_hunt(**kwargs: Any) -> JobResult:
    """OSS opportunity discovery, scoring, and delivery.

    Delegates to Phase 5's hunter, then delivers only opportunities that have
    not been successfully notified before. Cross-run dedup state lives under
    the ``oss_opportunities`` resource key in Phase 3 state (at-least-once
    delivery: identities are marked only when Telegram reports success).
    Run stats ride on the opportunities message; no separate summary is sent.
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

    records = _oss_known_records(previous_state)
    new_opportunities: list[Any] = []
    changed_opportunities: list[Any] = []
    for opportunity in hunter_result.opportunities:
        identity = _oss_identity(opportunity)
        record = records.get(identity)
        if record is None or not record.get("notified"):
            new_opportunities.append(opportunity)
        elif "comments" not in record:
            # Legacy record without a baseline: adopt the current values
            # silently so future changes become detectable (no upgrade
            # re-announce noise).
            records[identity] = _oss_record(opportunity)
        elif _oss_changed(record, opportunity):
            changed_opportunities.append(opportunity)

    stats = OssRunSummary(
        queries_run=hunter_result.queries_run,
        total_issues_found=hunter_result.total_issues_found,
        opportunities=len(hunter_result.opportunities),
        duplicates=hunter_result.total_duplicates,
    )

    # Exactly one batch delivery per run, even when the list is empty (the
    # formatter emits nothing and the notifier reports "nothing to send").
    delivery = _safe_notify(
        JOB_OSS_HUNT,
        lambda: notify_oss_opportunities(
            new_opportunities,
            config=config,
            limit=hunter_settings.max_results,
            stats=stats,
        ),
    )
    if delivery is not None and delivery.ok:
        for opportunity in new_opportunities:
            records[_oss_identity(opportunity)] = _oss_record(opportunity)
    elif delivery is None:
        logger.warning("%s: opportunity delivery raised; not marking identities", JOB_OSS_HUNT)
    elif not delivery.ok:
        logger.warning(
            "%s: opportunity delivery failed; not marking identities (%s)",
            JOB_OSS_HUNT,
            delivery.failed_chats,
        )

    # Previously seen opportunities that changed: one radar report under a
    # distinct title, leaving the pinned opportunities contract untouched.
    if changed_opportunities:
        from src.reporting.narratives import oss_update_blocks

        blocks = oss_update_blocks(changed_opportunities)
        if blocks:
            radar_delivery = _safe_notify(
                JOB_OSS_HUNT,
                lambda: notify_report(
                    "🧭 GH-OPS · OSS RADAR",
                    blocks,
                    topic=TOPIC_OSS_OPPORTUNITIES,
                    config=config,
                ),
            )
            if radar_delivery is not None and radar_delivery.ok:
                for opportunity in changed_opportunities:
                    records[_oss_identity(opportunity)] = _oss_record(opportunity)
            elif radar_delivery is None:
                logger.warning(
                    "%s: radar delivery raised; not marking changed opportunities",
                    JOB_OSS_HUNT,
                )
            elif not radar_delivery.ok:
                logger.warning(
                    "%s: radar delivery failed; not marking changed opportunities (%s)",
                    JOB_OSS_HUNT,
                    radar_delivery.failed_chats,
                )

    try:
        persist_state(_state_with_oss_records(previous_state, records))
    except Exception as exc:
        return _fatal(JOB_OSS_HUNT, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc)

    return JobResult(
        job=JOB_OSS_HUNT,
        success=True,
        data={
            "opportunities": len(hunter_result.opportunities),
            "new_opportunities": len(new_opportunities),
            "changed_opportunities": len(changed_opportunities),
            "queries_run": hunter_result.queries_run,
            "queries_failed": hunter_result.queries_failed,
            **_notification_data(delivery),
        },
    )


def job_security(**kwargs: Any) -> JobResult:
    """Dependabot and code-scanning intelligence and alerts.

    Collects security alerts into Phase 3 state, analyzes them with the
    Security Intelligence radar, delivers only actionable findings that have
    not been successfully notified before (at-least-once under the
    ``security_notified`` resource key). Recovered alerts clear their
    notification mark so a future re-open can alert again. No completion
    summary is sent.
    """
    failure, config, state, _snapshot, events = _collect_and_persist(
        JOB_SECURITY, ("security",)
    )
    if failure is not None:
        return failure

    radar = _security_radar_config(config)
    report = analyze_security(state, radar)

    notified = set(state.resources.get(SECURITY_NOTIFIED_KEY, {}) or {})

    # Recoveries: removed / closed alerts clear security_notified.
    # Only when the security collector succeeded (no false recovery on failure).
    security_status = (state.update_log or {}).get("security", {}).get("status")
    recovery_events: list[ReportEvent] = []
    if security_status == "success":
        try:
            event_results = evaluate_events(state, monitor_config(config), list(events))
        except Exception as exc:
            logger.warning("%s: evaluate_events failed (%s)", JOB_SECURITY, type(exc).__name__)
            event_results = []
        for result in event_results:
            if result.monitor != "security":
                continue
            event_type = str((result.metadata or {}).get("event_type") or "")
            if event_type in ("alert_removed",) or (
                event_type == "alert_changed"
                and result.status.value == "changed"
                and any(
                    getattr(c, "field", "") == "state"
                    and getattr(c, "after", "") != "open"
                    for c in result.changes
                )
            ):
                identity = result.resource
                if identity in notified:
                    notified.discard(identity)
                converted = report_event_from_monitor(result)
                if converted is not None:
                    recovery_events.append(converted)

    new_actionable = [
        finding for finding in report.actionable if finding.identity not in notified
    ]

    # Exactly one batch delivery per run, even when the list is empty (the
    # formatter emits nothing and the notifier reports "nothing to send").
    delivery = _safe_notify(
        JOB_SECURITY,
        lambda: notify_security_alerts(new_actionable, config=config),
    )
    if delivery is not None and delivery.ok:
        notified.update(finding.identity for finding in new_actionable)
    elif delivery is None:
        logger.warning("%s: security alert delivery raised; not marking identities", JOB_SECURITY)
    elif not delivery.ok:
        logger.warning(
            "%s: security alert delivery failed; not marking identities (%s)",
            JOB_SECURITY,
            delivery.failed_chats,
        )

    # Resolve recovery report events on the security topic.
    recovery_delivery = None
    if recovery_events:
        from src.reporting.builders import build_report as _build_report

        built = _build_report(recovery_events, report_name="SECURITY")
        if built is not None:
            title, blocks = built
            recovery_delivery = _safe_notify(
                JOB_SECURITY,
                lambda: notify_report(title, blocks, topic=TOPIC_SECURITY, config=config),
            )

    try:
        persist_state(_state_with_notified_security(state, notified))
    except Exception as exc:
        return _fatal(JOB_SECURITY, ErrorCode.STATE_SAVE_FAILED, "Could not persist state", exc)

    data: dict[str, Any] = {
        "state": state_summary(state),
        "alerts": report.total,
        "open_alerts": report.open_count,
        "actionable": report.actionable_count,
        "new_actionable": len(new_actionable),
        "by_severity": dict(report.by_severity),
        "report": report.to_dict(),
        **_notification_data(delivery),
    }
    if recovery_delivery is not None:
        data["recovery_notification"] = recovery_delivery.to_dict()
    if recovery_events:
        data["recoveries"] = len(recovery_events)
    return JobResult(job=JOB_SECURITY, success=True, data=data)


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
            "data_quality": _system_health(state),
        },
    )


def _system_health(state: CurrentState) -> dict[str, Any]:
    """Collector health summary for job self-monitoring.

    Failed collectors surface as incomplete; success-only health is compact
    so a clean status run does not dump empty failure lists.
    """
    from src.developer.activity import DataQuality
    from src.developer.statistics import summarize_data_quality

    entries: list[DataQuality] = []
    for name, entry in (state.update_log or {}).items():
        status = entry.get("status", "unknown")
        entries.append(
            DataQuality(
                collector=name,
                is_complete=status == "success",
                observed_at=str(entry.get("observed_at") or ""),
                error=entry.get("error") if status == "failure" else None,
                item_count=int(entry.get("item_count") or 0),
            )
        )
    summary = summarize_data_quality(entries)
    failed = [d for d in summary.get("details", []) if not d.get("is_complete")]
    return {
        "collectors": summary.get("collectors", 0),
        "complete": summary.get("complete", 0),
        "incomplete": summary.get("incomplete", 0),
        "completeness_pct": summary.get("completeness_pct", 100.0),
        "failed": [{"collector": d.get("collector"), "error": d.get("error")} for d in failed],
    }


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
