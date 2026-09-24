"""Deduplication and prioritization for report events."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from src.reporting.model import SEVERITY_RANK, ReportEvent, Severity


def event_key(event: ReportEvent) -> str:
    """Stable identity for a report event."""
    if event.identity:
        return event.identity
    return f"{event.subsystem}:{event.event_type}:{event.repository}:{event.title}"


def dedupe(events: Iterable[ReportEvent]) -> list[ReportEvent]:
    """First occurrence wins; preserves input order of unique keys."""
    seen: set[str] = set()
    unique: list[ReportEvent] = []
    for event in events:
        key = event_key(event)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def prioritize(events: Iterable[ReportEvent]) -> list[ReportEvent]:
    """Sort by severity rank, then timestamp, then identity (deterministic)."""

    def sort_key(event: ReportEvent) -> tuple[int, str, str]:
        return (
            SEVERITY_RANK.get(event.severity, len(SEVERITY_RANK)),
            event.timestamp or "",
            event_key(event),
        )

    return sorted(events, key=sort_key)


def group_by_severity(
    events: Sequence[ReportEvent],
) -> dict[Severity, list[ReportEvent]]:
    """Group already-ordered events by severity, preserving order within each."""
    groups: dict[Severity, list[ReportEvent]] = {severity: [] for severity in SEVERITY_RANK}
    for event in events:
        groups.setdefault(event.severity, []).append(event)
    return {severity: items for severity, items in groups.items() if items}
