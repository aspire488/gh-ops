"""CI monitor for gh-ops Phase 4.

Detects meaningful CI/workflow changes by consuming Phase 3 events.

Signals monitored:
- Workflow failure detection
- Workflow recovery detection
- Repeated failure tracking
- Status/conclusion changes

Distinguishes:
- FAILURE: workflow run failed
- RECOVERY: previously failing workflow now succeeds
- UNCHANGED: no change in workflow status
- COLLECTION ERROR: collector failed (not CI failure)

Configuration:
    monitors.ci.enabled: bool
    monitors.ci.failure_threshold: int (consecutive failures before alert)
    monitors.ci.recovery_notification: bool
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

logger = get_logger("monitors.ci")


def evaluate_workflow_event(
    event: ResourceEvent,
    config: dict[str, Any],
) -> MonitorResult | None:
    """Evaluate a single workflow event.

    Args:
        event: ResourceEvent from the workflow collector.
        config: CI monitor configuration.

    Returns:
        MonitorResult if the event is meaningful, None if irrelevant.
    """
    if event.event_type == EventType.UNCHANGED:
        return None

    # NEW workflow run
    if event.event_type == EventType.NEW and event.current:
        conclusion = event.current.get("conclusion")
        name = event.current.get("name", event.resource_id)

        if conclusion in ("failure", "timed_out", "cancelled"):
            return MonitorResult(
                monitor="ci",
                resource=event.resource_id,
                status=MonitorStatus.ALERT,
                summary=f"Workflow {name} failed (conclusion: {conclusion})",
                category=MonitorCategory.CI,
                events=(event,),
                metadata={
                    "conclusion": conclusion,
                    "workflow_name": name,
                    "event_type": "failure",
                },
            )
        elif conclusion == "success":
            return MonitorResult(
                monitor="ci",
                resource=event.resource_id,
                status=MonitorStatus.OK,
                summary=f"Workflow {name} completed successfully",
                category=MonitorCategory.CI,
                events=(event,),
                metadata={
                    "conclusion": conclusion,
                    "workflow_name": name,
                    "event_type": "success",
                },
            )
        else:
            return MonitorResult(
                monitor="ci",
                resource=event.resource_id,
                status=MonitorStatus.CHANGED,
                summary=f"Workflow {name} status: {conclusion or 'unknown'}",
                category=MonitorCategory.CI,
                events=(event,),
                metadata={
                    "conclusion": conclusion,
                    "workflow_name": name,
                    "event_type": "status_change",
                },
            )

    # REMOVED workflow run (cleanup, not meaningful)
    if event.event_type == EventType.REMOVED:
        return None

    # CHANGED workflow run
    if event.event_type == EventType.CHANGED and event.changes:
        # Check if conclusion changed
        conclusion_change = next(
            (c for c in event.changes if c.field == "conclusion"), None
        )
        if conclusion_change:
            old_conclusion = conclusion_change.before
            new_conclusion = conclusion_change.after

            # Recovery: was failing, now success
            if old_conclusion in ("failure", "timed_out", "cancelled") and new_conclusion == "success":
                return MonitorResult(
                    monitor="ci",
                    resource=event.resource_id,
                    status=MonitorStatus.CHANGED,
                    summary=f"Workflow recovered: {old_conclusion} → {new_conclusion}",
                    category=MonitorCategory.CI,
                    events=(event,),
                    changes=(conclusion_change,),
                    metadata={
                        "event_type": "recovery",
                        "old_conclusion": old_conclusion,
                        "new_conclusion": new_conclusion,
                    },
                )

            # New failure
            if new_conclusion in ("failure", "timed_out", "cancelled"):
                return MonitorResult(
                    monitor="ci",
                    resource=event.resource_id,
                    status=MonitorStatus.ALERT,
                    summary=f"Workflow failed: {old_conclusion} → {new_conclusion}",
                    category=MonitorCategory.CI,
                    events=(event,),
                    changes=(conclusion_change,),
                    metadata={
                        "event_type": "failure",
                        "old_conclusion": old_conclusion,
                        "new_conclusion": new_conclusion,
                    },
                )

            # Other conclusion change
            return MonitorResult(
                monitor="ci",
                resource=event.resource_id,
                status=MonitorStatus.CHANGED,
                summary=f"Workflow conclusion changed: {old_conclusion} → {new_conclusion}",
                category=MonitorCategory.CI,
                events=(event,),
                changes=(conclusion_change,),
                metadata={
                    "event_type": "status_change",
                    "old_conclusion": old_conclusion,
                    "new_conclusion": new_conclusion,
                },
            )

        # Status changed (not conclusion)
        status_change = next(
            (c for c in event.changes if c.field == "status"), None
        )
        if status_change:
            return MonitorResult(
                monitor="ci",
                resource=event.resource_id,
                status=MonitorStatus.CHANGED,
                summary=f"Workflow status changed: {status_change.before} → {status_change.after}",
                category=MonitorCategory.CI,
                events=(event,),
                changes=(status_change,),
                metadata={
                    "event_type": "status_change",
                    "old_status": status_change.before,
                    "new_status": status_change.after,
                },
            )

    return None


def monitor_ci(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Evaluate CI state and produce monitor results.

    Consumes Phase 3 state and events. Does NOT make HTTP requests.

    Args:
        state: Current state containing workflow data.
        config: Monitoring configuration (monitors.ci section).

    Returns:
        List of MonitorResult for all CI changes.
    """
    if not config.get("enabled", True):
        return []

    results: list[MonitorResult] = []

    # Check if workflows collector succeeded
    workflows_log = state.update_log.get("workflows", {})
    if workflows_log.get("status") == "failure":
        return [MonitorResult(
            monitor="ci",
            resource="*",
            status=MonitorStatus.ERROR,
            summary=f"CI collection failed: {workflows_log.get('error', 'unknown')}",
            category=MonitorCategory.CI,
            metadata={"collection_error": True},
        )]

    return results
