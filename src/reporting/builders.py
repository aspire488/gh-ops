"""Deterministic report builders (plain-text blocks for Telegram)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from src.reporting.aggregate import dedupe, group_by_severity, prioritize
from src.reporting.model import (
    SEVERITY_EMOJI,
    SEVERITY_LABEL,
    ReportEvent,
    Severity,
)
from src.utils.time import format_ist_coverage, format_ist_date, format_ist_datetime


def event_block(event: ReportEvent) -> str:
    """Render one event as a self-contained plain-text block."""
    lines: list[str] = []
    header = event.repository or event.subsystem
    if event.title:
        lines.append(f"• {header}" if header else f"• {event.title}")
        if header and event.title:
            lines.append(f"  {event.title}")
    else:
        lines.append(f"• {header}" if header else f"• {event.event_type}")
    if event.description:
        lines.append(f"  {event.description}")
    if event.url:
        lines.append(f"  {event.url}")
    return "\n".join(lines)


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
) -> tuple[str, list[str]] | None:
    """Build the daily brief, or None when there is nothing meaningful."""
    ordered = prioritize(dedupe(events))
    if not ordered:
        return None

    end = coverage_end or datetime.now(timezone.utc)
    start = coverage_start or (end - timedelta(hours=24))

    title = f"🔵 GH-OPS · {format_ist_date(end)} DAILY BRIEF"
    blocks: list[str] = [
        format_ist_datetime(end),
        f"Coverage: {format_ist_coverage(start, end)}",
        f"• {repository_count} repositories monitored",
    ]

    counts: dict[str, int] = {}
    for event in ordered:
        counts[event.subsystem] = counts.get(event.subsystem, 0) + 1
    if counts:
        summary = ", ".join(
            f"{name}: {count}" for name, count in sorted(counts.items())
        )
        blocks.append(summary)

    action_items = [e for e in ordered if e.severity is Severity.ACTION_REQUIRED]
    attention = action_items or [e for e in ordered if e.severity is Severity.IMPORTANT]
    if attention:
        blocks.append("Needs attention:")
        blocks.extend(event_block(event) for event in attention[:5])

    return title, blocks


def build_weekly_brief(
    events: Sequence[ReportEvent],
    *,
    coverage_start: datetime | None = None,
    coverage_end: datetime | None = None,
    repository_count: int = 0,
    developer_events: int = 0,
) -> tuple[str, list[str]] | None:
    """Build the weekly brief, or None when empty."""
    ordered = prioritize(dedupe(events))
    end = coverage_end or datetime.now(timezone.utc)
    start = coverage_start or (end - timedelta(days=7))

    if not ordered and developer_events <= 0 and repository_count <= 0:
        return None

    title = f"🔵 GH-OPS · {format_ist_date(end)} WEEKLY BRIEF"
    blocks: list[str] = [
        format_ist_datetime(end),
        f"Coverage: {format_ist_coverage(start, end)}",
        f"• {repository_count} repositories monitored",
    ]
    if developer_events:
        blocks.append(f"• {developer_events} developer activities")

    if ordered:
        counts: dict[str, int] = {}
        for event in ordered:
            counts[event.subsystem] = counts.get(event.subsystem, 0) + 1
        blocks.append(
            ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        )
        action_items = [e for e in ordered if e.severity is Severity.ACTION_REQUIRED]
        attention = action_items or [
            e for e in ordered if e.severity is Severity.IMPORTANT
        ]
        if attention:
            blocks.append("Needs attention:")
            blocks.extend(event_block(event) for event in attention[:5])

    return title, blocks
