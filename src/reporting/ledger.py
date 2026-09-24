"""Cross-run report event ledger stored under Phase 3 state resources."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from src.core.models import CurrentState
from src.reporting.model import SEVERITY_RANK, ReportEvent, Severity
from src.utils.time import parse_event_timestamp

#: State resource key for the reporting ledger.
REPORTING_EVENTS_KEY = "reporting_events"

#: How long delivered/briefed events are retained.
DEFAULT_MAX_AGE_DAYS = 30

# ── Event lifecycle (NEW → NOTIFIED → OPEN → RESOLVED → RETIRED) ──
# new:       first observed this run; not yet delivered
# notified:  delivered to Telegram at least once
# open:      re-observed while still active after a prior notify (suppressed)
# resolved:  a resolution has been delivered for this condition
# retired:   aged out by prune_ledger (entry removed; status for audit trails)

STATUS_NEW = "new"
STATUS_NOTIFIED = "notified"
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_RETIRED = "retired"

#: Statuses that mean "condition still active and already announced".
_ACTIVE_STATUSES = frozenset({STATUS_NEW, STATUS_NOTIFIED, STATUS_OPEN})


def load_ledger(state: CurrentState) -> dict[str, dict]:
    """Load ledger entries from state (identity → serialized event + flags)."""
    raw = state.resources.get(REPORTING_EVENTS_KEY, {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def condition_key(identity: str) -> str:
    """Condition identity without the trailing event-type segment.

    ``ci:owner/repo/run/1:failure`` and ``ci:owner/repo/run/1:recovery``
    share a condition key so a resolution can close the open condition.
    """
    head, sep, tail = str(identity or "").rpartition(":")
    if sep and tail and "/" not in tail and " " not in tail:
        return head
    return str(identity or "")


def event_should_notify(ledger: dict[str, dict], event: ReportEvent) -> bool:
    """Whether ``event`` should be delivered now given ledger state.

    Suppresses re-notification of persistent conditions already announced
    (NEW/NOTIFIED/OPEN) unless severity escalated or this is a resolution.
    """
    from src.reporting.aggregate import event_key

    key = event_key(event)
    if not key:
        return True
    entry = ledger.get(key)
    if entry is None:
        return True
    if not entry.get("delivered"):
        return True

    status = str(entry.get("status") or STATUS_NOTIFIED)
    if status in (STATUS_RESOLVED, STATUS_RETIRED):
        return False

    if event.severity is Severity.RESOLVED:
        return status != STATUS_RESOLVED

    # Already announced this identity: only re-notify on escalation.
    # Compare against the severity at announce time — merge may have already
    # refreshed the entry severity to the escalated value.
    raw_announced = str(entry.get("announced_severity") or entry.get("severity") or "")
    try:
        previous = Severity(raw_announced) if raw_announced else event.severity
    except ValueError:
        previous = event.severity
    return SEVERITY_RANK[event.severity] < SEVERITY_RANK.get(previous, len(SEVERITY_RANK))


def filter_notifiable(
    ledger: dict[str, dict],
    events: Iterable[ReportEvent],
) -> list[ReportEvent]:
    """Filter events down to those that should be delivered now."""
    return [event for event in events if event_should_notify(ledger, event)]


def merge_events(
    ledger: dict[str, dict],
    events: Iterable[ReportEvent],
) -> dict[str, dict]:
    """Merge new events into the ledger, preserving prior flags/timestamps.

    Lifecycle: first sighting → ``new``; re-sighting after delivery → ``open``
    (condition still active, already notified); resolution severity → tracked
    as its own entry until delivered then ``resolved``.
    """
    from src.reporting.aggregate import event_key

    merged = {k: dict(v) for k, v in ledger.items()}
    for event in events:
        key = event_key(event)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            entry = event.to_dict()
            entry["delivered"] = False
            entry["briefed_daily"] = False
            entry["briefed_weekly"] = False
            entry["status"] = STATUS_NEW
            entry["condition"] = condition_key(key)
            merged[key] = entry
        else:
            # Keep original timestamp, delivery flags, and announce metadata.
            flags = {
                "delivered": bool(existing.get("delivered")),
                "briefed_daily": bool(existing.get("briefed_daily")),
                "briefed_weekly": bool(existing.get("briefed_weekly")),
            }
            entry = event.to_dict()
            entry["timestamp"] = str(existing.get("timestamp") or entry.get("timestamp") or "")
            entry.update(flags)
            if existing.get("announced_severity"):
                entry["announced_severity"] = str(existing["announced_severity"])
            prev_status = str(existing.get("status") or STATUS_NEW)
            if event.severity is Severity.RESOLVED:
                # A delivered resolution stays resolved; an undelivered one
                # starts as new so mark_delivered can promote it to resolved.
                entry["status"] = STATUS_RESOLVED if flags["delivered"] else STATUS_NEW
            elif prev_status in (STATUS_RESOLVED, STATUS_RETIRED):
                # Condition reopened (e.g. alert came back after resolution).
                entry["status"] = STATUS_OPEN if flags["delivered"] else STATUS_NEW
            elif flags["delivered"]:
                # Persistent condition re-observed after notify → open.
                entry["status"] = STATUS_OPEN
            else:
                entry["status"] = prev_status or STATUS_NEW
            entry["condition"] = str(
                existing.get("condition") or condition_key(key)
            )
            merged[key] = entry
    return merged


def mark_delivered(ledger: dict[str, dict], keys: Sequence[str]) -> dict[str, dict]:
    """Mark the given ledger keys as delivered and advance lifecycle status."""
    updated = {k: dict(v) for k, v in ledger.items()}
    for key in keys:
        if key not in updated:
            continue
        entry = updated[key]
        entry["delivered"] = True
        severity = str(entry.get("severity") or "")
        if severity:
            entry["announced_severity"] = severity
        if severity == Severity.RESOLVED.value:
            entry["status"] = STATUS_RESOLVED
        elif str(entry.get("status") or "") in ("", STATUS_NEW, STATUS_OPEN):
            entry["status"] = STATUS_NOTIFIED
    return updated


def resolve_conditions(
    ledger: dict[str, dict],
    events: Sequence[ReportEvent],
) -> dict[str, dict]:
    """Close open/notified siblings of delivered resolution events."""
    if not events:
        return ledger
    resolved_keys = {
        condition_key(event.identity or event_key_from(event))
        for event in events
        if event.severity is Severity.RESOLVED
    }
    if not resolved_keys:
        return ledger
    updated = {k: dict(v) for k, v in ledger.items()}
    for key, entry in updated.items():
        entry_condition = str(entry.get("condition") or condition_key(key))
        if (
            entry_condition in resolved_keys
            and str(entry.get("status") or "") in _ACTIVE_STATUSES
            and entry.get("severity") != Severity.RESOLVED.value
        ):
            entry["status"] = STATUS_RESOLVED
    return updated


def event_key_from(event: ReportEvent) -> str:
    from src.reporting.aggregate import event_key

    return event_key(event)


def mark_briefed(
    ledger: dict[str, dict],
    keys: Sequence[str],
    kind: str,
) -> dict[str, dict]:
    """Mark keys as included in a daily or weekly brief."""
    field = f"briefed_{kind}"
    updated = {k: dict(v) for k, v in ledger.items()}
    for key in keys:
        if key in updated:
            updated[key][field] = True
    return updated


def _entry_event(entry: dict) -> ReportEvent | None:
    try:
        return ReportEvent.from_dict(entry)
    except (TypeError, ValueError, KeyError):
        return None


def undelivered_events(ledger: dict[str, dict]) -> list[ReportEvent]:
    """Ledger events not yet successfully delivered, in deterministic order."""
    events: list[ReportEvent] = []
    for key in sorted(ledger):
        entry = ledger[key]
        if entry.get("delivered"):
            continue
        event = _entry_event(entry)
        if event is not None:
            events.append(event)
    return events


def events_in_window(
    ledger: dict[str, dict],
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ReportEvent]:
    """Events whose timestamp falls within [start, end] (inclusive)."""
    end = end or datetime.now(timezone.utc)
    events: list[ReportEvent] = []
    for key in sorted(ledger):
        event = _entry_event(ledger[key])
        if event is None:
            continue
        ts = parse_event_timestamp(event.timestamp)
        if ts is None:
            continue
        if start is not None and ts < start:
            continue
        if ts > end:
            continue
        events.append(event)
    return events


def events_for_brief(
    ledger: dict[str, dict],
    *,
    kind: str,
    window: timedelta,
    end: datetime | None = None,
) -> list[ReportEvent]:
    """Events for a brief window that have not yet been briefed for ``kind``."""
    end = end or datetime.now(timezone.utc)
    start = end - window
    field = f"briefed_{kind}"
    events: list[ReportEvent] = []
    for key in sorted(ledger):
        entry = ledger[key]
        if entry.get(field):
            continue
        event = _entry_event(entry)
        if event is None:
            continue
        ts = parse_event_timestamp(event.timestamp)
        if ts is None:
            continue
        if ts < start or ts > end:
            continue
        events.append(event)
    return events


def prune_ledger(
    ledger: dict[str, dict],
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Drop entries older than ``max_age_days`` (lifecycle: retired)."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    kept: dict[str, dict] = {}
    for key, entry in ledger.items():
        ts = parse_event_timestamp(entry.get("timestamp") or "")
        if ts is None or ts >= cutoff:
            kept[key] = dict(entry)
    return kept


def state_with_ledger(state: CurrentState, ledger: dict[str, dict]) -> CurrentState:
    """Return state with the reporting ledger resource replaced."""
    resources = dict(state.resources)
    resources[REPORTING_EVENTS_KEY] = {k: dict(v) for k, v in ledger.items()}
    return CurrentState(
        resources=resources,
        etags=dict(state.etags),
        update_log=dict(state.update_log),
        last_updated=state.last_updated,
        schema_version=state.schema_version,
    )
