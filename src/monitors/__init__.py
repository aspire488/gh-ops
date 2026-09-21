"""Monitor registry and evaluator for gh-ops Phase 4.

Central coordination point for all monitors. Evaluates events
against configured monitors and produces structured results.

Architecture:
- Each monitor function consumes Phase 3 state/events
- Registry dispatches to the appropriate monitor based on event category
- Evaluator runs all monitors and produces a consolidated report
- No HTTP, no auth, no write operations (except endpoint monitor)
"""
from __future__ import annotations

from typing import Any, Callable

from src.core.events import events_by_resource_type, events_by_event_type
from src.core.models import (
    CurrentState,
    EventType,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.monitors.ci import evaluate_workflow_event, monitor_ci
from src.monitors.endpoint import monitor_endpoints
from src.monitors.release import evaluate_release_event, monitor_releases
from src.monitors.repository import evaluate_repository_event, monitor_repositories
from src.utils.logging import get_logger

logger = get_logger("monitors.registry")

# Map event types to their evaluation functions
_EVENT_EVALUATORS: dict[str, Callable[[ResourceEvent, dict[str, Any]], MonitorResult | None]] = {
    "repos": evaluate_repository_event,
    "workflows": evaluate_workflow_event,
    "releases": evaluate_release_event,
}

# Map monitor names to their batch evaluation functions
_MONITOR_FUNCTIONS: dict[str, Callable[[CurrentState, dict[str, Any]], list[MonitorResult]]] = {
    "repository": monitor_repositories,
    "ci": monitor_ci,
    "release": monitor_releases,
    "endpoint": monitor_endpoints,
}


def evaluate_events(
    state: CurrentState,
    config: dict[str, Any],
    events: list[ResourceEvent] | None = None,
) -> list[MonitorResult]:
    """Evaluate events against configured monitors.

    Args:
        state: Current state containing all resource data.
        config: Full monitoring configuration.
        events: Optional list of events to evaluate. If None, derived from state.

    Returns:
        List of MonitorResult from all active monitors.
    """
    if events is None:
        events = _extract_events_from_state(state)

    results: list[MonitorResult] = []
    monitor_config = config.get("monitors", {})

    for event in events:
        # Get the resource_type from the event
        resource_type = event.resource_type
        evaluator = _EVENT_EVALUATORS.get(resource_type)
        if evaluator is None:
            logger.debug("No evaluator for resource_type '%s', skipping event", resource_type)
            continue

        # Get the monitor config for this resource_type
        monitor_name = _collector_to_monitor(resource_type)
        m_config = monitor_config.get(monitor_name, {})

        if not m_config.get("enabled", True):
            continue

        result = evaluator(event, m_config)
        if result is not None:
            results.append(result)

    return results


def run_monitors(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Run all active monitors against current state.

    This is the primary entry point for monitor execution.
    Monitors are run in a deterministic order.

    Args:
        state: Current state containing all resource data.
        config: Full monitoring configuration.

    Returns:
        Consolidated list of MonitorResult from all active monitors.
    """
    monitor_config = config.get("monitors", {})
    all_results: list[MonitorResult] = []

    # Run monitors in deterministic order
    for name in ("repository", "ci", "release", "endpoint"):
        m_config = monitor_config.get(name, {})
        if not m_config.get("enabled", False if name == "endpoint" else True):
            continue

        monitor_fn = _MONITOR_FUNCTIONS.get(name)
        if monitor_fn is None:
            logger.warning("No monitor function for '%s'", name)
            continue

        try:
            results = monitor_fn(state, m_config)
            all_results.extend(results)
        except Exception as e:
            logger.error("Monitor '%s' failed: %s", name, e)
            all_results.append(MonitorResult(
                monitor=name,
                resource="*",
                status=MonitorStatus.ERROR,
                summary=f"Monitor execution failed: {e}",
                category=MonitorCategory(name),
                metadata={"execution_error": str(e)},
            ))

    return all_results


def summarize_results(results: list[MonitorResult]) -> dict[str, Any]:
    """Produce a deterministic summary of monitor results.

    Args:
        results: List of MonitorResult.

    Returns:
        Summary dict with counts and categorized results.
    """
    summary: dict[str, Any] = {
        "total": len(results),
        "by_status": {},
        "by_category": {},
        "alerts": [],
        "errors": [],
    }

    for status in MonitorStatus:
        count = sum(1 for r in results if r.status == status)
        if count > 0:
            summary["by_status"][status.value] = count

    for category in MonitorCategory:
        count = sum(1 for r in results if r.category == category)
        if count > 0:
            summary["by_category"][category.value] = count

    for result in results:
        if result.status == MonitorStatus.ALERT:
            summary["alerts"].append({
                "monitor": result.monitor,
                "resource": result.resource,
                "summary": result.summary,
            })
        elif result.status == MonitorStatus.ERROR:
            summary["errors"].append({
                "monitor": result.monitor,
                "resource": result.resource,
                "summary": result.summary,
            })

    return summary


def _extract_events_from_state(state: CurrentState) -> list[ResourceEvent]:
    """Extract events from the current state's update log.

    This is a fallback when events are not provided directly.

    Args:
        state: Current state.

    Returns:
        List of ResourceEvent extracted from the state.
    """
    events: list[ResourceEvent] = []
    # Events are typically produced by diff_resources() during apply_snapshot.
    # If the state has an events attribute, use it; otherwise return empty.
    if hasattr(state, "events") and state.events:
        events = list(state.events)
    return events


def _collector_to_monitor(collector: str) -> str:
    """Map collector name to monitor name.

    Args:
        collector: Collector name (e.g., "repos", "workflows").

    Returns:
        Monitor name (e.g., "repository", "ci").
    """
    mapping = {
        "repos": "repository",
        "issues": "repository",
        "pulls": "repository",
        "releases": "release",
        "workflows": "ci",
        "security": "repository",
        "user": "repository",
    }
    return mapping.get(collector, collector)
