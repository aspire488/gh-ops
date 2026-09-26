"""Cross-subsystem correlation over an evidence pack.

Correlations are deterministic observations about co-occurrence ("these
signals are active in the same repository and window"), never causal claims.
Each correlation lists the exact evidence ids it is grounded in.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.intelligence.evidence import Evidence, EvidencePack
from src.reporting.model import (
    SUBSYSTEM_CI,
    SUBSYSTEM_DEVELOPER,
    SUBSYSTEM_ENDPOINT,
    SUBSYSTEM_RELEASE,
    SUBSYSTEM_SECURITY,
    SUBSYSTEM_SYSTEM,
)


@dataclass(frozen=True)
class Correlation:
    """One grounded cross-signal observation."""

    kind: str
    repository: str
    evidence_ids: tuple[str, ...]
    statement: str


def _ids(items: list[Evidence]) -> tuple[str, ...]:
    return tuple(item.eid for item in sorted(items, key=lambda evidence: evidence.eid))


def correlate(pack: EvidencePack) -> tuple[Correlation, ...]:
    """Correlate evidence items that share a repository, in pack order.

    Wording stays non-causal: co-occurrence and overlap only. One
    correlation per repository (the most specific match wins).
    """
    by_repo: dict[str, list[Evidence]] = {}
    for item in pack.items:
        if item.repository:
            by_repo.setdefault(item.repository, []).append(item)

    correlations: list[Correlation] = []
    for repository in sorted(by_repo):
        items = by_repo[repository]
        subsystems = {item.subsystem for item in items}
        ids = _ids(items)
        dev_ref = next(
            (
                item.item_ref
                for item in items
                if item.subsystem == SUBSYSTEM_DEVELOPER and "#" in item.item_ref
            ),
            "",
        )
        if dev_ref and (
            {SUBSYSTEM_CI, SUBSYSTEM_ENDPOINT, SUBSYSTEM_SYSTEM} & subsystems
        ):
            correlations.append(
                Correlation(
                    kind="dev_ci",
                    repository=repository,
                    evidence_ids=ids,
                    statement=f"{dev_ref} activity and CI signals are active in the same development window.",
                )
            )
        elif SUBSYSTEM_SECURITY in subsystems and SUBSYSTEM_RELEASE in subsystems:
            correlations.append(
                Correlation(
                    kind="security_release",
                    repository=repository,
                    evidence_ids=ids,
                    statement=f"Security findings and release activity overlap in {repository}.",
                )
            )
        elif SUBSYSTEM_SECURITY in subsystems and SUBSYSTEM_CI in subsystems:
            correlations.append(
                Correlation(
                    kind="security_ci",
                    repository=repository,
                    evidence_ids=ids,
                    statement=f"Security findings and CI activity are active in {repository}.",
                )
            )
        elif SUBSYSTEM_RELEASE in subsystems and SUBSYSTEM_CI in subsystems:
            correlations.append(
                Correlation(
                    kind="release_ci",
                    repository=repository,
                    evidence_ids=ids,
                    statement=f"Release activity and CI signals overlap in {repository}.",
                )
            )
        elif len(subsystems) >= 2:
            correlations.append(
                Correlation(
                    kind="multi_subsystem",
                    repository=repository,
                    evidence_ids=ids,
                    statement=f"Activity spans {len(subsystems)} subsystems in {repository}.",
                )
            )
    return tuple(correlations)
