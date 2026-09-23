"""Security Intelligence models.

Deterministic, frozen models for ranking Dependabot and code-scanning alerts
already collected into Phase 3 state. No HTTP, no auth, no GitHub writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Severity rank (lower is more severe). Unknown sorts last.
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "unknown": 4,
}

#: Severities treated as actionable by default.
DEFAULT_ALERT_SEVERITIES: frozenset[str] = frozenset({"critical", "high"})


def normalize_severity(raw: Any) -> str:
    """Normalize a severity string to a known rank key."""
    value = str(raw or "unknown").strip().lower()
    return value if value in SEVERITY_RANK else "unknown"


def severity_rank(severity: Any) -> int:
    """Rank for sorting (most severe first)."""
    return SEVERITY_RANK[normalize_severity(severity)]


def parse_identity(identity: str) -> tuple[str, str]:
    """Split a state key into ``(repository, source)``.

    Keys are ``owner/repo/dependabot/{id}`` or ``owner/repo/codescan/{id}``.
    Falls back to the full identity as the repository when the shape is
    unexpected.
    """
    parts = str(identity).split("/")
    if len(parts) >= 4:
        return "/".join(parts[:2]), parts[2]
    return str(identity), "security"


@dataclass(frozen=True)
class SecurityFinding:
    """One open (or resolved) security alert from Phase 3 state."""

    identity: str
    repository: str
    source: str
    package_name: str
    severity: str
    summary: str
    state: str = "open"
    html_url: str | None = None

    @classmethod
    def from_state_item(cls, identity: str, data: dict[str, Any]) -> SecurityFinding:
        """Build a finding from a ``state.resources["security"]`` entry."""
        repository, source = parse_identity(identity)
        raw = data if isinstance(data, dict) else {}
        return cls(
            identity=str(identity),
            repository=repository,
            source=source,
            package_name=str(raw.get("package_name") or ""),
            severity=normalize_severity(raw.get("severity")),
            summary=str(raw.get("summary") or ""),
            state=str(raw.get("state") or "open"),
            html_url=raw.get("html_url") or None,
        )

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "repository": self.repository,
            "source": self.source,
            "package_name": self.package_name,
            "severity": self.severity,
            "summary": self.summary,
            "state": self.state,
            "html_url": self.html_url,
        }


def _sort_key(finding: SecurityFinding) -> tuple[int, str]:
    return (severity_rank(finding.severity), finding.identity)


@dataclass(frozen=True)
class SecurityReport:
    """Ranked snapshot of security findings for one analysis run."""

    findings: tuple[SecurityFinding, ...]
    by_severity: dict[str, int]
    open_count: int
    actionable: tuple[SecurityFinding, ...]
    alert_severities: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.findings)

    @property
    def actionable_count(self) -> int:
        return len(self.actionable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "open": self.open_count,
            "actionable": self.actionable_count,
            "by_severity": dict(self.by_severity),
            "alert_severities": list(self.alert_severities),
            "findings": [f.to_dict() for f in self.findings],
        }
