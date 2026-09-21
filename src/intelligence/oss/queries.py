"""Multi-query strategy for OSS opportunity discovery.

Generates diverse search queries beyond simple "good first issue" / "help wanted"
to maximize discovery of contribution opportunities.

No external dependencies in runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Default query categories with their search patterns
DEFAULT_QUERY_CATEGORIES: dict[str, list[dict[str, Any]]] = {
    "starter_labels": [
        {"q": 'label:"good first issue" is:open', "description": "GitHub starter label"},
        {"q": 'label:"help wanted" is:open', "description": "Community help wanted"},
        {"q": 'label:"beginner" is:open', "description": "Beginner-friendly"},
        {"q": 'label:"easy" is:open', "description": "Easy difficulty"},
        {"q": 'label:"starter" is:open', "description": "Starter issues"},
        {"q": 'label:"newbie" is:open', "description": "Newbie-friendly"},
    ],
    "language_specific": [
        {"q": 'label:"good first issue" language:python is:open', "description": "Python starter"},
        {"q": 'label:"good first issue" language:typescript is:open', "description": "TypeScript starter"},
        {"q": 'label:"good first issue" language:javascript is:open', "description": "JavaScript starter"},
        {"q": 'label:"good first issue" language:rust is:open', "description": "Rust starter"},
        {"q": 'label:"good first issue" language:go is:open', "description": "Go starter"},
    ],
    "bug_bounty": [
        {"q": 'label:"bug" label:"help wanted" is:open', "description": "Bug with help wanted"},
        {"q": 'label:"bug" label:"good first issue" is:open', "description": "Bug as starter"},
        {"q": 'label:"bug" is:open no:assignee', "description": "Unassigned bugs"},
    ],
    "docs_contrib": [
        {"q": 'label:"documentation" is:open', "description": "Documentation issues"},
        {"q": 'label:"docs" is:open', "description": "Docs label"},
        {"q": 'label:"good first issue" label:"documentation" is:open', "description": "Doc starter issues"},
    ],
    "unclaimed": [
        {"q": 'is:issue is:open no:assignee label:"help wanted"', "description": "Unclaimed help wanted"},
        {"q": 'is:issue is:open no:assignee label:"good first issue"', "description": "Unclaimed starter"},
        {"q": 'is:issue is:open comments:0 label:"good first issue"', "description": "No-discussion starters"},
    ],
    "high_engagement": [
        {"q": 'label:"good first issue" is:open reactions:>=3', "description": "Popular starters (3+ reactions)"},
        {"q": 'label:"help wanted" is:open reactions:>=5', "description": "Popular help wanted (5+ reactions)"},
    ],
    "recent_activity": [
        {"q": 'label:"good first issue" is:open pushed:>=2025-01-01', "description": "Active repos with starters"},
        {"q": 'label:"help wanted" is:open pushed:>=2025-01-01', "description": "Active repos with help wanted"},
    ],
}


@dataclass(frozen=True)
class SearchQuery:
    """A single search query with metadata."""

    query: str
    category: str
    description: str
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "category": self.category,
            "description": self.description,
            "priority": self.priority,
        }


def generate_queries(
    config: dict[str, Any] | None = None,
    languages: list[str] | None = None,
    categories: list[str] | None = None,
    include_custom: list[str] | None = None,
) -> list[SearchQuery]:
    """Generate search queries from configuration.

    Args:
        config: OSS hunter configuration dict (optional, uses defaults if None)
        languages: Override language list. If None, uses config or defaults.
        categories: Limit to specific categories. If None, uses all.
        include_custom: Additional custom query strings to include.

    Returns:
        List of SearchQuery objects, sorted by priority (desc), then category.
    """
    config = config or {}
    queries_config = config.get("queries", [])

    result: list[SearchQuery] = []

    # Build from config queries if present
    if queries_config:
        for i, q in enumerate(queries_config):
            if isinstance(q, str):
                result.append(SearchQuery(
                    query=q,
                    category="config",
                    description=f"Config query {i + 1}",
                    priority=100 - i,
                ))
            elif isinstance(q, dict):
                result.append(SearchQuery(
                    query=q.get("q", q.get("query", "")),
                    category=q.get("category", "config"),
                    description=q.get("description", ""),
                    priority=q.get("priority", 50),
                ))

    # Build from default categories
    target_categories = categories or list(DEFAULT_QUERY_CATEGORIES.keys())

    for cat_name in target_categories:
        if cat_name not in DEFAULT_QUERY_CATEGORIES:
            continue

        cat_queries = DEFAULT_QUERY_CATEGORIES[cat_name]

        for i, qdef in enumerate(cat_queries):
            query_str = qdef["q"]

            # Apply language override if provided
            if languages and "language:" in query_str:
                # Replace existing language: qualifier
                import re
                query_str = re.sub(r"language:\S+", "", query_str).strip()
                # Add first language (we'll generate per-language queries separately)
                query_str = f"{query_str} language:{languages[0]}"

            # Skip if already covered by config
            if any(sq.query == query_str for sq in result):
                continue

            result.append(SearchQuery(
                query=query_str,
                category=cat_name,
                description=qdef.get("description", ""),
                priority=80 - i,
            ))

    # Generate per-language variants if languages provided
    if languages:
        lang_queries: list[SearchQuery] = []
        base_labels = [
            'label:"good first issue" is:open',
            'label:"help wanted" is:open',
        ]
        for lang in languages:
            for base_q in base_labels:
                q_str = f"{base_q} language:{lang}"
                if not any(sq.query == q_str for sq in result):
                    lang_queries.append(SearchQuery(
                        query=q_str,
                        category="language_specific",
                        description=f"{lang} starter",
                        priority=70,
                    ))
        result.extend(lang_queries)

    # Add custom queries
    if include_custom:
        for custom_q in include_custom:
            if not any(sq.query == custom_q for sq in result):
                result.append(SearchQuery(
                    query=custom_q,
                    category="custom",
                    description="Custom query",
                    priority=60,
                ))

    # Stable sort: priority desc, then category, then query
    result.sort(key=lambda sq: (-sq.priority, sq.category, sq.query))

    return result


def deduplicate_queries(queries: list[SearchQuery]) -> list[SearchQuery]:
    """Remove duplicate queries while preserving first occurrence.

    Two queries are duplicates if they have the same query string,
    regardless of category or priority.
    """
    seen: set[str] = set()
    result: list[SearchQuery] = []

    for q in queries:
        normalized = q.query.strip().lower()
        if normalized not in seen:
            seen.add(normalized)
            result.append(q)

    return result
