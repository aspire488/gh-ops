"""Release monitor for gh-ops Phase 4.

Detects release-related events by consuming Phase 3 events.

Signals monitored:
- New release published
- Release state change (draft → published, etc.)
- Prerelease detection
- Release removal

Configuration:
    monitors.release.enabled: bool
    monitors.release.notify_prerelease: bool
    monitors.release.notify_draft: bool
"""
from __future__ import annotations

from typing import Any

from src.core.models import (
    CurrentState,
    EventType,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.utils.logging import get_logger

logger = get_logger("monitors.release")


def evaluate_release_event(
    event: ResourceEvent,
    config: dict[str, Any],
) -> MonitorResult | None:
    """Evaluate a single release event.

    Args:
        event: ResourceEvent from the release collector.
        config: Release monitor configuration.

    Returns:
        MonitorResult if the event is meaningful, None if irrelevant.
    """
    if event.event_type == EventType.UNCHANGED:
        return None

    notify_prerelease = config.get("notify_prerelease", False)
    notify_draft = config.get("notify_draft", False)

    # NEW release
    if event.event_type == EventType.NEW and event.current:
        tag = event.current.get("tag_name", event.resource_id)
        is_prerelease = event.current.get("prerelease", False)
        is_draft = event.current.get("draft", False)
        name = event.current.get("name") or tag

        # Filter based on config
        if is_prerelease and not notify_prerelease:
            return None
        if is_draft and not notify_draft:
            return None

        status = MonitorStatus.OK
        summary = f"New release: {name} ({tag})"

        if is_prerelease:
            summary += " [prerelease]"
        if is_draft:
            summary += " [draft]"
            status = MonitorStatus.CHANGED

        return MonitorResult(
            monitor="release",
            resource=event.resource_id,
            status=status,
            summary=summary,
            category=MonitorCategory.RELEASE,
            events=(event,),
            metadata={
                "tag_name": tag,
                "prerelease": is_prerelease,
                "draft": is_draft,
                "event_type": "new_release",
            },
        )

    # REMOVED release
    if event.event_type == EventType.REMOVED:
        return MonitorResult(
            monitor="release",
            resource=event.resource_id,
            status=MonitorStatus.CHANGED,
            summary=f"Release removed: {event.resource_id}",
            category=MonitorCategory.RELEASE,
            events=(event,),
            metadata={"event_type": "release_removed"},
        )

    # CHANGED release
    if event.event_type == EventType.CHANGED and event.changes:
        # Check for meaningful changes
        meaningful_fields = {"tag_name", "draft", "prerelease", "name"}
        meaningful_changes = tuple(
            c for c in event.changes if c.field in meaningful_fields
        )

        if meaningful_changes:
            fields = ", ".join(c.field for c in meaningful_changes)
            return MonitorResult(
                monitor="release",
                resource=event.resource_id,
                status=MonitorStatus.CHANGED,
                summary=f"Release changed: {fields} for {event.resource_id}",
                category=MonitorCategory.RELEASE,
                events=(event,),
                changes=meaningful_changes,
                metadata={"changed_fields": [c.field for c in meaningful_changes]},
            )

    return None


def monitor_releases(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Evaluate release state and produce monitor results.

    Consumes Phase 3 state and events. Does NOT make HTTP requests.

    Args:
        state: Current state containing release data.
        config: Monitoring configuration (monitors.release section).

    Returns:
        List of MonitorResult for all release changes.
    """
    if not config.get("enabled", True):
        return []

    results: list[MonitorResult] = []

    # Check if releases collector succeeded
    releases_log = state.update_log.get("releases", {})
    if releases_log.get("status") == "failure":
        return [MonitorResult(
            monitor="release",
            resource="*",
            status=MonitorStatus.ERROR,
            summary=f"Release collection failed: {releases_log.get('error', 'unknown')}",
            category=MonitorCategory.RELEASE,
            metadata={"collection_error": True},
        )]

    return results
