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
    SUBSYSTEM_LABEL,
    ReportEvent,
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
_CONTEXT_KEYS: tuple[str, ...] = (
    "branch",
    "workflow_name",
    "conclusion",
    "package_name",
    "severity",
    "event_type",
    "status",
    "resource",
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
        elif key == "status":
            lines.append(f"status: {value}")
        elif key == "resource" and str(value) != event.repository:
            lines.append(f"resource: {value}")
    return lines


def event_block(event: ReportEvent) -> str:
    """Render one event as a self-contained plain-text block.

    Layout::

        • [P0] owner/repo · CI
          Title
          context lines…
          description
          https://…
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
    lines.extend(f"  {line}" for line in event_context(event))
    if event.description:
        lines.append(f"  {event.description}")
    if event.url:
        lines.append(f"  {event.url}")
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


def _repository_groups(
    ordered: Sequence[ReportEvent],
) -> list[tuple[str, list[ReportEvent]]]:
    """Group events by repository, preserving priority order within each.

    Repositories appear in the order of their highest-priority event so the
    most urgent repo surfaces first.
    """
    groups: dict[str, list[ReportEvent]] = {}
    order: list[str] = []
    for event in ordered:
        key = event.repository or event.subsystem
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(event)
    return [(key, groups[key]) for key in order]


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
) -> tuple[str, list[str]] | None:
    """Build the daily brief, or None when there is nothing meaningful.

    Sections are repository-first; empty categories are omitted. A quiet run
    with healthy collectors and no events returns None (no-op silence).
    Data-quality issues surface only when collectors failed.
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
    if repository_count > 0:
        blocks.append(f"• {repository_count} repositories monitored")

    for repo, items in _repository_groups(ordered):
        blocks.append(f"▸ {repo}")
        blocks.extend(event_block(event) for event in items)

    if quality_line:
        blocks.append(quality_line)

    return title, _omit_empty(blocks)


def build_weekly_brief(
    events: Sequence[ReportEvent],
    *,
    coverage_start: datetime | None = None,
    coverage_end: datetime | None = None,
    repository_count: int = 0,
    developer_events: int = 0,
    data_quality: dict[str, Any] | None = None,
) -> tuple[str, list[str]] | None:
    """Build the weekly brief, or None when empty.

    Only events not already briefed for this window should be passed by the
    caller. Empty sections are omitted; data-quality issues surface only when
    collectors failed.
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
    if repository_count > 0:
        blocks.append(f"• {repository_count} repositories monitored")
    if developer_events > 0:
        blocks.append(f"• {developer_events} developer activities")

    for repo, items in _repository_groups(ordered):
        blocks.append(f"▸ {repo}")
        blocks.extend(event_block(event) for event in items)

    if quality_line:
        blocks.append(quality_line)

    return title, _omit_empty(blocks)
