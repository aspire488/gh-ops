"""Deterministic intelligence context: the bounded input to interpretation.

A context packages evidence, temporal profiles, trends, correlations,
repository signals, and a deterministic focus into one object whose
digest is the only thing System-1/System-2 ever see. Everything is
bounded: item count, per-section lines, and total digest characters.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from src.intelligence.attention import deterministic_focus, is_meaningful
from src.intelligence.correlate import Correlation, correlate
from src.intelligence.evidence import EvidencePack, build_evidence
from src.intelligence.temporal import (
    TemporalProfile,
    TrendProfile,
    build_profiles,
    build_trends,
)
from src.reporting.model import ReportEvent

#: Bounded digest size (chars) fed to System-1 and System-2.
MAX_DIGEST_CHARS = 4000

#: Maximum trend/correlation lines carried into a digest.
_MAX_TREND_LINES = 5
_MAX_CORRELATION_LINES = 5


@dataclass(frozen=True)
class IntelligenceContext:
    """Everything an interpretation pass may ground against."""

    purpose: str
    pack: EvidencePack
    profiles: dict[str, TemporalProfile]
    trends: tuple[TrendProfile, ...]
    correlations: tuple[Correlation, ...]
    focus: str
    meaningful: bool

    @property
    def evidence_ids(self) -> frozenset[str]:
        return self.pack.ids

    @property
    def urls(self) -> frozenset[str]:
        return self.pack.urls

    @property
    def item_numbers(self) -> frozenset[int]:
        return self.pack.item_numbers

    @property
    def slugs(self) -> frozenset[str]:
        """All ``owner/repo`` slugs claims may mention (pack + trends)."""
        return self.pack.slugs | frozenset(
            trend.repository for trend in self.trends if trend.repository
        )

    def digest(self, *, max_chars: int = MAX_DIGEST_CHARS) -> str:
        """Bounded plain-text digest, deterministic across runs.

        Layout: purpose/focus header, evidence blocks (as many as fit the
        budget), then correlation and trend lines. Hard-truncated as a
        final safety net.
        """
        header = f"Purpose: {self.purpose}\nFocus: {self.focus}"
        tail_parts: list[str] = []
        if self.correlations:
            lines = [f"- {c.statement}" for c in self.correlations[:_MAX_CORRELATION_LINES]]
            tail_parts.append("Correlations:\n" + "\n".join(lines))
        if self.trends:
            lines = [f"- {trend.line}" for trend in self.trends[:_MAX_TREND_LINES]]
            tail_parts.append("Trends:\n" + "\n".join(lines))
        tail = ("\n" + "\n".join(tail_parts)) if tail_parts else ""

        budget = max_chars - len(header) - len(tail)
        blocks: list[str] = []
        used = 0
        for item in self.pack.items:
            block = item.block
            cost = len(block) + 1
            if used + cost > budget:
                break
            blocks.append(block)
            used += cost
        body = "\n".join(blocks)
        digest = "\n".join(part for part in (header, body, tail) if part)
        return digest[:max_chars]


def build_context(
    events: Iterable[ReportEvent],
    *,
    ledger: dict[str, dict],
    purpose: str,
    now: datetime | None = None,
) -> IntelligenceContext:
    """Build a deterministic intelligence context from events and ledger."""
    profiles = build_profiles(ledger, now=now)
    pack = build_evidence(events, profiles=profiles)
    correlations = correlate(pack)
    trends = build_trends(ledger, now=now)
    focus = deterministic_focus(pack)
    meaningful = is_meaningful(pack, correlations, trends)
    return IntelligenceContext(
        purpose=purpose,
        pack=pack,
        profiles=profiles,
        trends=trends,
        correlations=correlations,
        focus=focus,
        meaningful=meaningful,
    )
