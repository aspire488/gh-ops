"""Tests for src.github.models — data model parsing and serialization."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.github.models import (
    Issue,
    PaginatedResult,
    PullRequest,
    Release,
    Repository,
    SecurityAlert,
    User,
    WorkflowRun,
    _parse_ts,
)


class TestParseTs:
    def test_valid_iso(self):
        result = _parse_ts("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.year == 2024

    def test_none(self):
        assert _parse_ts(None) is None

    def test_empty_string(self):
        assert _parse_ts("") is None

    def test_non_string(self):
        assert _parse_ts(12345) is None


class TestRepository:
    def test_from_api(self):
        data = {
            "id": 1,
            "full_name": "owner/repo",
            "owner": {"login": "owner"},
            "name": "repo",
            "stargazers_count": 100,
            "forks_count": 10,
            "open_issues_count": 5,
            "topics": ["python", "cli"],
            "license": {"key": "mit"},
            "archived": False,
            "created_at": "2024-01-01T00:00:00Z",
        }
        repo = Repository.from_api(data)
        assert repo.id == 1
        assert repo.owner == "owner"
        assert repo.name == "repo"
        assert repo.stargazers_count == 100
        assert repo.topics == ("python", "cli")
        assert repo.license_key == "mit"

    def test_to_dict(self):
        repo = Repository(id=1, full_name="o/r", owner="o", name="r")
        d = repo.to_dict()
        assert d["id"] == 1
        assert d["topics"] == []

    def test_frozen(self):
        repo = Repository(id=1, full_name="o/r", owner="o", name="r")
        with pytest.raises(AttributeError):
            repo.id = 2


class TestIssue:
    def test_from_api(self):
        data = {
            "id": 1,
            "number": 10,
            "title": "Bug report",
            "state": "open",
            "repository_url": "https://api.github.com/repos/owner/repo",
            "user": {"login": "reporter"},
            "labels": [{"name": "bug"}, {"name": "priority"}],
            "comments": 3,
        }
        issue = Issue.from_api(data)
        assert issue.number == 10
        assert issue.labels == ("bug", "priority")
        assert issue.repo_full_name == "owner/repo"

    def test_repo_full_name_short_url(self):
        issue = Issue(id=1, number=1, title="t", state="s", repository_url="x/y")
        assert issue.repo_full_name == "x/y"


class TestPullRequest:
    def test_from_api(self):
        data = {
            "id": 1,
            "number": 5,
            "title": "Fix bug",
            "state": "open",
            "user": {"login": "dev"},
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "draft": False,
            "merged": False,
            "repository_url": "https://api.github.com/repos/owner/repo",
        }
        pr = PullRequest.from_api(data)
        assert pr.number == 5
        assert pr.head_branch == "feature"
        assert pr.base_branch == "main"
        assert pr.merged is False

    def test_repo_full_name(self):
        pr = PullRequest(
            id=1, number=1, title="t", state="s",
            repository_url="https://api.github.com/repos/o/r",
        )
        assert pr.repo_full_name == "o/r"


class TestRelease:
    def test_from_api(self):
        data = {
            "id": 1,
            "tag_name": "v1.0.0",
            "name": "Release 1.0",
            "draft": False,
            "prerelease": False,
            "author": {"login": "maintainer"},
            "assets": [{"id": 1}, {"id": 2}],
            "published_at": "2024-06-01T12:00:00Z",
        }
        release = Release.from_api(data)
        assert release.tag_name == "v1.0.0"
        assert release.author == "maintainer"
        assert release.assets_count == 2


class TestWorkflowRun:
    def test_from_api(self):
        data = {
            "id": 1,
            "name": "CI",
            "status": "completed",
            "conclusion": "success",
            "event": "push",
        }
        run = WorkflowRun.from_api(data)
        assert run.is_success is True
        assert run.is_failure is False

    def test_failure(self):
        run = WorkflowRun(id=1, name="CI", status="completed", conclusion="failure")
        assert run.is_failure is True

    def test_no_conclusion(self):
        run = WorkflowRun(id=1, name="CI", status="in_progress")
        assert run.is_success is False
        assert run.is_failure is False


class TestSecurityAlert:
    def test_from_api(self):
        data = {
            "number": 42,
            "state": "open",
            "security_advisory": {"summary": "XSS vuln", "severity": "high"},
            "security_vulnerability": {"package": {"name": "lodash"}},
        }
        alert = SecurityAlert.from_api(data, repo_url="/repos/o/r")
        assert alert.id == 42
        assert alert.severity == "high"
        assert alert.package_name == "lodash"


class TestUser:
    def test_from_api(self):
        data = {
            "id": 1,
            "login": "testuser",
            "name": "Test User",
            "public_repos": 10,
        }
        user = User.from_api(data)
        assert user.login == "testuser"
        assert user.public_repos == 10


class TestPaginatedResult:
    def test_empty(self):
        result = PaginatedResult(items=())
        assert len(result) == 0
        assert bool(result) is False

    def test_with_items(self):
        result = PaginatedResult(items=(1, 2, 3))
        assert len(result) == 3
        assert bool(result) is True
        assert list(result) == [1, 2, 3]
