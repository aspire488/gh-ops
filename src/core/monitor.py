"""Monitor result model for gh-ops Phase 4.

Defines the normalized output of monitoring evaluation:
- MonitorStatus: OK, CHANGED, ALERT, ERROR, SKIPPED
- MonitorResult: Deterministic, frozen result from a monitor evaluation

Monitors CONSUME Phase 3 state/events. They do NOT:
- Make HTTP requests (except endpoint monitor, which is isolated)
- Access GitHub authentication
- Write to GitHub
- Use Telegram or notification systems
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MonitorStatus(str, Enum):
    """Outcome of a monitor evaluation.

    Distinguishes:
    - "nothing changed" from "monitor failed"
    - "resource changed" from "alert condition met"
    - "monitor skipped" from "monitor error"
    """
    OK = "ok"               # No issues detected
    CHANGED = "changed"     # Resource changed, no alert threshold
    ALERT = "alert"         # Alert condition met (threshold, failure, etc.)
    ERROR = "error"         # Monitor itself failed (not the resource)
    SKIPPED = "skipped"     # Monitor was not run (disabled, missing data)


class MonitorCategory(str, Enum):
    """Monitor type classification."""
    REPOSITORY = "repository"
    CI = "ci"
    RELEASE = "release"
    ENDPOINT = "endpoint"


@dataclass(frozen=True)
class MonitorResult:
    """Deterministic result from a single monitor evaluation.

    Attributes:
        monitor: Monitor name (e.g. "repository", "ci", "release", "endpoint").
        resource: Resource identity (e.g. "microsoft/vscode", "workflow:build").
        status: Monitor outcome.
        summary: Human-readable summary of the result.
        category: Monitor type classification.
        events: Related events that triggered this result (empty tuple if none).
        changes: Field-level changes if status is CHANGED/ALERT.
        metadata: Structured data for downstream consumers.
        evaluated_at: ISO timestamp of when this evaluation was made.
    """
    monitor: str
    resource: str
    status: MonitorStatus
    summary: str
    category: MonitorCategory
    events: tuple[Any, ...] = ()
    changes: tuple[Any, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d: dict[str, Any] = {
            "monitor": self.monitor,
            "resource": self.resource,
            "status": self.status.value,
            "summary": self.summary,
            "category": self.category.value,
            "evaluated_at": self.evaluated_at,
        }
        if self.events:
            d["events"] = [
                e.to_dict() if hasattr(e, "to_dict") else str(e)
                for e in self.events
            ]
        if self.changes:
            d["changes"] = [
                {"field": c.field, "before": c.before, "after": c.after}
                if hasattr(c, "field")
                else str(c)
                for c in self.changes
            ]
        if self.metadata:
            d["metadata"] = self.metadata
        return d


def classify_changes_as_alert(
    changes: tuple[Any, ...],
    alert_fields: frozenset[str],
) -> bool:
    """Determine if field changes constitute an alert.

    A change is an alert if ANY changed field is in the alert_fields set.

    Args:
        changes: Tuple of FieldChange objects.
        alert_fields: Set of field names that trigger alerts.

    Returns:
        True if any change field is in alert_fields.
    """
    return any(c.field in alert_fields for c in changes)
