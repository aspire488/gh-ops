"""Tests for OSS intelligence models."""
from __future__ import annotations

from datetime import datetime, timezone
from src.intelligence.oss.models import Opportunity, ScoreBreakdown


class TestOpportunity:
    """Tests for Opportunity dataclass."""

    def test_identity_returns_tuple(self):
        opp = Opportunity(
            repo_full_name="owner/repo",
            issue_number=42,
            issue_id=123,
            title="Fix bug",
            html_url="https://github.com/owner/repo/issues/42",
            state="open",
            user="contributor",
        )
        assert opp.identity() == ("owner/repo", 42)

    def test_identity_is_deterministic(self):
        opp = Opportunity(
            repo_full_name="a/b",
            issue_number=1,
            issue_id=1,
            title="t",
            html_url="u",
            state="open",
            user="u",
        )
        assert opp.identity() == opp.identity()

    def test_to_dict_roundtrip(self):
        opp = Opportunity(
            repo_full_name="owner/repo",
            issue_number=1,
            issue_id=100,
            title="Test Issue",
            html_url="https://github.com/owner/repo/issues/1",
            state="open",
            user="testuser",
            labels=("bug", "help wanted"),
            score=0.75,
        )
        d = opp.to_dict()
        assert d["repo_full_name"] == "owner/repo"
        assert d["issue_number"] == 1
        assert d["labels"] == ["bug", "help wanted"]
        assert d["score"] == 0.75

    def test_from_search_result_parses_labels(self):
        data = {
            "id": 100,
            "number": 5,
            "title": "Fix typo",
            "state": "open",
            "html_url": "https://github.com/o/r/issues/5",
            "repository_url": "https://api.github.com/repos/o/r",
            "user": {"login": "alice"},
            "labels": [{"name": "bug"}, {"name": "good first issue"}],
            "comments": 3,
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-20T12:00:00Z",
        }
        opp = Opportunity.from_search_result(data, source_query="test")
        assert opp.repo_full_name == "o/r"
        assert opp.issue_number == 5
        assert opp.user == "alice"
        assert opp.labels == ("bug", "good first issue")
        assert opp.comments == 3
        assert opp.source_query == "test"

    def test_from_search_result_handles_missing_fields(self):
        data = {
            "id": 1,
            "number": 1,
            "title": "Minimal",
            "state": "open",
            "html_url": "",
            "repository_url": "",
            "user": None,
            "labels": [],
        }
        opp = Opportunity.from_search_result(data)
        assert opp.user == ""
        assert opp.labels == ()
        assert opp.repo_full_name == ""

    def test_frozen_dataclass(self):
        opp = Opportunity(
            repo_full_name="o/r",
            issue_number=1,
            issue_id=1,
            title="t",
            html_url="u",
            state="open",
            user="u",
        )
        try:
            opp.issue_number = 2  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_score_breakdown_default(self):
        opp = Opportunity(
            repo_full_name="o/r",
            issue_number=1,
            issue_id=1,
            title="t",
            html_url="u",
            state="open",
            user="u",
        )
        assert opp.score_breakdown.repo_activity == 0.0
        assert opp.score_breakdown.to_dict()["label_signal"] == 0.0


class TestScoreBreakdown:
    """Tests for ScoreBreakdown."""

    def test_to_dict(self):
        breakdown = ScoreBreakdown(
            repo_activity=0.8,
            issue_freshness=0.6,
            label_signal=1.0,
        )
        d = breakdown.to_dict()
        assert d["repo_activity"] == 0.8
        assert d["issue_freshness"] == 0.6
        assert d["label_signal"] == 1.0
        assert d["discussion_activity"] == 0.0

    def test_frozen(self):
        breakdown = ScoreBreakdown(repo_activity=0.5)
        try:
            breakdown.repo_activity = 0.6  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass
