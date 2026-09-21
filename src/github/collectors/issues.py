"""Issues collector. Fetches issues across repositories."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.github.client import GitHubClient
from src.github.models import Issue


def list_issues(
    client: GitHubClient,
    owner: str,
    repo: str,
    state: str = "open",
    labels: str = "",
    per_page: int = 100,
    max_pages: int = 5,
    sort: str = "updated",
    direction: str = "desc",
    etag: Optional[str] = None,
) -> list[Issue]:
    """List issues for a repository."""
    params: Dict[str, Any] = {
        "state": state,
        "sort": sort,
        "direction": direction,
    }
    if labels:
        params["labels"] = labels

    result = client.get_paginated(
        f"/repos/{owner}/{repo}/issues",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []

    issues = []
    for item in result.items:
        if isinstance(item, dict):
            # Filter out pull requests (GitHub issues API returns PRs too)
            if "pull_request" not in item:
                issues.append(Issue.from_api(item))
    return issues


def list_issues_for_repo(
    client: GitHubClient,
    full_name: str,
    state: str = "open",
    labels: str = "",
    per_page: int = 100,
    max_pages: int = 5,
    sort: str = "updated",
    direction: str = "desc",
    etag: Optional[str] = None,
) -> list[Issue]:
    """List issues using owner/repo format."""
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {full_name}, expected owner/repo")
    return list_issues(client, parts[0], parts[1], state, labels, per_page, max_pages, sort, direction, etag)


def list_my_issues(
    client: GitHubClient,
    state: str = "open",
    per_page: int = 100,
    max_pages: int = 3,
    sort: str = "updated",
    etag: Optional[str] = None,
) -> list[Issue]:
    """List issues assigned to the authenticated user."""
    params: Dict[str, Any] = {"state": state, "sort": sort}
    result = client.get_paginated(
        "/issues",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [Issue.from_api(item) for item in result.items if isinstance(item, dict)]


def get_issue(client: GitHubClient, owner: str, repo: str, number: int) -> Issue:
    """Get a specific issue."""
    data = client.get(f"/repos/{owner}/{repo}/issues/{number}")
    return Issue.from_api(data)


def search_issues(
    client: GitHubClient,
    query: str,
    per_page: int = 30,
    max_pages: int = 3,
    sort: str = "updated",
    etag: Optional[str] = None,
) -> list[Issue]:
    """Search issues using the search API."""
    params: Dict[str, Any] = {"q": query, "sort": sort}
    result = client.get_paginated(
        "/search/issues",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [Issue.from_api(item) for item in result.items if isinstance(item, dict)]
