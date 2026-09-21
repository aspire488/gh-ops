"""Tests for OSS intelligence query generation."""
from __future__ import annotations

from src.intelligence.oss.queries import (
    generate_queries,
    deduplicate_queries,
    SearchQuery,
    DEFAULT_QUERY_CATEGORIES,
)


class TestGenerateQueries:
    """Tests for generate_queries()."""

    def test_default_queries_generated(self):
        queries = generate_queries()
        assert len(queries) > 0
        assert all(isinstance(q, SearchQuery) for q in queries)

    def test_queries_have_required_fields(self):
        queries = generate_queries()
        for q in queries:
            assert q.query, f"Query should not be empty: {q}"
            assert q.category, f"Category should not be empty: {q}"
            assert q.description, f"Description should not be empty: {q}"

    def test_queries_are_sorted_by_priority(self):
        queries = generate_queries()
        priorities = [q.priority for q in queries]
        # Should be non-increasing (descending)
        for i in range(len(priorities) - 1):
            assert priorities[i] >= priorities[i + 1], (
                f"Queries not sorted by priority: {priorities}"
            )

    def test_config_queries_included(self):
        config = {
            "queries": [
                "label:\"bug\" is:open",
                {"q": "label:\"feature\" is:open", "category": "feature", "description": "Features"},
            ]
        }
        queries = generate_queries(config=config)
        query_strings = [q.query for q in queries]
        assert 'label:"bug" is:open' in query_strings
        assert 'label:"feature" is:open' in query_strings

    def test_config_queries_take_priority(self):
        config = {
            "queries": ["custom query"]
        }
        queries = generate_queries(config=config)
        # Custom query should have highest priority
        assert queries[0].query == "custom query"
        assert queries[0].priority == 100

    def test_language_filter(self):
        queries = generate_queries(languages=["rust"])
        # Should have rust-specific queries
        rust_queries = [q for q in queries if "language:rust" in q.query]
        assert len(rust_queries) > 0

    def test_category_filter(self):
        queries = generate_queries(categories=["starter_labels"])
        categories = {q.category for q in queries}
        assert "starter_labels" in categories
        # Other categories should not be present (unless from config)
        assert "bug_bounty" not in categories

    def test_custom_queries_added(self):
        custom = ["is:issue is:open label:\"mentor available\""]
        queries = generate_queries(include_custom=custom)
        query_strings = [q.query for q in queries]
        assert custom[0] in query_strings

    def test_empty_config(self):
        queries = generate_queries(config={})
        assert len(queries) > 0


class TestDeduplicateQueries:
    """Tests for deduplicate_queries()."""

    def test_no_duplicates(self):
        queries = [
            SearchQuery(query="a", category="c1", description="d1"),
            SearchQuery(query="b", category="c2", description="d2"),
        ]
        result = deduplicate_queries(queries)
        assert len(result) == 2

    def test_removes_duplicates(self):
        queries = [
            SearchQuery(query="label:\"bug\" is:open", category="c1", description="d1"),
            SearchQuery(query="label:\"bug\" is:open", category="c2", description="d2"),
        ]
        result = deduplicate_queries(queries)
        assert len(result) == 1

    def test_case_insensitive_dedup(self):
        queries = [
            SearchQuery(query="LABEL:\"bug\" is:open", category="c1", description="d1"),
            SearchQuery(query="label:\"bug\" is:open", category="c2", description="d2"),
        ]
        result = deduplicate_queries(queries)
        assert len(result) == 1

    def test_preserves_first_occurrence(self):
        queries = [
            SearchQuery(query="a", category="c1", description="d1", priority=10),
            SearchQuery(query="a", category="c2", description="d2", priority=20),
        ]
        result = deduplicate_queries(queries)
        assert len(result) == 1
        assert result[0].category == "c1"

    def test_empty_input(self):
        result = deduplicate_queries([])
        assert result == []


class TestSearchQuery:
    """Tests for SearchQuery dataclass."""

    def test_to_dict(self):
        q = SearchQuery(
            query="label:\"bug\" is:open",
            category="bug_bounty",
            description="Bugs with help wanted",
            priority=50,
        )
        d = q.to_dict()
        assert d["query"] == 'label:"bug" is:open'
        assert d["category"] == "bug_bounty"
        assert d["priority"] == 50

    def test_frozen(self):
        q = SearchQuery(query="a", category="b", description="c")
        try:
            q.query = "x"  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass
