"""Deterministic evidence packs: the only facts interpretation may use.

An evidence pack is a bounded, deduplicated, priority-ordered projection of
report events enriched with cross-run temporal state. Every claim made by
System-1/System-2 must ground against a pack; the pack itself is pure
deterministic data with no model involvement.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from src.intelligence.temporal import TemporalProfile
from src.reporting.aggregate import dedupe, event_key
from src.reporting.ledger import condition_key
from src.reporting.model import SUBSYSTEM_SECURITY, ReportEvent, Severity

#: Hard cap on evidence items carried into interpretation.
MAX_EVIDENCE_ITEMS = 40

#: Metadata keys copied into evidence facts (bounded, no free-form dumps).
_FACT_KEYS: tuple[str, ...] = (
    "branch",
    "workflow_name",
    "conclusion",
    "package_name",
    "item_id",
)


@dataclass(frozen=True)
class Evidence:
    """One verified fact an interpretation claim may reference."""

    eid: str
    subsystem: str
    severity: str
    repository: str
    title: str
    observed_at: str
    url: str
    facts: tuple[tuple[str, str], ...]
    temporal: str
    item_ref: str

    @property
    def block(self) -> str:
        """Multi-line digest block for this item."""
        header = (
            f"{self.eid} [{self.subsystem}|{self.severity}] "
            f"{self.repository or '-'} · {self.title}"
        )
        meta = f"observed {self.observed_at}" if self.observed_at else "observed unknown"
        if self.temporal:
            meta = f"{meta} · {self.temporal}"
        lines = [header, f"  {meta}"]
        if self.facts:
            lines.append("  " + " ".join(f"{key}={value}" for key, value in self.facts))
        return "\n".join(lines)


@dataclass(frozen=True)
class EvidencePack:
    """Bounded set of evidence items with derived grounding sets."""

    items: tuple[Evidence, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(item.eid for item in self.items)

    @property
    def repositories(self) -> frozenset[str]:
        return frozenset(item.repository for item in self.items if item.repository)

    @property
    def urls(self) -> frozenset[str]:
        return frozenset(item.url for item in self.items if item.url)

    @property
    def slugs(self) -> frozenset[str]:
        """``owner/repo`` slugs an interpretation may mention.

        Includes repositories plus slash-bearing fact values (branch names
        like ``feature/x``) so grounding never blocks a legitimate reference.
        """
        slugs = set(self.repositories)
        for item in self.items:
            for _key, value in item.facts:
                if "/" in value:
                    slugs.add(value)
        return frozenset(slugs)

    @property
    def item_numbers(self) -> frozenset[int]:
        """Issue/PR numbers present in evidence references (from ``#N``)."""
        numbers: set[int] = set()
        for item in self.items:
            for source in (item.item_ref, item.url):
                numbers.update(int(match) for match in re.findall(r"#(\d{1,9})", source))
        return frozenset(numbers)

    def subsystem_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.subsystem] = counts.get(item.subsystem, 0) + 1
        return counts

    def has_security_action(self) -> bool:
        """True when any open security finding needs action or attention."""
        return any(
            item.subsystem == SUBSYSTEM_SECURITY
            and item.severity in (Severity.ACTION_REQUIRED.value, Severity.IMPORTANT.value)
            for item in self.items
        )

    def count_severity(self, severity: str) -> int:
        return sum(1 for item in self.items if item.severity == severity)


def build_evidence(
    events: Iterable[ReportEvent],
    *,
    profiles: Mapping[str, TemporalProfile] | None = None,
    limit: int = MAX_EVIDENCE_ITEMS,
) -> EvidencePack:
    """Project report events into a bounded, deterministically ordered pack.

    Order: severity, then subsystem, then event key. Deduplicated by event
    key first so repeated observations collapse to one item.
    """
    unique = dedupe(events)
    ordered = sorted(
        unique,
        key=lambda event: (event.severity.value, event.subsystem, event_key(event)),
    )
    items: list[Evidence] = []
    for index, event in enumerate(ordered[:limit], start=1):
        profile = None
        if profiles is not None and event.identity:
            profile = profiles.get(condition_key(event.identity))
        facts = tuple(
            (key, str(event.metadata.get(key)))
            for key in _FACT_KEYS
            if event.metadata.get(key) not in (None, "", False)
        )
        items.append(
            Evidence(
                eid=f"E{index}",
                subsystem=event.subsystem,
                severity=event.severity.value,
                repository=event.repository,
                title=event.title,
                observed_at=event.timestamp,
                url=event.url,
                facts=facts,
                temporal=profile.summary if profile is not None else "",
                item_ref=event.identity,
            )
        )
    return EvidencePack(items=tuple(items))
