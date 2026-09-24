"""Cross-run report event ledger stored under Phase 3 state resources."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from src.core.models import CurrentState
from src.reporting.model import ReportEvent
from src.utils.time import parse_event_timestamp

#: State resource key for the reporting ledger.
REPORTING_EVENTS_KEY = "reporting_events"

#: How long delivered/briefed events are retained.
DEFAULT_MAX_AGE_DAYS = 30


def load_ledger(state: CurrentState) -> dict[str, dict]:
    """Load ledger entries from state (identity → serialized event + flags)."""
    raw = state.resources.get(REPORTING_EVENTS_KEY, {}) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def merge_events(
    ledger: dict[str, dict],
    events: Iterable[ReportEvent],
) -> dict[str, dict]:
    """Merge new events into the ledger, preserving prior flags/timestamps."""
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
            merged[key] = entry
        else:
            # Keep original timestamp and delivery flags; refresh content.
            flags = {
                "delivered": bool(existing.get("delivered")),
                "briefed_daily": bool(existing.get("briefed_daily")),
                "briefed_weekly": bool(existing.get("briefed_weekly")),
            }
            entry = event.to_dict()
            entry["timestamp"] = str(existing.get("timestamp") or entry.get("timestamp") or "")
            entry.update(flags)
            merged[key] = entry
    return merged


def mark_delivered(ledger: dict[str, dict], keys: Sequence[str]) -> dict[str, dict]:
    """Mark the given ledger keys as delivered."""
    updated = {k: dict(v) for k, v in ledger.items()}
    for key in keys:
        if key in updated:
            updated[key]["delivered"] = True
    return updated


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
    """Drop entries older than ``max_age_days``."""
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
