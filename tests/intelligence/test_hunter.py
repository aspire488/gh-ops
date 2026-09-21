"""Tests for OSS intelligence hunter orchestrator."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.intelligence.oss.hunter import (
    HunterConfig,
    HunterResult,
    run_hunter,
    _fetch_repo_metadata,
)
from src.intelligence.oss.models import Opportunity
from src.github.models import Issue


def _make_issue_data(**kwargs) -> dict:
    """Create mock issue API response data."""
    defaults = {
        "id": 100,
        "number": 1,
        "title": "Test Issue",
        "state": "open",
        "html_url": "https://github.com/owner/repo/issues/1",
        "repository_url": "https://api.github.com/repos/owner/repo",
        "user": {"login": "contributor"},
        "labels": [{"name": "bug"}],
        "comments": 2,
        "created_at": "2025-06-01T10:00:00Z",
        "updated_at": "2025-06-10T12:00:00Z",
    }
    defaults.update(kwargs)
    return defaults


def _make_issue_model(**kwargs) -> Issue:
    """Create an Issue model from API data."""
    data = _make_issue_data(**kwargs)
    return Issue.from_api(data)


class TestHunterConfig:
    """Tests for HunterConfig."""

    def test_defaults(self):
        config = HunterConfig()
        assert config.enabled is True
        assert config.max_results == 10
        assert config.max_queries == 20

    def test_from_dict(self):
        data = {
            "oss_hunter": {
                "enabled": False,
                "max_results": 5,
                "filters": {"min_repo_stars": 50},
            }
        }
        config = HunterConfig.from_dict(data)
        assert config.enabled is False
        assert config.max_results == 5
        assert config.filters.min_repo_stars == 50

    def test_frozen(self):
        config = HunterConfig()
        try:
            config.max_results = 20  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestHunterResult:
    """Tests for HunterResult."""

    def test_empty_result(self):
        result = HunterResult(opportunities=[])
        assert result.queries_run == 0
        assert result.total_issues_found == 0

    def test_to_dict(self):
        opp = Opportunity(
            repo_full_name="a/b",
            issue_number=1,
            issue_id=100,
            title="Test",
            html_url="u",
            state="open",
            user="u",
        )
        result = HunterResult(
            opportunities=[opp],
            queries_run=5,
            total_issues_found=10,
        )
        d = result.to_dict()
        assert d["queries_run"] == 5
        assert d["total_issues_found"] == 10
        assert len(d["opportunities"]) == 1

    def test_frozen(self):
        result = HunterResult(opportunities=[])
        try:
            result.queries_run = 5  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestFetchRepoMetadata:
    """Tests for _fetch_repo_metadata()."""

    def test_successful_fetch(self):
        client = MagicMock()
        client.get.return_value = {
            "stargazers_count": 500,
            "forks_count": 50,
            "open_issues_count": 20,
            "language": "python",
            "description": "A test repo",
        }
        result = _fetch_repo_metadata(client, "owner/repo")
        assert result["stars"] == 500
        assert result["forks"] == 50
        assert result["language"] == "python"

    def test_failed_fetch_returns_empty(self):
        client = MagicMock()
        client.get.side_effect = Exception("API error")
        result = _fetch_repo_metadata(client, "owner/repo")
        assert result == {}


class TestRunHunter:
    """Tests for run_hunter() with mocked client."""

    def test_disabled_config(self):
        client = MagicMock()
        config = HunterConfig(enabled=False)
        result = run_hunter(client, config)
        assert result.opportunities == []
        assert "OSS hunter disabled" in result.errors

    def test_empty_queries_result(self):
        client = MagicMock()
        config = HunterConfig(custom_queries=[])
        # Mock search to return empty
        with patch("src.intelligence.oss.hunter.search_issues", return_value=[]):
            result = run_hunter(client, config)
            assert result.queries_run >= 0

    def test_successful_hunt(self):
        client = MagicMock()

        # Mock search results
        issue1 = _make_issue_model(number=1, labels=[{"name": "bug"}])
        issue2 = _make_issue_model(number=2, labels=[{"name": "good first issue"}])

        with patch("src.intelligence.oss.hunter.search_issues") as mock_search:
            mock_search.return_value = [issue1, issue2]

            # Mock repo metadata
            client.get.return_value = {
                "stargazers_count": 100,
                "forks_count": 10,
                "open_issues_count": 5,
                "language": "python",
                "description": "Test",
            }

            config = HunterConfig(max_results=5, max_queries=2)
            result = run_hunter(client, config)

            assert result.queries_run > 0
            assert result.total_issues_found > 0

    def test_partial_query_failure(self):
        client = MagicMock()

        with patch("src.intelligence.oss.hunter.search_issues") as mock_search:
            mock_search.side_effect = Exception("API error")

            config = HunterConfig(max_queries=1)
            result = run_hunter(client, config)

            assert result.queries_failed > 0
            assert len(result.errors) > 0

    def test_max_results_respected(self):
        client = MagicMock()

        # Create many issues
        issues = [_make_issue_model(number=i) for i in range(20)]

        with patch("src.intelligence.oss.hunter.search_issues") as mock_search:
            mock_search.return_value = issues

            client.get.return_value = {
                "stargazers_count": 100,
                "forks_count": 10,
                "open_issues_count": 5,
                "language": "python",
                "description": "Test",
            }

            config = HunterConfig(max_results=5, max_queries=1)
            result = run_hunter(client, config)

            assert len(result.opportunities) <= 5

    def test_filters_applied(self):
        client = MagicMock()

        # Create issues with different repos
        issue_low_stars = _make_issue_model(
            number=1,
            repository_url="https://api.github.com/repos/low/stars",
        )
        issue_good = _make_issue_model(
            number=2,
            repository_url="https://api.github.com/repos/good/repo",
        )

        with patch("src.intelligence.oss.hunter.search_issues") as mock_search:
            mock_search.return_value = [issue_low_stars, issue_good]

            def mock_get(endpoint, **kwargs):
                if "low/stars" in endpoint:
                    return {"stargazers_count": 1, "forks_count": 0, "open_issues_count": 0}
                return {"stargazers_count": 100, "forks_count": 10, "open_issues_count": 5}

            client.get.side_effect = mock_get

            from src.intelligence.oss.filters import FilterConfig
            config = HunterConfig(
                max_queries=1,
                filters=FilterConfig(min_repo_stars=50),
            )
            result = run_hunter(client, config)

            # Low-star repo should be filtered
            assert result.total_filtered_out > 0


# Need pytest
import pytest
