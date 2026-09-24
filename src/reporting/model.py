"""Structured report events shared by every gh-ops subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """User-facing report severity (most severe first)."""

    ACTION_REQUIRED = "action_required"
    IMPORTANT = "important"
    INFORMATION = "information"
    RESOLVED = "resolved"


SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.ACTION_REQUIRED,
    Severity.IMPORTANT,
    Severity.INFORMATION,
    Severity.RESOLVED,
)

SEVERITY_RANK: dict[Severity, int] = {
    severity: index for index, severity in enumerate(SEVERITY_ORDER)
}

SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.ACTION_REQUIRED: "🔴",
    Severity.IMPORTANT: "🟠",
    Severity.INFORMATION: "🔵",
    Severity.RESOLVED: "🟢",
}

SEVERITY_LABEL: dict[Severity, str] = {
    Severity.ACTION_REQUIRED: "ACTION REQUIRED",
    Severity.IMPORTANT: "IMPORTANT",
    Severity.INFORMATION: "INFORMATION",
    Severity.RESOLVED: "RESOLVED",
}


class Priority(str, Enum):
    """Action priority (how soon to act). Independent of severity.

    Severity classifies *what kind* of thing this is (action required vs
    informational). Priority classifies *when to look* (P0 now → P3 backlog).
    A resolved item is always low priority even if its severity label is
    RESOLVED; an action-required item defaults to P0 but can be demoted.
    """

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


PRIORITY_ORDER: tuple[Priority, ...] = (
    Priority.P0,
    Priority.P1,
    Priority.P2,
    Priority.P3,
)

PRIORITY_RANK: dict[Priority, int] = {
    priority: index for index, priority in enumerate(PRIORITY_ORDER)
}

PRIORITY_LABEL: dict[Priority, str] = {
    Priority.P0: "P0",
    Priority.P1: "P1",
    Priority.P2: "P2",
    Priority.P3: "P3",
}

#: Default priority when an event does not carry an explicit one.
DEFAULT_PRIORITY_BY_SEVERITY: dict[Severity, Priority] = {
    Severity.ACTION_REQUIRED: Priority.P0,
    Severity.IMPORTANT: Priority.P1,
    Severity.INFORMATION: Priority.P2,
    Severity.RESOLVED: Priority.P3,
}


def default_priority(severity: Severity) -> Priority:
    """Default action priority for a severity (overridable per event)."""
    return DEFAULT_PRIORITY_BY_SEVERITY.get(severity, Priority.P2)


def parse_priority(raw: Any, fallback: Priority | None = None) -> Priority | None:
    """Parse a priority value; return None when absent/unknown."""
    if isinstance(raw, Priority):
        return raw
    if raw is None or raw == "":
        return None
    try:
        return Priority(str(raw).lower())
    except ValueError:
        return fallback


SUBSYSTEM_MONITORING = "monitoring"
SUBSYSTEM_CI = "ci"
SUBSYSTEM_RELEASE = "release"
SUBSYSTEM_REPOSITORY = "repository"
SUBSYSTEM_ENDPOINT = "endpoint"
SUBSYSTEM_SECURITY = "security"
SUBSYSTEM_OSS = "oss"
SUBSYSTEM_DEVELOPER = "developer"
SUBSYSTEM_DAILY = "daily"
SUBSYSTEM_WEEKLY = "weekly"

#: Short display labels used in Telegram event blocks.
SUBSYSTEM_LABEL: dict[str, str] = {
    SUBSYSTEM_MONITORING: "MONITORING",
    SUBSYSTEM_CI: "CI",
    SUBSYSTEM_RELEASE: "RELEASE",
    SUBSYSTEM_REPOSITORY: "REPO",
    SUBSYSTEM_ENDPOINT: "ENDPOINT",
    SUBSYSTEM_SECURITY: "SECURITY",
    SUBSYSTEM_OSS: "OSS",
    SUBSYSTEM_DEVELOPER: "DEV",
    SUBSYSTEM_DAILY: "DAILY",
    SUBSYSTEM_WEEKLY: "WEEKLY",
}


@dataclass(frozen=True)
class ReportEvent:
    """One notifiable fact produced by a domain subsystem."""

    subsystem: str
    event_type: str
    severity: Severity
    title: str
    description: str = ""
    repository: str = ""
    url: str = ""
    identity: str = ""
    timestamp: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Explicit action priority; when None, derived from severity.
    priority: Priority | None = None

    @property
    def effective_priority(self) -> Priority:
        """Resolved priority (explicit value, else severity default)."""
        return self.priority or default_priority(self.severity)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "subsystem": self.subsystem,
            "event_type": self.event_type,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "repository": self.repository,
            "url": self.url,
            "identity": self.identity,
            "timestamp": self.timestamp,
            "source": self.source,
            "metadata": dict(self.metadata),
        }
        if self.priority is not None:
            data["priority"] = self.priority.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportEvent:
        raw_severity = str(data.get("severity") or Severity.INFORMATION.value)
        try:
            severity = Severity(raw_severity)
        except ValueError:
            severity = Severity.INFORMATION
        return cls(
            subsystem=str(data.get("subsystem") or ""),
            event_type=str(data.get("event_type") or ""),
            severity=severity,
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            repository=str(data.get("repository") or ""),
            url=str(data.get("url") or ""),
            identity=str(data.get("identity") or ""),
            timestamp=str(data.get("timestamp") or ""),
            source=str(data.get("source") or ""),
            metadata=dict(data.get("metadata") or {}),
            priority=parse_priority(data.get("priority")),
        )
