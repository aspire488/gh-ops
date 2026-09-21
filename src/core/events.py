"""Event detection for gh-ops.

Phase 1 (preserved):
    Compares current state against previous state to find:
    - New items (added since last run)
    - Removed items (deleted or no longer matching)
    - Changed items (modified since last run)

Phase 3 additions:
    - Deterministic diff engine (same input → same output)
    - Field-level change tracking (what specifically changed)
    - NEW / CHANGED / REMOVED / UNCHANGED semantics
    - Stable resource identity and ordering
    - No secrets, no volatile fields, no random IDs, no network access
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.models import (
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.utils.logging import get_logger

logger = get_logger("core.events")


# ── Phase 1 Event (preserved for backward compatibility) ─────────


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

    # New items — deterministic order (sorted)
    for key in sorted(curr_keys - prev_keys):
        events.append(Event(
            type="new",
            source=source,
            key=key,
            current=curr_items[key],
        ))

    # Removed items — deterministic order (sorted)
    for key in sorted(prev_keys - curr_keys):
        events.append(Event(
            type="removed",
            source=source,
            key=key,
            previous=prev_items[key],
        ))

    # Changed items — deterministic order (sorted)
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


# ── Phase 3: Deterministic diff engine ───────────────────────────


# Fields to ignore during comparison (volatile, not meaningful for change detection)
# Uses a frozenset of exact names plus a suffix check for *_url fields
_IGNORED_FIELDS_EXACT: frozenset[str] = frozenset({
    "updated_at",       # Changes on every API response
    "pushed_at",        # Changes on every push
    "last_modified",    # Header-derived, not resource content
    "etag",             # Meta-field, not resource content
    "node_id",          # Opaque, not meaningful for diff
    "url",              # API endpoint, not content
})


def _is_ignored_field(field: str) -> bool:
    """Check if a field should be ignored during comparison."""
    if field in _IGNORED_FIELDS_EXACT:
        return True
    # Ignore any *_url field (GitHub API template URLs)
    if field.endswith("_url"):
        return True
    return False


def _normalize_value(value: Any) -> Any:
    """Normalize a value for deterministic comparison.

    Ensures:
    - Lists are sorted by string representation (lexicographic, for determinism)
    - Tuples become lists
    - None is preserved
    - Nested dicts are recursively normalized

    Note: List sorting uses str() for determinism, not numeric order.
    This ensures [10, 2, 3] always sorts to [10, 2, 3] regardless of types.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        normalized = [_normalize_value(v) for v in value]
        try:
            return sorted(normalized, key=lambda x: str(x))
        except TypeError:
            return normalized
    if isinstance(value, dict):
        return {
            k: _normalize_value(v)
            for k, v in sorted(value.items())
        }
    return value


def _compute_field_changes(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[FieldChange, ...]:
    """Compute field-level changes between two resource states.

    Only includes meaningful fields (excludes ignored/volatile fields).
    Changes are sorted by field name for deterministic ordering.

    Args:
        previous: Previous resource state.
        current: Current resource state.

    Returns:
        Tuple of FieldChange, sorted by field name.
    """
    changes: list[FieldChange] = []

    all_keys = set(previous.keys()) | set(current.keys())

    for key in sorted(all_keys):
        if _is_ignored_field(key):
            continue

        prev_val = previous.get(key)
        curr_val = current.get(key)

        if prev_val != curr_val:
            changes.append(FieldChange(
                field=key,
                before=_normalize_value(prev_val),
                after=_normalize_value(curr_val),
            ))

    return tuple(changes)


def diff_resources(
    resource_type: str,
    previous_items: dict[str, dict[str, Any]],
    current_items: dict[str, dict[str, Any]],
    include_unchanged: bool = False,
) -> tuple[ResourceEvent, ...]:
    """Deterministic diff engine for resource comparison.

    Produces NEW / CHANGED / REMOVED / UNCHANGED events with
    field-level change tracking for CHANGED resources.

    Determinism guarantees:
    - Same input always produces same output
    - Events sorted by resource_id (stable identity)
    - Field changes sorted by field name
    - No timestamps, no random IDs, no network access

    Args:
        resource_type: Collector name (e.g. "repos", "issues").
        previous_items: Previous state items {key: {...}}.
        current_items: Current state items {key: {...}}.
        include_unchanged: If True, include UNCHANGED events.

    Returns:
        Tuple of ResourceEvent, sorted by resource_id.
    """
    events_by_id: dict[str, ResourceEvent] = {}

    prev_keys = set(previous_items.keys())
    curr_keys = set(current_items.keys())

    # REMOVED: items in previous but not in current
    for key in sorted(prev_keys - curr_keys):
        events_by_id[key] = ResourceEvent(
            event_type=EventType.REMOVED,
            resource_type=resource_type,
            resource_id=key,
            previous=previous_items[key],
        )

    # CHANGED / UNCHANGED: items in both
    # Compare using normalized values that ignore volatile fields
    for key in sorted(prev_keys & curr_keys):
        prev_filtered = {k: v for k, v in previous_items[key].items() if not _is_ignored_field(k)}
        curr_filtered = {k: v for k, v in current_items[key].items() if not _is_ignored_field(k)}
        if prev_filtered != curr_filtered:
            changes = _compute_field_changes(
                previous_items[key],
                current_items[key],
            )
            events_by_id[key] = ResourceEvent(
                event_type=EventType.CHANGED,
                resource_type=resource_type,
                resource_id=key,
                previous=previous_items[key],
                current=current_items[key],
                changes=changes,
            )
        elif include_unchanged:
            events_by_id[key] = ResourceEvent(
                event_type=EventType.UNCHANGED,
                resource_type=resource_type,
                resource_id=key,
                current=current_items[key],
            )

    # NEW: items in current but not in previous
    for key in sorted(curr_keys - prev_keys):
        events_by_id[key] = ResourceEvent(
            event_type=EventType.NEW,
            resource_type=resource_type,
            resource_id=key,
            current=current_items[key],
        )

    # Sort by resource_id for deterministic output
    return tuple(events_by_id[k] for k in sorted(events_by_id.keys()))


def events_by_resource_type(
    events: tuple[ResourceEvent, ...],
    resource_type: str,
) -> tuple[ResourceEvent, ...]:
    """Filter ResourceEvents by resource type.

    Args:
        events: Tuple of ResourceEvents.
        resource_type: Resource type to filter for.

    Returns:
        Filtered tuple.
    """
    return tuple(e for e in events if e.resource_type == resource_type)


def events_by_event_type(
    events: tuple[ResourceEvent, ...],
    event_type: EventType,
) -> tuple[ResourceEvent, ...]:
    """Filter ResourceEvents by event type.

    Args:
        events: Tuple of ResourceEvents.
        event_type: EventType to filter for.

    Returns:
        Filtered tuple.
    """
    return tuple(e for e in events if e.event_type == event_type)


def summarize_resource_events(
    events: tuple[ResourceEvent, ...],
) -> dict[str, int]:
    """Summarize ResourceEvents by resource_type:event_type.

    Args:
        events: Tuple of ResourceEvents.

    Returns:
        Summary dict with counts.
    """
    summary: dict[str, int] = {}
    for event in events:
        key = f"{event.resource_type}:{event.event_type.value}"
        summary[key] = summary.get(key, 0) + 1
    return summary
