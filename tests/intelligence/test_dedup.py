"""Tests for OSS intelligence deduplication."""
from __future__ import annotations

from src.intelligence.oss.models import Opportunity
from src.intelligence.oss.dedup import (
    DedupConfig,
    DedupResult,
    deduplicate,
    is_duplicate,
)


def _make_opp(**kwargs) -> Opportunity:
    """Create a test opportunity with sensible defaults."""
    defaults = {
        "repo_full_name": "owner/repo",
        "issue_number": 1,
        "issue_id": 100,
        "title": "Test Issue",
        "html_url": "https://github.com/owner/repo/issues/1",
        "state": "open",
        "user": "contributor",
        "labels": (),
        "score": 0.5,
    }
    defaults.update(kwargs)
    return Opportunity(**defaults)


class TestDedupConfig:
    """Tests for DedupConfig."""

    def test_defaults(self):
        config = DedupConfig()
        assert config.enabled is True
        assert config.merge_source_queries is True

    def test_from_dict(self):
        config = DedupConfig.from_dict({"enabled": False})
        assert config.enabled is False

    def test_frozen(self):
        config = DedupConfig()
        try:
            config.enabled = False  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestDeduplicate:
    """Tests for deduplicate()."""

    def test_empty_input(self):
        result = deduplicate([])
        assert result.unique == []
        assert result.duplicates_found == 0
        assert result.original_count == 0

    def test_no_duplicates(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=2),
            _make_opp(repo_full_name="c/d", issue_number=1),
        ]
        result = deduplicate(opps)
        assert len(result.unique) == 3
        assert result.duplicates_found == 0

    def test_removes_duplicates(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1, score=0.3),
            _make_opp(repo_full_name="a/b", issue_number=1, score=0.7),
        ]
        result = deduplicate(opps)
        assert len(result.unique) == 1
        assert result.duplicates_found == 1

    def test_keeps_higher_score(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1, score=0.3),
            _make_opp(repo_full_name="a/b", issue_number=1, score=0.7),
        ]
        result = deduplicate(opps)
        assert result.unique[0].score == 0.7

    def test_merges_source_queries(self):
        opps = [
            _make_opp(
                repo_full_name="a/b",
                issue_number=1,
                source_query="query1",
                source_queries=(),
            ),
            _make_opp(
                repo_full_name="a/b",
                issue_number=1,
                source_query="query2",
                source_queries=(),
            ),
        ]
        result = deduplicate(opps)
        assert len(result.unique) == 1
        merged = set(result.unique[0].source_queries)
        assert "query1" in merged
        assert "query2" in merged

    def test_idempotent(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=1),
        ]
        result1 = deduplicate(opps)
        result2 = deduplicate(result1.unique)
        # Same unique count after second pass (no more duplicates)
        assert len(result1.unique) == len(result2.unique)
        # Second pass finds 0 new duplicates (already deduplicated)
        assert result2.duplicates_found == 0

    def test_disabled_dedup(self):
        config = DedupConfig(enabled=False)
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=1),
        ]
        result = deduplicate(opps, config)
        assert len(result.unique) == 2
        assert result.duplicates_found == 0

    def test_preserves_insertion_order(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=3),
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=2),
        ]
        result = deduplicate(opps)
        numbers = [o.issue_number for o in result.unique]
        assert numbers == [3, 1, 2]

    def test_dedup_rate(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="a/b", issue_number=1),
            _make_opp(repo_full_name="c/d", issue_number=1),
        ]
        result = deduplicate(opps)
        assert result.dedup_rate == pytest.approx(2 / 4)


class TestIsDuplicate:
    """Tests for is_duplicate() streaming dedup."""

    def test_not_duplicate(self):
        opp = _make_opp(repo_full_name="a/b", issue_number=1)
        assert is_duplicate(opp, set()) is False

    def test_is_duplicate(self):
        opp = _make_opp(repo_full_name="a/b", issue_number=1)
        seen = {("a/b", 1)}
        assert is_duplicate(opp, seen) is True

    def test_different_issue_not_duplicate(self):
        opp = _make_opp(repo_full_name="a/b", issue_number=2)
        seen = {("a/b", 1)}
        assert is_duplicate(opp, seen) is False

    def test_different_repo_not_duplicate(self):
        opp = _make_opp(repo_full_name="c/d", issue_number=1)
        seen = {("a/b", 1)}
        assert is_duplicate(opp, seen) is False


class TestDedupResult:
    """Tests for DedupResult."""

    def test_empty_result(self):
        result = DedupResult()
        assert result.dedup_rate == 0.0

    def test_to_dict(self):
        result = DedupResult(
            unique=[_make_opp()],
            duplicates_found=2,
            original_count=5,
        )
        d = result.to_dict()
        assert d["duplicates_found"] == 2
        assert d["original_count"] == 5
        assert len(d["unique"]) == 1


# Need pytest for approx
import pytest
