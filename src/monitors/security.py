"""Security monitor for gh-ops.

Detects meaningful security-alert changes by consuming Phase 3 events.

Signals monitored:
- New open alert at an actionable severity → ALERT
- New open alert at a non-actionable severity → CHANGED
- Alert resolved / removed → CHANGED
- Collection failure → ERROR

Configuration:
    monitors.security.enabled: bool
    monitors.security.alert_severities: list[str]
"""
from __future__ import annotations

from typing import Any

from src.core.models import (
    CurrentState,
    EventType,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.intelligence.security.models import normalize_severity
from src.intelligence.security.radar import DEFAULT_ALERT_SEVERITIES
from src.utils.logging import get_logger

logger = get_logger("monitors.security")


def _actionable_severities(config: dict[str, Any]) -> frozenset[str]:
    raw = config.get("alert_severities")
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(normalize_severity(s) for s in raw)
    return frozenset(DEFAULT_ALERT_SEVERITIES)


def evaluate_security_event(
    event: ResourceEvent,
    config: dict[str, Any],
) -> MonitorResult | None:
    """Evaluate a single security resource event.

    Args:
        event: ResourceEvent from the security collector.
        config: monitors.security configuration.

    Returns:
        MonitorResult if the event is meaningful, None if irrelevant.
    """
    if event.event_type == EventType.UNCHANGED:
        return None

    alert_severities = _actionable_severities(config)
    current = event.current or {}
    severity = normalize_severity(current.get("severity"))
    package = str(current.get("package_name") or event.resource_id)
    summary_text = str(current.get("summary") or "")

    if event.event_type == EventType.NEW and event.current:
        if str(current.get("state", "open")) != "open":
            return None
        detail = f"New {severity} alert: {package}"
        if summary_text:
            detail = f"{detail} — {summary_text}"
        if severity in alert_severities:
            return MonitorResult(
                monitor="security",
                resource=event.resource_id,
                status=MonitorStatus.ALERT,
                summary=detail,
                category=MonitorCategory.SECURITY,
                events=(event,),
                metadata={
                    "event_type": "new_alert",
                    "severity": severity,
                    "package_name": package,
                    "alertable": True,
                },
            )
        return MonitorResult(
            monitor="security",
            resource=event.resource_id,
            status=MonitorStatus.CHANGED,
            summary=detail,
            category=MonitorCategory.SECURITY,
            events=(event,),
            metadata={
                "event_type": "new_alert",
                "severity": severity,
                "package_name": package,
                "alertable": False,
            },
        )

    if event.event_type == EventType.REMOVED:
        return MonitorResult(
            monitor="security",
            resource=event.resource_id,
            status=MonitorStatus.CHANGED,
            summary=f"Security alert removed: {event.resource_id}",
            category=MonitorCategory.SECURITY,
            events=(event,),
            metadata={"event_type": "alert_removed"},
        )

    if event.event_type == EventType.CHANGED and event.changes:
        meaningful = {"state", "severity"}
        changed = tuple(c for c in event.changes if c.field in meaningful)
        if not changed:
            return None
        fields = ", ".join(c.field for c in changed)
        state_after = str(current.get("state") or "")
        is_open_actionable = (
            state_after == "open"
            and severity in alert_severities
            and any(c.field == "state" and c.after == "open" for c in changed)
        )
        status = MonitorStatus.ALERT if is_open_actionable else MonitorStatus.CHANGED
        return MonitorResult(
            monitor="security",
            resource=event.resource_id,
            status=status,
            summary=f"Security alert changed ({fields}): {event.resource_id}",
            category=MonitorCategory.SECURITY,
            events=(event,),
            changes=changed,
            metadata={
                "event_type": "alert_changed",
                "changed_fields": [c.field for c in changed],
                "severity": severity,
            },
        )

    return None


def monitor_security(
    state: CurrentState,
    config: dict[str, Any],
) -> list[MonitorResult]:
    """Evaluate security state and produce monitor results.

    Consumes Phase 3 state. Does NOT make HTTP requests. Event-level
    evaluation is driven by ``evaluate_events`` / the registry; this batch
    entry reports collection failures and surfaces high-water findings when
    no events are available.

    Args:
        state: Current state containing security data.
        config: monitors.security configuration.

    Returns:
        List of MonitorResult.
    """
    if not config.get("enabled", True):
        return []

    security_log = (state.update_log or {}).get("security", {})
    if security_log.get("status") == "failure":
        return [MonitorResult(
            monitor="security",
            resource="*",
            status=MonitorStatus.ERROR,
            summary=f"Security collection failed: {security_log.get('error', 'unknown')}",
            category=MonitorCategory.SECURITY,
            metadata={"collection_error": True},
        )]

    return []
