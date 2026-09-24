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

    def to_dict(self) -> dict[str, Any]:
        return {
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
        )
