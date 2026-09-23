"""Security Intelligence subsystem.

Read-only analysis of Dependabot and code-scanning alerts already collected
into Phase 3 state. No HTTP, no auth, no GitHub writes.

Public API:
    - analyze_security: State → ranked SecurityReport
    - RadarConfig: Radar / monitor configuration
    - SecurityFinding / SecurityReport: Deterministic models
    - SECURITY_RESOURCE_KEY / SECURITY_NOTIFIED_KEY: State keys
"""
from src.intelligence.security.models import (
    DEFAULT_ALERT_SEVERITIES,
    SEVERITY_RANK,
    SecurityFinding,
    SecurityReport,
    normalize_severity,
    parse_identity,
    severity_rank,
)
from src.intelligence.security.radar import (
    SECURITY_NOTIFIED_KEY,
    SECURITY_RESOURCE_KEY,
    RadarConfig,
    analyze_security,
    extract_findings,
)

__all__ = [
    "DEFAULT_ALERT_SEVERITIES",
    "SEVERITY_RANK",
    "SecurityFinding",
    "SecurityReport",
    "normalize_severity",
    "parse_identity",
    "severity_rank",
    "SECURITY_NOTIFIED_KEY",
    "SECURITY_RESOURCE_KEY",
    "RadarConfig",
    "analyze_security",
    "extract_findings",
]
