"""Deterministic report builders (plain-text blocks for Telegram)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from src.reporting.aggregate import dedupe, group_by_severity, prioritize
from src.reporting.model import (
    PRIORITY_LABEL,
    SEVERITY_EMOJI,
    SEVERITY_LABEL,
    SEVERITY_ORDER,
    SUBSYSTEM_CI,
    SUBSYSTEM_DEVELOPER,
    SUBSYSTEM_ENDPOINT,
    SUBSYSTEM_LABEL,
    SUBSYSTEM_MONITORING,
    SUBSYSTEM_OSS,
    SUBSYSTEM_RELEASE,
    SUBSYSTEM_REPOSITORY,
    SUBSYSTEM_SECURITY,
    SUBSYSTEM_SYSTEM,
    ReportEvent,
    Severity,
)
from src.utils.time import format_ist_coverage, format_ist_date, format_ist_datetime


def report_name_for_events(events: Sequence[ReportEvent]) -> str:
    """Title report name for a monitor batch.

    A single-subsystem batch uses that subsystem's display label (CI, RELEASE,
    REPO, ENDPOINT, SECURITY). Mixed or unknown batches fall back to MONITORING.
    """
    subsystems = {event.subsystem for event in events}
    if len(subsystems) == 1:
        only = next(iter(subsystems))
        if only in SUBSYSTEM_LABEL:
            return SUBSYSTEM_LABEL[only]
        if only:
            return only.upper()
    return "MONITORING"


#: Metadata keys rendered as one-line context under the title (in order).
#: Raw dumps (status, resource) are deliberately excluded: messages carry
#: intelligence, not monitor bookkeeping.
_CONTEXT_KEYS: tuple[str, ...] = (
    "branch",
    "workflow_name",
    "conclusion",
    "package_name",
    "severity",
    "event_type",
)


def event_context(event: ReportEvent) -> list[str]:
    """Subsystem-specific context lines for an event (deterministic order)."""
    lines: list[str] = []
    meta = event.metadata or {}
    for key in _CONTEXT_KEYS:
        if key == "event_type":
            continue  # already represented by the block header
        value = meta.get(key)
        if value in (None, "", False):
            continue
        if key == "branch":
            lines.append(f"branch: {value}")
        elif key == "workflow_name":
            lines.append(f"workflow: {value}")
        elif key == "conclusion":
            lines.append(f"conclusion: {value}")
        elif key == "package_name":
            lines.append(f"package: {value}")
        elif key == "severity":
            lines.append(f"alert severity: {value}")
    return lines


#: Subsystems whose URL points at a workflow run (label the link for humans).
_RUN_LINK_SUBSYSTEMS = frozenset({SUBSYSTEM_CI, SUBSYSTEM_SYSTEM})


def event_block(event: ReportEvent) -> str:
    """Render one event as a self-contained plain-text block.

    Layout::

        • [P0] owner/repo · CI
          Title
          • context lines…
          description
          Open run → https://…
    """
    lines: list[str] = []
    priority = PRIORITY_LABEL[event.effective_priority]
    subsystem = SUBSYSTEM_LABEL.get(event.subsystem, event.subsystem.upper())
    header_target = event.repository or event.subsystem or event.event_type
    if event.title:
        lines.append(f"• [{priority}] {header_target} · {subsystem}")
        lines.append(f"  {event.title}")
    else:
        lines.append(f"• [{priority}] {header_target} · {subsystem}")
    lines.extend(f"  • {line}" for line in event_context(event))
    if event.description:
        lines.append(f"  {event.description}")
    if event.url:
        label = "Open run" if event.subsystem in _RUN_LINK_SUBSYSTEMS else "Open"
        lines.append(f"  {label} → {event.url}")
    return "\n".join(lines)


def data_quality_block(data_quality: dict[str, Any] | None) -> str | None:
    """One-line data-quality note, or None when healthy/absent.

    Healthy or empty health is omitted so a clean run does not advertise
    "100%" or collector counts. Only incomplete collectors surface.
    """
    if not data_quality:
        return None
    incomplete = int(data_quality.get("incomplete") or 0)
    collectors = int(data_quality.get("collectors") or 0)
    if incomplete <= 0 or collectors <= 0:
        return None
    pct = data_quality.get("completeness_pct")
    detail = f"{incomplete}/{collectors} collectors incomplete"
    if isinstance(pct, (int, float)):
        detail = f"{detail} ({pct}% complete)"
    return f"⚠️ Data quality: {detail}"


#: Brief section order (after 🚨 Attention): header → subsystems covered.
_BRIEF_SECTIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("🛡️ Security", frozenset({SUBSYSTEM_SECURITY})),
    (
        "CI",
        frozenset(
            {
                SUBSYSTEM_CI,
                SUBSYSTEM_MONITORING,
                SUBSYSTEM_ENDPOINT,
                SUBSYSTEM_SYSTEM,
            }
        ),
    ),
    ("🧭 OSS", frozenset({SUBSYSTEM_OSS})),
    ("🧑‍💻 Developer", frozenset({SUBSYSTEM_DEVELOPER})),
    (
        "🚀 Releases & Repositories",
        frozenset({SUBSYSTEM_RELEASE, SUBSYSTEM_REPOSITORY}),
    ),
)

_SEVERITY_COUNT_LABEL: dict[Severity, str] = {
    Severity.ACTION_REQUIRED: "action required",
    Severity.IMPORTANT: "important",
    Severity.INFORMATION: "informational",
    Severity.RESOLVED: "resolved",
}


def _brief_sections(
    ordered: Sequence[ReportEvent],
) -> list[tuple[str, list[ReportEvent]]]:
    """Partition ordered events into the intelligence-report sections.

    Action-required events lead under 🚨 Attention regardless of subsystem;
    everything else follows in the canonical topical order. Unknown
    subsystems are grouped last (never dropped).
    """
    attention = [e for e in ordered if e.severity is Severity.ACTION_REQUIRED]
    rest = [e for e in ordered if e.severity is not Severity.ACTION_REQUIRED]

    sections: list[tuple[str, list[ReportEvent]]] = []
    if attention:
        sections.append(("🚨 Attention", attention))

    classified: set[str] = set()
    for header, subsystems in _BRIEF_SECTIONS:
        items = [e for e in rest if e.subsystem in subsystems]
        if items:
            sections.append((header, items))
            classified |= subsystems

    leftovers: dict[str, list[ReportEvent]] = {}
    for event in rest:
        if event.subsystem not in classified:
            leftovers.setdefault(event.subsystem or "monitoring", []).append(event)
    for subsystem, items in leftovers.items():
        header = SUBSYSTEM_LABEL.get(subsystem, subsystem.upper())
        sections.append((header, items))
    return sections


def _overall_line(events: Sequence[ReportEvent]) -> str:
    """One-line severity census for the foot of a brief."""
    counts: dict[Severity, int] = {severity: 0 for severity in SEVERITY_ORDER}
    for event in events:
        counts[event.severity] = counts.get(event.severity, 0) + 1
    parts = [
        f"{counts[severity]} {_SEVERITY_COUNT_LABEL[severity]}"
        for severity in SEVERITY_ORDER
        if counts[severity]
    ]
    return f"Overall: {', '.join(parts)}" if parts else "Overall: quiet"


def _omit_empty(lines: list[str]) -> list[str]:
    """Drop blank lines so briefs never emit empty sections."""
    return [line for line in lines if line and line.strip()]


def build_report(
    events: Sequence[ReportEvent],
    *,
    report_name: str,
    show_severity_sections: bool = True,
) -> tuple[str, list[str]] | None:
    """Build (title, blocks) from events, or None when empty."""
    ordered = prioritize(dedupe(events))
    if not ordered:
        return None

    top = ordered[0].severity
    title = f"{SEVERITY_EMOJI[top]} GH-OPS · {report_name}"

    groups = group_by_severity(ordered)
    show_sections = show_severity_sections and len(groups) > 1

    blocks: list[str] = []
    for severity, items in groups.items():
        if show_sections:
            blocks.append(SEVERITY_LABEL[severity])
        blocks.extend(event_block(event) for event in items)
    return title, blocks


def build_daily_brief(
    events: Sequence[ReportEvent],
    *,
    coverage_start: datetime | None = None,
    coverage_end: datetime | None = None,
    repository_count: int = 0,
    data_quality: dict[str, Any] | None = None,
    headline: str | None = None,
    extra_sections: Sequence[str] | None = None,
) -> tuple[str, list[str]] | None:
    """Build the daily brief, or None when there is nothing meaningful.

    Sections follow the intelligence-report order (attention first, then
    topical categories); empty sections are omitted. A quiet run with
    healthy collectors and no events returns None (no-op silence).
    Data-quality issues surface only when collectors failed.
    ``headline`` is an optional validated interpretation line (advisory
    display only); when absent the brief is byte-identical to the
    deterministic-only rendering. ``extra_sections`` are additional blocks
    (repository signals, trends, focus) appended before the quality line;
    when absent/empty the brief is byte-identical to the original.
    """
    ordered = prioritize(dedupe(events))
    quality_line = data_quality_block(data_quality)
    if not ordered and not quality_line:
        return None

    end = coverage_end or datetime.now(timezone.utc)
    start = coverage_start or (end - timedelta(hours=24))

    title = f"🔵 GH-OPS · {format_ist_date(end)} DAILY BRIEF"
    blocks: list[str] = [
        format_ist_datetime(end),
        f"Coverage: {format_ist_coverage(start, end)}",
    ]
    if headline:
        blocks.append(f"🧠 {headline}")
    if repository_count > 0:
        blocks.append(f"• {repository_count} repositories monitored")

    for header, items in _brief_sections(ordered):
        blocks.append(header)
        blocks.extend(event_block(event) for event in items)

    if extra_sections:
        blocks.extend(line for line in extra_sections if line)
    if quality_line:
        blocks.append(quality_line)
    blocks.append(_overall_line(ordered))

    return title, _omit_empty(blocks)


def build_weekly_brief(
    events: Sequence[ReportEvent],
    *,
    coverage_start: datetime | None = None,
    coverage_end: datetime | None = None,
    repository_count: int = 0,
    developer_events: int = 0,
    data_quality: dict[str, Any] | None = None,
    headline: str | None = None,
    extra_sections: Sequence[str] | None = None,
) -> tuple[str, list[str]] | None:
    """Build the weekly brief, or None when empty.

    Only events not already briefed for this window should be passed by the
    caller. Empty sections are omitted; data-quality issues surface only when
    collectors failed. ``headline`` is an optional validated interpretation
    line (advisory display only). ``extra_sections`` are additional blocks
    appended before the quality line; absent/empty keeps the brief
    byte-identical to the original.
    """
    ordered = prioritize(dedupe(events))
    end = coverage_end or datetime.now(timezone.utc)
    start = coverage_start or (end - timedelta(days=7))
    quality_line = data_quality_block(data_quality)

    if not ordered and developer_events <= 0 and not quality_line:
        return None

    title = f"🔵 GH-OPS · {format_ist_date(end)} WEEKLY BRIEF"
    blocks: list[str] = [
        format_ist_datetime(end),
        f"Coverage: {format_ist_coverage(start, end)}",
    ]
    if headline:
        blocks.append(f"🧠 {headline}")
    if repository_count > 0:
        blocks.append(f"• {repository_count} repositories monitored")
    if developer_events > 0:
        blocks.append(f"• {developer_events} developer activities")

    for header, items in _brief_sections(ordered):
        blocks.append(header)
        blocks.extend(event_block(event) for event in items)

    if extra_sections:
        blocks.extend(line for line in extra_sections if line)
    if quality_line:
        blocks.append(quality_line)
    blocks.append(_overall_line(ordered))

    return title, _omit_empty(blocks)
