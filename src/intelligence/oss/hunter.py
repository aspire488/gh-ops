"""OSS Contribution Opportunity Hunter — main orchestrator.

Read-only pipeline: generate queries → search GitHub → filter → score → dedup → sort.
No writes, no LLM, no embeddings, no Telegram integration.

No external dependencies in runtime beyond requests + pyyaml.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.github.client import GitHubClient
from src.github.collectors.issues import search_issues
from src.intelligence.oss.models import Opportunity
from src.intelligence.oss.queries import generate_queries, deduplicate_queries, SearchQuery
from src.intelligence.oss.filters import FilterConfig, filter_opportunities, FilterResult
from src.intelligence.oss.scorer import ScoringConfig, score_opportunity, sort_by_score
from src.intelligence.oss.dedup import DedupConfig, deduplicate, DedupResult

logger = logging.getLogger("gh_ops.intelligence.oss.hunter")


@dataclass(frozen=True)
class HunterConfig:
    """Full configuration for the OSS hunter."""

    enabled: bool = True
    max_results: int = 10
    max_queries: int = 20
    per_query_max_pages: int = 3
    languages: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    custom_queries: list[str] = field(default_factory=list)

    filters: FilterConfig = field(default_factory=FilterConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HunterConfig:
        """Parse from configuration dictionary."""
        oss = data.get("oss_hunter", data)

        # Convert filters dict to FilterConfig if it's a plain dict
        filters_raw = oss.get("filters", {})
        if isinstance(filters_raw, dict):
            filters = FilterConfig.from_dict(filters_raw)
        else:
            filters = filters_raw

        return cls(
            enabled=oss.get("enabled", True),
            max_results=oss.get("max_results", 10),
            max_queries=oss.get("max_queries", 20),
            per_query_max_pages=oss.get("per_query_max_pages", 3),
            languages=oss.get("languages", []),
            categories=oss.get("categories", []),
            custom_queries=oss.get("custom_queries", []),
            filters=filters,
            scoring=ScoringConfig.from_dict(oss),
            dedup=DedupConfig.from_dict(oss.get("dedup", {})),
        )


@dataclass(frozen=True)
class HunterResult:
    """Result of running the OSS hunter pipeline."""

    opportunities: list[Opportunity]
    queries_run: int = 0
    queries_failed: int = 0
    total_issues_found: int = 0
    total_filtered_out: int = 0
    total_duplicates: int = 0
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunities": [o.to_dict() for o in self.opportunities],
            "queries_run": self.queries_run,
            "queries_failed": self.queries_failed,
            "total_issues_found": self.total_issues_found,
            "total_filtered_out": self.total_filtered_out,
            "total_duplicates": self.total_duplicates,
            "errors": list(self.errors),
        }


def _fetch_repo_metadata(
    client: GitHubClient,
    repo_full_name: str,
) -> dict[str, Any]:
    """Fetch minimal repo metadata for scoring enrichment.

    Returns dict with stars, forks, open_issues, language, description.
    Returns empty dict on failure (partial failure is normal).
    """
    try:
        data = client.get(f"/repos/{repo_full_name}")
        return {
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "language": data.get("language"),
            "description": data.get("description"),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch repo metadata for {repo_full_name}: {e}")
        return {}


def run_hunter(
    client: GitHubClient,
    config: HunterConfig | None = None,
    now: datetime | None = None,
) -> HunterResult:
    """Run the OSS contribution opportunity hunter.

    Pipeline:
    1. Generate search queries from config
    2. Execute queries against GitHub search API
    3. Convert results to Opportunity model
    4. Enrich with repository metadata (best-effort)
    5. Apply filter pipeline
    6. Score opportunities
    7. Deduplicate by (repo, issue) identity
    8. Sort by score with stable tie-breaking
    9. Return top-N results

    Args:
        client: Authenticated GitHub client.
        config: Hunter configuration. If None, loads from default config.
        now: Current time. If None, uses UTC now.

    Returns:
        HunterResult with ranked opportunities and pipeline stats.
    """
    config = config or HunterConfig()
    now = now or datetime.now(timezone.utc)

    if not config.enabled:
        return HunterResult(opportunities=[], errors=("OSS hunter disabled",))

    errors: list[str] = []

    # Step 1: Generate queries
    queries = generate_queries(
        languages=config.languages or None,
        categories=config.categories or None,
        include_custom=config.custom_queries or None,
    )
    queries = deduplicate_queries(queries)

    # Limit number of queries
    if len(queries) > config.max_queries:
        queries = queries[:config.max_queries]

    logger.info(f"Generated {len(queries)} search queries")

    # Step 2-3: Execute queries and collect opportunities
    all_opportunities: list[Opportunity] = []
    queries_run = 0
    queries_failed = 0

    for sq in queries:
        try:
            issues = search_issues(
                client,
                query=sq.query,
                per_page=30,
                max_pages=config.per_query_max_pages,
                sort="updated",
            )

            queries_run += 1

            for issue_data in issues:
                opp = Opportunity.from_search_result(
                    issue_data.to_dict() if hasattr(issue_data, 'to_dict') else issue_data,
                    source_query=sq.query,
                )
                all_opportunities.append(opp)

        except Exception as e:
            queries_failed += 1
            error_msg = f"Query failed [{sq.category}]: {sq.query} -> {e}"
            errors.append(error_msg)
            logger.warning(error_msg)
            continue

    total_found = len(all_opportunities)
    logger.info(f"Found {total_found} issues from {queries_run} queries")

    if not all_opportunities:
        return HunterResult(
            opportunities=[],
            queries_run=queries_run,
            queries_failed=queries_failed,
            total_issues_found=0,
            errors=tuple(errors),
        )

    # Step 4: Enrich with repository metadata (best-effort)
    repo_cache: dict[str, dict[str, Any]] = {}
    enriched: list[Opportunity] = []

    for opp in all_opportunities:
        repo_name = opp.repo_full_name

        if repo_name not in repo_cache:
            repo_cache[repo_name] = _fetch_repo_metadata(client, repo_name)

        meta = repo_cache[repo_name]
        if meta:
            enriched.append(Opportunity(
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
                repo_stars=meta.get("stars", 0),
                repo_forks=meta.get("forks", 0),
                repo_open_issues=meta.get("open_issues", 0),
                repo_language=meta.get("language"),
                repo_description=meta.get("description"),
                source_query=opp.source_query,
            ))
        else:
            enriched.append(opp)

    # Step 5: Filter
    filter_result = filter_opportunities(enriched, config.filters, now)
    total_filtered_out = filter_result.total_excluded
    logger.info(f"Filtered: {len(filter_result.passed)} passed, {total_filtered_out} excluded")

    if not filter_result.passed:
        return HunterResult(
            opportunities=[],
            queries_run=queries_run,
            queries_failed=queries_failed,
            total_issues_found=total_found,
            total_filtered_out=total_filtered_out,
            errors=tuple(errors),
        )

    # Step 6: Score
    scored = [score_opportunity(opp, config.scoring, now) for opp in filter_result.passed]

    # Step 7: Deduplicate
    dedup_result = deduplicate(scored, config.dedup)
    total_duplicates = dedup_result.duplicates_found
    logger.info(f"Dedup: {len(dedup_result.unique)} unique, {total_duplicates} duplicates merged")

    # Step 8: Sort and take top-N
    sorted_opps = sort_by_score(dedup_result.unique, config.scoring.tie_breaking_method)
    top_n = sorted_opps[:config.max_results]

    logger.info(f"Final: {len(top_n)} opportunities returned")

    return HunterResult(
        opportunities=top_n,
        queries_run=queries_run,
        queries_failed=queries_failed,
        total_issues_found=total_found,
        total_filtered_out=total_filtered_out,
        total_duplicates=total_duplicates,
        errors=tuple(errors),
    )
