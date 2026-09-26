"""Attention allocation: triage labels, report modes, and focus decisions.

Deterministic rules always win. Laya System-1 may suggest a focus label,
but a deterministic ``security``/``attention`` context focus is never
overridden, model scores are never surfaced, and an empty allocation set
would mean silence (no current subsystem uses it).
"""
from __future__ import annotations

from enum import Enum

from src.intelligence.correlate import Correlation
from src.intelligence.evidence import EvidencePack
from src.intelligence.laya_adapter import decide
from src.intelligence.temporal import TrendProfile
from src.reporting.convert import MONITORING_SUBSYSTEMS
from src.reporting.model import (
    SUBSYSTEM_CI,
    SUBSYSTEM_DAILY,
    SUBSYSTEM_DEVELOPER,
    SUBSYSTEM_ENDPOINT,
    SUBSYSTEM_MONITORING,
    SUBSYSTEM_OSS,
    SUBSYSTEM_RELEASE,
    SUBSYSTEM_REPOSITORY,
    SUBSYSTEM_SECURITY,
    SUBSYSTEM_SYSTEM,
    SUBSYSTEM_WEEKLY,
    ReportEvent,
    Severity,
)


#: Report delivery modes an event may be allocated to.
class ReportMode(str, Enum):
    IMMEDIATE = "immediate"
    DAILY = "daily"
    WEEKLY = "weekly"


#: Extended focus labels for context triage (interpret.FOCUS_LABELS stays
#: pinned for the legacy brief path; this set is for the context path).
CONTEXT_FOCUS_LABELS: tuple[str, ...] = (
    "attention",
    "security",
    "operations",
    "developer",
    "oss",
    "release",
    "recovery",
    "trend",
    "quiet",
    "mixed",
)

TRIAGE_INSTRUCTIONS = (
    "Triage this GH-OPS intelligence context for an operator. Choose "
    "'quiet' when nothing needs interpretation, 'attention' when "
    "action-required work dominates, 'security' when security findings "
    "dominate, 'operations' when repository health dominates, 'developer' "
    "when developer activity dominates, 'oss' when opportunity scanning "
    "dominates, 'release' when releases dominate, 'recovery' when "
    "resolutions dominate, 'trend' when recurring patterns dominate, and "
    "'mixed' when no single focus dominates."
)

#: Dominant-subsystem → focus label mapping for deterministic focus.
_DOMINANT_LABEL: dict[str, str] = {
    SUBSYSTEM_SECURITY: "security",
    SUBSYSTEM_CI: "operations",
    SUBSYSTEM_MONITORING: "operations",
    SUBSYSTEM_REPOSITORY: "operations",
    SUBSYSTEM_ENDPOINT: "operations",
    SUBSYSTEM_SYSTEM: "operations",
    SUBSYSTEM_DEVELOPER: "developer",
    SUBSYSTEM_OSS: "oss",
    SUBSYSTEM_RELEASE: "release",
    SUBSYSTEM_DAILY: "operations",
    SUBSYSTEM_WEEKLY: "operations",
}


def allocation_for(event: ReportEvent) -> frozenset[ReportMode]:
    """Deterministic report allocation for an event.

    Monitoring subsystems deliver immediately plus both briefs; developer
    and OSS content skips the immediate path; every current subsystem lands
    in at least the briefs (gates may only suppress, never add modes).
    """
    if event.subsystem in MONITORING_SUBSYSTEMS:
        return frozenset({ReportMode.IMMEDIATE, ReportMode.DAILY, ReportMode.WEEKLY})
    if event.subsystem in (SUBSYSTEM_DEVELOPER, SUBSYSTEM_OSS):
        return frozenset({ReportMode.DAILY, ReportMode.WEEKLY})
    return frozenset({ReportMode.DAILY, ReportMode.WEEKLY})


def deterministic_focus(pack: EvidencePack) -> str:
    """Deterministic focus label for an evidence pack.

    Precedence: empty → quiet; open security findings → security;
    action-required items → attention; everything resolved → recovery;
    otherwise the strict dominant subsystem (ties → mixed).
    """
    if not pack.items:
        return "quiet"
    if pack.has_security_action():
        return "security"
    if pack.count_severity(Severity.ACTION_REQUIRED.value) > 0:
        return "attention"
    if all(item.severity == Severity.RESOLVED.value for item in pack.items):
        return "recovery"

    counts = pack.subsystem_counts()
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    top, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return "mixed"
    return _DOMINANT_LABEL.get(top, "operations")


def is_meaningful(
    pack: EvidencePack,
    correlations: tuple[Correlation, ...],
    trends: tuple[TrendProfile, ...],
) -> bool:
    """Whether an interpretation pass is warranted for this context.

    Quiet informational light days are not: no action items, no
    correlations, no recurring trends, and fewer than four evidence items.
    """
    if not pack.items:
        return False
    if pack.count_severity(Severity.ACTION_REQUIRED.value) > 0:
        return True
    if pack.count_severity(Severity.IMPORTANT.value) > 0:
        return True
    if correlations:
        return True
    if any(trend.state == "recurring" for trend in trends):
        return True
    return len(pack.items) >= 4


def reconcile(deterministic: str, laya_label: str) -> str:
    """Merge the Laya suggestion with the deterministic focus.

    Deterministic ``security``/``attention`` always wins; an invalid Laya
    label falls back to the deterministic focus; otherwise Laya refines
    within the allowed label set.
    """
    if laya_label not in CONTEXT_FOCUS_LABELS:
        return deterministic
    if deterministic in ("attention", "security"):
        return deterministic
    if laya_label == "quiet":
        return "quiet"
    return laya_label


def triage(digest: str, *, loader=None) -> tuple[str, float] | None:
    """Laya System-1 label for a digest, or None (advisory only)."""
    return decide(TRIAGE_INSTRUCTIONS, digest, CONTEXT_FOCUS_LABELS, loader=loader)
