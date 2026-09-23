"""Security advisory radar over Phase 3 state.

Turns the ``security`` resource key into a ranked, actionable report.
Read-only: no HTTP, no auth, no GitHub writes. Composition lives in
``src.jobs``; this module only interprets state it is given.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.models import CurrentState
from src.intelligence.security.models import (
    DEFAULT_ALERT_SEVERITIES,
    SecurityFinding,
    SecurityReport,
    normalize_severity,
    severity_rank,
)
from src.utils.logging import get_logger

logger = get_logger("intelligence.security.radar")

#: Phase 3 resource key holding collected security alerts.
SECURITY_RESOURCE_KEY = "security"

#: Phase 3 resource key marking identities already delivered (at-least-once).
SECURITY_NOTIFIED_KEY = "security_notified"


@dataclass(frozen=True)
class RadarConfig:
    """Configuration for one radar analysis.

    Attributes:
        enabled: Master switch for the security monitor / radar path.
        alert_severities: Severities treated as actionable (at-least-once
            candidates and monitor ALERT inputs).
        max_findings: Cap on findings included in the report body (0 = all).
    """

    enabled: bool = True
    alert_severities: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_ALERT_SEVERITIES)
    )
    max_findings: int = 50

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RadarConfig:
        """Parse from a ``monitors.security`` config mapping."""
        raw = data or {}
        severities_raw = raw.get("alert_severities")
        if severities_raw is None:
            severities = frozenset(DEFAULT_ALERT_SEVERITIES)
        elif isinstance(severities_raw, (list, tuple, set, frozenset)):
            severities = frozenset(normalize_severity(s) for s in severities_raw)
        else:
            severities = frozenset(DEFAULT_ALERT_SEVERITIES)
        try:
            max_findings = int(raw.get("max_findings", 50))
        except (TypeError, ValueError):
            max_findings = 50
        if max_findings < 0:
            max_findings = 0
        return cls(
            enabled=bool(raw.get("enabled", True)),
            alert_severities=severities,
            max_findings=max_findings,
        )


def extract_findings(state: CurrentState) -> list[SecurityFinding]:
    """Build findings from ``state.resources["security"]`` in stable order."""
    resources = getattr(state, "resources", None) or {}
    raw = resources.get(SECURITY_RESOURCE_KEY, {}) or {}
    if not isinstance(raw, dict):
        logger.warning("security resource key is not a mapping; ignoring")
        return []
    findings = [
        SecurityFinding.from_state_item(str(identity), data if isinstance(data, dict) else {})
        for identity, data in raw.items()
    ]
    findings.sort(key=lambda f: (severity_rank(f.severity), f.identity))
    return findings


def analyze_security(
    state: CurrentState,
    config: dict[str, Any] | RadarConfig | None = None,
) -> SecurityReport:
    """Analyze collected security alerts into a ranked report.

    Args:
        state: Current state containing the ``security`` resource key.
        config: ``monitors.security`` mapping or a ``RadarConfig``.

    Returns:
        A deterministic SecurityReport sorted by severity then identity.
    """
    radar = config if isinstance(config, RadarConfig) else RadarConfig.from_dict(config)
    findings = extract_findings(state)
    if radar.max_findings > 0:
        report_findings = tuple(findings[: radar.max_findings])
    else:
        report_findings = tuple(findings)

    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    open_count = sum(1 for f in findings if f.is_open)
    actionable = tuple(
        f for f in findings if f.is_open and f.severity in radar.alert_severities
    )

    return SecurityReport(
        findings=report_findings,
        by_severity=dict(sorted(by_severity.items())),
        open_count=open_count,
        actionable=actionable,
        alert_severities=tuple(sorted(radar.alert_severities)),
    )
