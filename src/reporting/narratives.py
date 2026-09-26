"""Narrative blocks for briefs and radar updates (pure, deterministic).

Small render helpers jobs composes into briefs: repository signals,
trend lines, the focus line, and OSS radar-update blocks. Everything is
precomputed presentation over already-verified data — no intelligence,
no state, no network.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.reporting.model import (
    SUBSYSTEM_LABEL,
    ReportEvent,
    Severity,
)

#: Subsystems whose activity counts as "needs attention" for the focus line.
_ATTENTION_SEVERITIES = frozenset({Severity.ACTION_REQUIRED, Severity.IMPORTANT})


def repository_signal_blocks(events: Sequence[ReportEvent]) -> list[str]:
    """Cross-subsystem repository signals, or [] when nothing qualifies.

    Only repositories spanning ≥2 distinct subsystems qualify — a single
    subsystem on a repo is already visible in its own section.
    """
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        if not event.repository:
            continue
        per_repo = counts.setdefault(event.repository, {})
        per_repo[event.subsystem] = per_repo.get(event.subsystem, 0) + 1

    signals = {
        repo: per_repo for repo, per_repo in counts.items() if len(per_repo) >= 2
    }
    if not signals:
        return []
    blocks = ["📦 Repository Signals"]
    for repo in sorted(signals):
        per_repo = signals[repo]
        parts = [
            f"{SUBSYSTEM_LABEL.get(subsystem, subsystem.upper())}×{count}"
            for subsystem, count in sorted(per_repo.items())
        ]
        blocks.append(f"{repo}: {' · '.join(parts)}")
    return blocks


def trend_blocks(lines: Sequence[str]) -> list[str]:
    """Trend section from precomputed trend lines, or [] when empty."""
    if not lines:
        return []
    return ["📈 Trends", *[f"• {line}" for line in lines]]


def focus_blocks(events: Sequence[ReportEvent]) -> list[str]:
    """Focus line counting items that need attention, or [] when quiet."""
    count = sum(1 for event in events if event.severity in _ATTENTION_SEVERITIES)
    if count <= 0:
        return []
    return ["🎯 Focus", f"🔴 {count} items need attention"]


def _oss_update_block(opportunity: Any) -> str:
    """One radar-update block for a previously seen, changed opportunity."""
    repo = str(getattr(opportunity, "repo_full_name", "") or "")
    number = getattr(opportunity, "issue_number", 0) or 0
    title = str(getattr(opportunity, "title", "") or "")
    lines = [f"🔄 {repo}#{number}"]
    if title:
        lines.append(f"  {title}")

    meta: list[str] = []
    comments = int(getattr(opportunity, "comments", 0) or 0)
    if comments:
        meta.append(f"{comments} comments")
    stars = int(getattr(opportunity, "repo_stars", 0) or 0)
    if stars:
        meta.append(f"★{stars}")
    updated = getattr(opportunity, "updated_at", None)
    if updated is not None:
        meta.append(f"updated {str(updated)[:10]}")
    state = str(getattr(opportunity, "state", "") or "")
    if state:
        meta.append(state)
    if meta:
        lines.append("  " + " · ".join(meta))

    url = str(getattr(opportunity, "html_url", "") or "")
    if url:
        lines.append(f"  Open → {url}")
    return "\n".join(lines)


def oss_update_blocks(opportunities: Sequence[Any]) -> list[str]:
    """Blocks for changed, previously-seen opportunities, or [] when empty.

    Delivered under a distinct radar title so the pinned OSS
    opportunities contract title is never reused for updates.
    """
    if not opportunities:
        return []
    blocks = ["Changed opportunities"]
    blocks.extend(_oss_update_block(opportunity) for opportunity in opportunities)
    return blocks
