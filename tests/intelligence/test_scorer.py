"""Tests for OSS intelligence scoring engine."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from src.intelligence.oss.models import Opportunity, ScoreBreakdown
from src.intelligence.oss.scorer import (
    ScoringConfig,
    score_opportunity,
    sort_by_score,
    _normalize,
    _score_repo_activity,
    _score_issue_freshness,
    _score_label_signal,
    _score_discussion_activity,
    _score_assignment_status,
    _score_scope_signal,
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
        "repo_stars": 100,
        "repo_forks": 20,
        "repo_open_issues": 10,
        "comments": 0,
        "created_at": datetime(2025, 6, 1, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return Opportunity(**defaults)


class TestNormalize:
    """Tests for _normalize()."""

    def test_midpoint(self):
        assert _normalize(5, 0, 10) == 0.5

    def test_at_min(self):
        assert _normalize(0, 0, 10) == 0.0

    def test_at_max(self):
        assert _normalize(10, 0, 10) == 1.0

    def test_above_max(self):
        assert _normalize(15, 0, 10) == 1.0

    def test_below_min(self):
        assert _normalize(-5, 0, 10) == 0.0

    def test_equal_bounds(self):
        assert _normalize(5, 5, 5) == 0.0


class TestScoringConfig:
    """Tests for ScoringConfig."""

    def test_defaults(self):
        config = ScoringConfig()
        assert sum(config.weights.values()) == pytest.approx(1.0)
        assert config.tie_breaking_method == "issue_number_asc"

    def test_from_dict(self):
        data = {
            "scoring": {
                "weights": {"repo_activity": 0.5, "other": 0.5},
                "tie_breaking": {"method": "repo_stars_desc"},
            }
        }
        config = ScoringConfig.from_dict(data)
        assert config.weights["repo_activity"] == 0.5
        assert config.tie_breaking_method == "repo_stars_desc"

    def test_frozen(self):
        config = ScoringConfig()
        try:
            config.weights = {}  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestScoreOpportunity:
    """Tests for score_opportunity()."""

    def test_score_is_bounded(self):
        opp = _make_opp()
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        assert 0.0 <= scored.score <= 1.0

    def test_score_is_deterministic(self):
        opp = _make_opp()
        config = ScoringConfig()
        scored1 = score_opportunity(opp, config)
        scored2 = score_opportunity(opp, config)
        assert scored1.score == scored2.score

    def test_higher_stars_higher_score(self):
        low_stars = _make_opp(issue_number=1, repo_stars=10)
        high_stars = _make_opp(issue_number=2, repo_stars=1000)
        config = ScoringConfig()
        score_low = score_opportunity(low_stars, config).score
        score_high = score_opportunity(high_stars, config).score
        assert score_high > score_low

    def test_fresher_issue_higher_score(self):
        now = datetime(2025, 6, 15, tzinfo=timezone.utc)
        fresh = _make_opp(
            issue_number=1,
            created_at=now - timedelta(days=2),
        )
        old = _make_opp(
            issue_number=2,
            created_at=now - timedelta(days=60),
        )
        config = ScoringConfig()
        score_fresh = score_opportunity(fresh, config, now=now).score
        score_old = score_opportunity(old, config, now=now).score
        assert score_fresh > score_old

    def test_good_first_issue_label_highest(self):
        gfi = _make_opp(issue_number=1, labels=("good first issue",))
        hw = _make_opp(issue_number=2, labels=("help wanted",))
        config = ScoringConfig()
        score_gfi = score_opportunity(gfi, config).score
        score_hw = score_opportunity(hw, config).score
        assert score_gfi > score_hw

    def test_unassigned_higher_score(self):
        unassigned = _make_opp(issue_number=1, assignee=None, assignees=())
        assigned = _make_opp(issue_number=2, assignee="someone", assignees=("someone",))
        config = ScoringConfig()
        score_unassigned = score_opportunity(unassigned, config).score
        score_assigned = score_opportunity(assigned, config).score
        assert score_unassigned > score_assigned

    def test_breakdown_matches_weights(self):
        opp = _make_opp(labels=("good first issue",), comments=5)
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        breakdown = scored.score_breakdown

        # Verify weighted sum matches total
        expected = (
            breakdown.repo_activity * config.weights["repo_activity"]
            + breakdown.issue_freshness * config.weights["issue_freshness"]
            + breakdown.label_signal * config.weights["label_signal"]
            + breakdown.discussion_activity * config.weights["discussion_activity"]
            + breakdown.assignment_status * config.weights["assignment_status"]
            + breakdown.maintainer_activity * config.weights["maintainer_activity"]
            + breakdown.scope_signal * config.weights["scope_signal"]
        )
        assert scored.score == pytest.approx(expected, abs=0.01)

    def test_breakdown_is_populated(self):
        opp = _make_opp(labels=("bug",), comments=3)
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        breakdown = scored.score_breakdown
        assert breakdown.label_signal > 0
        assert breakdown.discussion_activity > 0

    def test_no_labels_zero_label_signal(self):
        opp = _make_opp(labels=())
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        assert scored.score_breakdown.label_signal == 0.0


class TestSortByScore:
    """Tests for sort_by_score()."""

    def test_sorts_by_score_descending(self):
        opps = [
            _make_opp(issue_number=1, score=0.3),
            _make_opp(issue_number=2, score=0.9),
            _make_opp(issue_number=3, score=0.6),
        ]
        sorted_opps = sort_by_score(opps)
        scores = [o.score for o in sorted_opps]
        assert scores == [0.9, 0.6, 0.3]

    def test_tie_breaking_by_issue_number_asc(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=5, score=0.5),
            _make_opp(repo_full_name="a/b", issue_number=2, score=0.5),
            _make_opp(repo_full_name="a/b", issue_number=8, score=0.5),
        ]
        sorted_opps = sort_by_score(opps, method="issue_number_asc")
        numbers = [o.issue_number for o in sorted_opps]
        assert numbers == [2, 5, 8]

    def test_tie_breaking_by_repo_stars_desc(self):
        opps = [
            _make_opp(repo_full_name="a/b", issue_number=1, score=0.5, repo_stars=100),
            _make_opp(repo_full_name="a/b", issue_number=2, score=0.5, repo_stars=500),
            _make_opp(repo_full_name="a/b", issue_number=3, score=0.5, repo_stars=200),
        ]
        sorted_opps = sort_by_score(opps, method="repo_stars_desc")
        stars = [o.repo_stars for o in sorted_opps]
        assert stars == [500, 200, 100]

    def test_does_not_mutate_input(self):
        opps = [
            _make_opp(issue_number=3, score=0.3),
            _make_opp(issue_number=1, score=0.9),
        ]
        original_order = [o.issue_number for o in opps]
        sort_by_score(opps)
        assert [o.issue_number for o in opps] == original_order

    def test_empty_list(self):
        assert sort_by_score([]) == []


class TestScoringEdgeCases:
    """Edge case tests for scoring."""

    def test_no_created_at(self):
        opp = _make_opp(created_at=None)
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        assert scored.score_breakdown.issue_freshness == 0.0

    def test_empty_body(self):
        opp = _make_opp(body=None)
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        # Should still have a scope signal
        assert 0.0 <= scored.score_breakdown.scope_signal <= 1.0

    def test_many_comments(self):
        opp = _make_opp(comments=50)
        config = ScoringConfig()
        scored = score_opportunity(opp, config)
        # High comment count should still be reasonable
        assert 0.0 <= scored.score_breakdown.discussion_activity <= 1.0


# Need pytest for approx
import pytest
