"""Event detection for gh-ops.

Compares current state against previous state to find:
- New items (added since last run)
- Removed items (deleted or no longer matching)
- Changed items (modified since last run)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.utils.logging import get_logger

logger = get_logger("core.events")


@dataclass(frozen=True)
class Event:
    """A detected event from state comparison.

    Attributes:
        type: Kind of event (new, removed, changed).
        source: Which collector produced this (e.g., "repos", "releases").
        key: The item key (e.g., "microsoft/vscode").
        previous: Previous state of the item (None for new items).
        current: Current state of the item (None for removed items).
    """

    type: str  # "new", "removed", "changed"
    source: str
    key: str
    previous: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


def detect_events(
    source: str,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> list[Event]:
    """Compare previous and current state to detect events.

    Both dicts should have the structure:
        { "items": { "key": { ... }, ... } }

    Args:
        source: Collector name (e.g., "repos", "releases").
        previous: Previous state snapshot.
        current: Current state snapshot.

    Returns:
        List of detected events.
    """
    prev_items = previous.get("items", {})
    curr_items = current.get("items", {})

    events: list[Event] = []

    prev_keys = set(prev_items.keys())
    curr_keys = set(curr_items.keys())

    # New items
    for key in sorted(curr_keys - prev_keys):
        events.append(Event(
            type="new",
            source=source,
            key=key,
            current=curr_items[key],
        ))

    # Removed items
    for key in sorted(prev_keys - curr_keys):
        events.append(Event(
            type="removed",
            source=source,
            key=key,
            previous=prev_items[key],
        ))

    # Changed items
    for key in sorted(prev_keys & curr_keys):
        if prev_items[key] != curr_items[key]:
            events.append(Event(
                type="changed",
                source=source,
                key=key,
                previous=prev_items[key],
                current=curr_items[key],
            ))

    if events:
        logger.info(
            "Detected %d events in %s: %d new, %d removed, %d changed",
            len(events),
            source,
            sum(1 for e in events if e.type == "new"),
            sum(1 for e in events if e.type == "removed"),
            sum(1 for e in events if e.type == "changed"),
        )
    else:
        logger.debug("No events detected in %s", source)

    return events


def events_by_type(events: list[Event], event_type: str) -> list[Event]:
    """Filter events by type.

    Args:
        events: List of events.
        event_type: Type to filter for ("new", "removed", "changed").

    Returns:
        Filtered events.
    """
    return [e for e in events if e.type == event_type]


def events_by_source(events: list[Event], source: str) -> list[Event]:
    """Filter events by source collector.

    Args:
        events: List of events.
        source: Source to filter for.

    Returns:
        Filtered events.
    """
    return [e for e in events if e.source == source]


def summarize_events(events: list[Event]) -> dict[str, int]:
    """Summarize events by type and source.

    Args:
        events: List of events.

    Returns:
        Summary dict with counts.
    """
    summary: dict[str, int] = {}
    for event in events:
        key = f"{event.source}:{event.type}"
        summary[key] = summary.get(key, 0) + 1
    return summary
