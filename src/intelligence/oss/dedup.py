"""Deduplication engine for OSS contribution opportunities.

Uses repository+issue identity (NOT title) for stable deduplication.
Preserves provenance when merging duplicates from multiple queries.
Idempotent: applying dedup twice produces the same result.

No external dependencies in runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.intelligence.oss.models import Opportunity


@dataclass(frozen=True)
class DedupConfig:
    """Configuration for deduplication."""

    enabled: bool = True
    merge_source_queries: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DedupConfig:
        return cls(
            enabled=data.get("enabled", True),
            merge_source_queries=data.get("merge_source_queries", True),
        )


@dataclass
class DedupResult:
    """Result of deduplication."""

    unique: list[Opportunity] = field(default_factory=list)
    duplicates_found: int = 0
    original_count: int = 0

    @property
    def dedup_rate(self) -> float:
        if self.original_count == 0:
            return 0.0
        return self.duplicates_found / self.original_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "unique": [o.to_dict() for o in self.unique],
            "duplicates_found": self.duplicates_found,
            "original_count": self.original_count,
        }


def deduplicate(
    opportunities: list[Opportunity],
    config: DedupConfig | None = None,
) -> DedupResult:
    """Deduplicate opportunities by (repo_full_name, issue_number) identity.

    When duplicates are found:
    - The first occurrence is kept
    - Source queries are merged into source_queries tuple
    - The higher score wins

    Args:
        opportunities: List of opportunities to deduplicate.
        config: Deduplication configuration.

    Returns:
        DedupResult with unique opportunities and stats.
    """
    config = config or DedupConfig()

    if not config.enabled:
        return DedupResult(
            unique=list(opportunities),
            duplicates_found=0,
            original_count=len(opportunities),
        )

    result = DedupResult(original_count=len(opportunities))

    # Identity -> Opportunity mapping
    seen: dict[tuple[str, int], Opportunity] = {}

    for opp in opportunities:
        identity = opp.identity()

        if identity in seen:
            result.duplicates_found += 1
            existing = seen[identity]

            # Merge source queries
            if config.merge_source_queries:
                merged_queries = set(existing.source_queries)
                if opp.source_query:
                    merged_queries.add(opp.source_query)
                if opp.source_queries:
                    merged_queries.update(opp.source_queries)
                if existing.source_query:
                    merged_queries.add(existing.source_query)

                # Keep the higher-scored opportunity with merged provenance
                if opp.score > existing.score:
                    updated = Opportunity(
                        repo_full_name=opp.repo_full_name,
                        issue_number=opp.issue_number,
                        issue_id=opp.issue_id,
                        title=opp.title,
                        html_url=opp.html_url,
                        state=opp.state,
                        user=opp.user,
                        labels=opp.labels,
                        assignee=opp.assignee,
                        assignees=opp.assignees,
                        comments=opp.comments,
                        created_at=opp.created_at,
                        updated_at=opp.updated_at,
                        body=opp.body,
                        repo_stars=opp.repo_stars,
                        repo_forks=opp.repo_forks,
                        repo_open_issues=opp.repo_open_issues,
                        repo_language=opp.repo_language,
                        repo_description=opp.repo_description,
                        score=opp.score,
                        score_breakdown=opp.score_breakdown,
                        source_query=opp.source_query,
                        source_queries=tuple(sorted(merged_queries)),
                        discovered_at=opp.discovered_at,
                    )
                else:
                    updated = Opportunity(
                        repo_full_name=existing.repo_full_name,
                        issue_number=existing.issue_number,
                        issue_id=existing.issue_id,
                        title=existing.title,
                        html_url=existing.html_url,
                        state=existing.state,
                        user=existing.user,
                        labels=existing.labels,
                        assignee=existing.assignee,
                        assignees=existing.assignees,
                        comments=existing.comments,
                        created_at=existing.created_at,
                        updated_at=existing.updated_at,
                        body=existing.body,
                        repo_stars=existing.repo_stars,
                        repo_forks=existing.repo_forks,
                        repo_open_issues=existing.repo_open_issues,
                        repo_language=existing.repo_language,
                        repo_description=existing.repo_description,
                        score=existing.score,
                        score_breakdown=existing.score_breakdown,
                        source_query=existing.source_query,
                        source_queries=tuple(sorted(merged_queries)),
                        discovered_at=existing.discovered_at,
                    )

                seen[identity] = updated
            # If not merging queries, keep first occurrence (do nothing)
        else:
            seen[identity] = opp

    # Maintain insertion order (Python 3.7+ dicts are ordered)
    result.unique = list(seen.values())

    return result


def is_duplicate(
    opp: Opportunity,
    seen_identities: set[tuple[str, int]],
) -> bool:
    """Check if an opportunity is a duplicate of previously seen ones.

    This is the streaming/online version for real-time dedup.

    Args:
        opp: The opportunity to check.
        seen_identities: Set of previously seen (repo, issue_number) tuples.

    Returns:
        True if this is a duplicate.
    """
    return opp.identity() in seen_identities
