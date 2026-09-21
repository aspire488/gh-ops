"""Tests for OSS intelligence filters."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from src.intelligence.oss.models import Opportunity
from src.intelligence.oss.filters import (
    FilterConfig,
    FilterResult,
    filter_opportunities,
    compose_filters,
    _is_bot,
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
    }
    defaults.update(kwargs)
    return Opportunity(**defaults)


class TestIsBot:
    """Tests for _is_bot()."""

    def test_dependabot(self):
        assert _is_bot("dependabot[bot]") is True

    def test_renovate(self):
        assert _is_bot("renovate") is True

    def test_bot_suffix(self):
        assert _is_bot("my-bot") is True

    def test_bot_prefix(self):
        assert _is_bot("bot-something") is True

    def test_human(self):
        assert _is_bot("alice") is False

    def test_case_insensitive(self):
        assert _is_bot("Dependabot[Bot]") is True


class TestFilterConfig:
    """Tests for FilterConfig."""

    def test_defaults(self):
        config = FilterConfig()
        assert "wontfix" in config.exclude_labels
        assert config.exclude_bot_authors is True
        assert config.exclude_locked is True
        assert config.min_repo_stars == 0

    def test_from_dict(self):
        data = {
            "exclude_labels": ["bug", "wip"],
            "min_repo_stars": 50,
            "max_issue_age_days": 30,
        }
        config = FilterConfig.from_dict(data)
        assert config.exclude_labels == ("bug", "wip")
        assert config.min_repo_stars == 50
        assert config.max_issue_age_days == 30

    def test_frozen(self):
        config = FilterConfig()
        try:
            config.min_repo_stars = 100  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestFilterOpportunities:
    """Tests for filter_opportunities()."""

    def test_empty_input(self):
        result = filter_opportunities([], FilterConfig())
        assert result.passed == []
        assert result.total_input == 0
        assert result.pass_rate == 0.0

    def test_all_pass_with_default_config(self):
        opps = [_make_opp() for _ in range(5)]
        result = filter_opportunities(opps, FilterConfig())
        assert len(result.passed) == 5
        assert result.total_excluded == 0

    def test_excluded_labels(self):
        config = FilterConfig(exclude_labels=("wontfix", "duplicate"))
        opps = [
            _make_opp(issue_number=1, labels=("bug",)),
            _make_opp(issue_number=2, labels=("wontfix",)),
            _make_opp(issue_number=3, labels=("duplicate", "bug")),
        ]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1
        assert result.passed[0].issue_number == 1
        assert result.excluded_by_rule["excluded_label"] == 2

    def test_bot_author_excluded(self):
        config = FilterConfig(exclude_bot_authors=True)
        opps = [
            _make_opp(issue_number=1, user="alice"),
            _make_opp(issue_number=2, user="dependabot[bot]"),
        ]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1
        assert result.passed[0].user == "alice"

    def test_bot_author_not_excluded_when_disabled(self):
        config = FilterConfig(exclude_bot_authors=False)
        opps = [_make_opp(user="dependabot[bot]")]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1

    def test_min_repo_stars(self):
        config = FilterConfig(min_repo_stars=100)
        opps = [
            _make_opp(issue_number=1, repo_stars=200),
            _make_opp(issue_number=2, repo_stars=50),
        ]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1
        assert result.passed[0].repo_stars == 200

    def test_max_issue_age(self):
        config = FilterConfig(max_issue_age_days=30)
        now = datetime(2025, 6, 15, tzinfo=timezone.utc)
        opps = [
            _make_opp(
                issue_number=1,
                created_at=now - timedelta(days=10),
            ),
            _make_opp(
                issue_number=2,
                created_at=now - timedelta(days=60),
            ),
        ]
        result = filter_opportunities(opps, config, now=now)
        assert len(result.passed) == 1
        assert result.passed[0].issue_number == 1

    def test_excluded_repos(self):
        config = FilterConfig(exclude_repos=("spam/repo",))
        opps = [
            _make_opp(issue_number=1, repo_full_name="good/repo"),
            _make_opp(issue_number=2, repo_full_name="spam/repo"),
        ]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1
        assert result.passed[0].repo_full_name == "good/repo"

    def test_multiple_filters_compose(self):
        config = FilterConfig(
            exclude_labels=("wontfix",),
            min_repo_stars=50,
        )
        opps = [
            _make_opp(issue_number=1, labels=("bug",), repo_stars=100),  # Pass
            _make_opp(issue_number=2, labels=("wontfix",), repo_stars=100),  # Fail label
            _make_opp(issue_number=3, labels=("bug",), repo_stars=10),  # Fail stars
            _make_opp(issue_number=4, labels=("wontfix",), repo_stars=10),  # Fail both
        ]
        result = filter_opportunities(opps, config)
        assert len(result.passed) == 1
        assert result.passed[0].issue_number == 1

    def test_pass_rate(self):
        config = FilterConfig(min_repo_stars=100)
        opps = [
            _make_opp(repo_stars=200),
            _make_opp(repo_stars=50),
            _make_opp(repo_stars=300),
        ]
        result = filter_opportunities(opps, config)
        assert result.pass_rate == pytest.approx(2 / 3)

    def test_total_excluded(self):
        config = FilterConfig(
            exclude_labels=("wontfix",),
            min_repo_stars=100,
        )
        opps = [
            _make_opp(labels=("wontfix",), repo_stars=200),  # 1 exclusion
            _make_opp(labels=("bug",), repo_stars=50),  # 1 exclusion
        ]
        result = filter_opportunities(opps, config)
        assert result.total_excluded == 2


class TestComposeFilters:
    """Tests for compose_filters()."""

    def test_all_pass(self):
        f1 = lambda opp: opp.repo_stars > 50
        f2 = lambda opp: "bug" in opp.labels
        combined = compose_filters(f1, f2)

        opp = _make_opp(repo_stars=100, labels=("bug",))
        assert combined(opp) is True

    def test_one_fails(self):
        f1 = lambda opp: opp.repo_stars > 50
        f2 = lambda opp: "bug" in opp.labels
        combined = compose_filters(f1, f2)

        opp = _make_opp(repo_stars=100, labels=("feature",))
        assert combined(opp) is False

    def test_empty_composition(self):
        combined = compose_filters()
        opp = _make_opp()
        assert combined(opp) is True


# Need pytest for approx
import pytest
