"""OSS Contribution Intelligence subsystem.

Read-only pipeline for discovering and ranking contribution opportunities
across GitHub repositories.

Public API:
    - run_hunter: Main entry point
    - HunterConfig: Configuration
    - HunterResult: Pipeline result
    - Opportunity: Single opportunity model
    - ScoreBreakdown: Transparent scoring breakdown
"""
from src.intelligence.oss.models import Opportunity, ScoreBreakdown
from src.intelligence.oss.hunter import run_hunter, HunterConfig, HunterResult
from src.intelligence.oss.queries import generate_queries, SearchQuery
from src.intelligence.oss.filters import FilterConfig, filter_opportunities, FilterResult
from src.intelligence.oss.scorer import ScoringConfig, score_opportunity, sort_by_score
from src.intelligence.oss.dedup import DedupConfig, deduplicate, DedupResult

__all__ = [
    "Opportunity",
    "ScoreBreakdown",
    "run_hunter",
    "HunterConfig",
    "HunterResult",
    "generate_queries",
    "SearchQuery",
    "FilterConfig",
    "filter_opportunities",
    "FilterResult",
    "ScoringConfig",
    "score_opportunity",
    "sort_by_score",
    "DedupConfig",
    "deduplicate",
    "DedupResult",
]
