"""Repository monitor for gh-ops Phase 4.

Detects meaningful repository-level changes by consuming Phase 3 events.

Signals monitored:
- Repository metadata changes (visibility, default branch, archival)
- Star count significant changes
- Fork count changes
- Open issue count changes
- Archive/disabled state changes

Configuration:
    monitors.repository.enabled: bool
    monitors.repository.check_stars: bool
    monitors.repository.check_forks: bool
    monitors.repository.check_open_issues: bool
    monitors.repository.check_recent_activity: bool
"""
from __future__ import annotations

from typing import Any

from src.core.events import diff_resources
from src.core.models import (
    CurrentState,
    EventType,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.utils.logging import get_logger

logger = get_logger("monitors.repository")

# Fields that constitute an alert when changed
_REPO_ALERT_FIELDS = frozenset({
    "archived",
    "disabled",
    "default_branch",
    "visibility",
})

# Fields that are meaningful but not alert-level
_REPO_CHANGE_FIELDS = frozenset({
    "stargazers_count",
    "forks_count",
    "open_issues_count",
    "description",
    "language",
    "topics",
    "license_key",
})


def evaluate_repository_event(
    event: ResourceEvent,
    config: dict[str, Any],
) -> MonitorResult | None:
    """Evaluate a single repository event.

    Args:
        event: ResourceEvent from the repository collector.
        config: Repository monitor configuration.

    Returns:
        MonitorResult if the event is meaningful, None if irrelevant.
    """
    if event.event_type == EventType.UNCHANGED:
        return None

    # If monitor is disabled, skip all event evaluation
    if not config.get("enabled", True):
        return None

    # NEW repository
    if event.event_type == EventType.NEW:
        return MonitorResult(
            monitor="repository",
            resource=event.resource_id,
            status=MonitorStatus.OK,
            summary=f"New repository tracked: {event.resource_id}",
            category=MonitorCategory.REPOSITORY,
            events=(event,),
            metadata={"action": "tracked"},
        )

    # REMOVED repository
    if event.event_type == EventType.REMOVED:
        return MonitorResult(
            monitor="repository",
            resource=event.resource_id,
            status=MonitorStatus.CHANGED,
            summary=f"Repository removed from tracking: {event.resource_id}",
            category=MonitorCategory.REPOSITORY,
            events=(event,),
            metadata={"action": "removed"},
        )

    # CHANGED repository — check for meaningful changes
    if event.event_type == EventType.CHANGED:
        if not event.changes:
            return None

        # Check for alert-level changes
        alert_changes = tuple(
            c for c in event.changes if c.field in _REPO_ALERT_FIELDS
        )
        if alert_changes:
            fields = ", ".join(c.field for c in alert_changes)
            return MonitorResult(
                monitor="repository",
                resource=event.resource_id,
                status=MonitorStatus.ALERT,
                summary=f"Critical repository change: {fields} changed for {event.resource_id}",
                category=MonitorCategory.REPOSITORY,
                events=(event,),
                changes=alert_changes,
                metadata={"alert_fields": list(alert_changes[0].field for c in alert_changes)},
            )

        # Check for informational changes
        info_changes = tuple(
            c for c in event.changes if c.field in _REPO_CHANGE_FIELDS
        )
        if info_changes:
            fields = ", ".join(c.field for c in info_changes)
            return MonitorResult(
                monitor="repository",
                resource=event.resource_id,
                status=MonitorStatus.CHANGED,
                summary=f"Repository metadata changed: {fields} for {event.resource_id}",
                category=MonitorCategory.REPOSITORY,
                events=(event,),
                changes=info_changes,
                metadata={"changed_fields": [c.field for c in info_changes]},
            )

        # Changes only in ignored/volatile fields — not meaningful
        return None

    return None


def monitor_repositories(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Evaluate repository state and produce monitor results.

    Consumes Phase 3 state and events. Does NOT make HTTP requests.

    Args:
        state: Current state containing repository data.
        config: Monitoring configuration (monitors.repository section).

    Returns:
        List of MonitorResult for all repository changes.
    """
    if not config.get("enabled", True):
        return []

    results: list[MonitorResult] = []

    # Check if repos collector succeeded — BEFORE checking if repos is empty
    repos_log = state.update_log.get("repos", {})
    if repos_log.get("status") == "failure":
        return [MonitorResult(
            monitor="repository",
            resource="*",
            status=MonitorStatus.ERROR,
            summary=f"Repository collection failed: {repos_log.get('error', 'unknown')}",
            category=MonitorCategory.REPOSITORY,
            metadata={"collection_error": True},
        )]

    # Get repository items from state
    repo_items = state.resources.get("repos", {})
    if not repo_items:
        return []

    return results
