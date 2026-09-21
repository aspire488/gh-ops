"""Tests for collectors — all mocked, no live GitHub contact."""
from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from src.github.client import GitHubClient
from src.github.auth import AuthConfig
from src.github.models import (
    Issue,
    PaginatedResult,
    PullRequest,
    Release,
    Repository,
    SecurityAlert,
    User,
    WorkflowRun,
)
from src.github.collectors import repos, issues, pulls, releases, workflows, security, user as user_collector


@pytest.fixture
def client():
    """Create a mocked GitHubClient."""
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.authenticated = True
    return mock_client


def _paginated(items, not_modified=False):
    """Create a PaginatedResult."""
    return PaginatedResult(
        items=tuple(items),
        total_count=len(items),
        not_modified=not_modified,
    )


class TestReposCollector:
    def test_list_user_repos(self, client):
        repo_data = {
            "id": 1, "full_name": "o/r", "owner": {"login": "o"},
            "name": "r",
        }
        client.get_paginated.return_value = _paginated([repo_data])
        result = repos.list_user_repos(client)
        assert len(result) == 1
        assert result[0].full_name == "o/r"

    def test_list_user_repos_not_modified(self, client):
        client.get_paginated.return_value = _paginated([], not_modified=True)
        result = repos.list_user_repos(client)
        assert result == []

    def test_list_org_repos(self, client):
        repo_data = {
            "id": 2, "full_name": "org/repo", "owner": {"login": "org"},
            "name": "repo",
        }
        client.get_paginated.return_value = _paginated([repo_data])
        result = repos.list_org_repos(client, "org")
        assert len(result) == 1

    def test_get_repo(self, client):
        repo_data = {
            "id": 3, "full_name": "o/r", "owner": {"login": "o"},
            "name": "r", "stargazers_count": 100,
        }
        client.get.return_value = repo_data
        result = repos.get_repo(client, "o", "r")
        assert result.stargazers_count == 100

    def test_list_repo_topics(self, client):
        client.get.return_value = {"names": ["python", "cli"]}
        result = repos.list_repo_topics(client, "o", "r")
        assert result == ["python", "cli"]


class TestIssuesCollector:
    def test_list_issues(self, client):
        issue_data = {
            "id": 1, "number": 10, "title": "Bug", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
            "labels": [{"name": "bug"}],
        }
        client.get_paginated.return_value = _paginated([issue_data])
        result = issues.list_issues(client, "o", "r")
        assert len(result) == 1
        assert result[0].labels == ("bug",)

    def test_filters_out_pull_requests(self, client):
        """Issues API returns PRs too; collector filters them."""
        issue_data = {
            "id": 1, "number": 10, "title": "Bug", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
        }
        pr_data = {
            "id": 2, "number": 11, "title": "PR", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
            "pull_request": {"url": "..."},
        }
        client.get_paginated.return_value = _paginated([issue_data, pr_data])
        result = issues.list_issues(client, "o", "r")
        assert len(result) == 1
        assert result[0].title == "Bug"

    def test_list_my_issues(self, client):
        issue_data = {
            "id": 1, "number": 5, "title": "My issue", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
        }
        client.get_paginated.return_value = _paginated([issue_data])
        result = issues.list_my_issues(client)
        assert len(result) == 1

    def test_get_issue(self, client):
        issue_data = {
            "id": 1, "number": 5, "title": "Issue", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
        }
        client.get.return_value = issue_data
        result = issues.get_issue(client, "o", "r", 5)
        assert result.number == 5

    def test_search_issues(self, client):
        issue_data = {
            "id": 1, "number": 1, "title": "Found", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r",
        }
        client.get_paginated.return_value = _paginated([issue_data])
        result = issues.search_issues(client, "is:issue is:open")
        assert len(result) == 1

    def test_invalid_repo_format(self):
        with pytest.raises(ValueError):
            issues.list_issues_for_repo(MagicMock(), "invalid")


class TestPullsCollector:
    def test_list_pulls(self, client):
        pr_data = {
            "id": 1, "number": 5, "title": "PR", "state": "open",
            "user": {"login": "dev"},
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "repository_url": "https://api.github.com/repos/o/r",
        }
        client.get_paginated.return_value = _paginated([pr_data])
        result = pulls.list_pulls(client, "o", "r")
        assert len(result) == 1
        assert result[0].head_branch == "feature"

    def test_get_pull(self, client):
        pr_data = {
            "id": 1, "number": 5, "title": "PR", "state": "open",
            "head": {"ref": "feat"},
            "base": {"ref": "main"},
            "repository_url": "https://api.github.com/repos/o/r",
        }
        client.get.return_value = pr_data
        result = pulls.get_pull(client, "o", "r", 5)
        assert result.head_branch == "feat"


class TestReleasesCollector:
    def test_list_releases(self, client):
        release_data = {
            "id": 1, "tag_name": "v1.0", "author": {"login": "m"},
            "assets": [{"id": 1}],
        }
        client.get_paginated.return_value = _paginated([release_data])
        result = releases.list_releases(client, "o", "r")
        assert len(result) == 1
        assert result[0].tag_name == "v1.0"

    def test_get_latest_release(self, client):
        release_data = {
            "id": 1, "tag_name": "v2.0", "author": {"login": "m"},
            "assets": [],
        }
        client.get.return_value = release_data
        result = releases.get_latest_release(client, "o", "r")
        assert result.tag_name == "v2.0"

    def test_get_release_by_tag(self, client):
        release_data = {
            "id": 1, "tag_name": "v1.0", "author": {"login": "m"},
            "assets": [],
        }
        client.get.return_value = release_data
        result = releases.get_release_by_tag(client, "o", "r", "v1.0")
        assert result.tag_name == "v1.0"


class TestWorkflowsCollector:
    def test_list_workflow_runs(self, client):
        run_data = {
            "id": 1, "name": "CI", "status": "completed",
            "conclusion": "success",
        }
        client.get_paginated.return_value = _paginated([run_data])
        result = workflows.list_workflow_runs(client, "o", "r")
        assert len(result) == 1
        assert result[0].is_success is True

    def test_get_workflow_run(self, client):
        run_data = {
            "id": 1, "name": "CI", "status": "completed",
            "conclusion": "failure",
        }
        client.get.return_value = run_data
        result = workflows.get_workflow_run(client, "o", "r", 1)
        assert result.is_failure is True


class TestSecurityCollector:
    def test_list_dependabot_alerts(self, client):
        alert_data = {
            "number": 42,
            "state": "open",
            "security_advisory": {"summary": "XSS", "severity": "high"},
            "security_vulnerability": {"package": {"name": "lodash"}},
        }
        client.get_paginated.return_value = _paginated([alert_data])
        result = security.list_dependabot_alerts(client, "o", "r")
        assert len(result) == 1
        assert result[0].severity == "high"

    def test_list_code_scanning_alerts(self, client):
        alert_data = {
            "number": 1,
            "state": "open",
            "rule": {"id": "js/sql-injection", "severity": "error", "description": "SQL injection"},
            "html_url": "https://github.com/o/r/security/code-scanning/1",
        }
        client.get_paginated.return_value = _paginated([alert_data])
        result = security.list_code_scanning_alerts(client, "o", "r")
        assert len(result) == 1
        assert result[0].severity == "error"


class TestUserCollector:
    def test_get_authenticated_user(self, client):
        user_data = {"id": 1, "login": "testuser", "public_repos": 10}
        client.get.return_value = user_data
        result = user_collector.get_authenticated_user(client)
        assert result.login == "testuser"
        assert result.public_repos == 10

    def test_get_user(self, client):
        user_data = {"id": 2, "login": "other", "public_repos": 5}
        client.get.return_value = user_data
        result = user_collector.get_user(client, "other")
        assert result.login == "other"

    def test_check_rate_limit(self, client):
        client.get.return_value = {
            "resources": {"core": {"limit": 5000, "remaining": 4999}}
        }
        result = user_collector.check_rate_limit(client)
        assert "core" in result
