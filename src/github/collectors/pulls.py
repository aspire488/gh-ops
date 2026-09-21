"""Pull requests collector."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.github.client import GitHubClient
from src.github.models import PullRequest


def list_pulls(
    client: GitHubClient,
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 100,
    max_pages: int = 5,
    sort: str = "updated",
    direction: str = "desc",
    etag: Optional[str] = None,
) -> list[PullRequest]:
    """List pull requests for a repository."""
    params: Dict[str, Any] = {
        "state": state,
        "sort": sort,
        "direction": direction,
    }
    result = client.get_paginated(
        f"/repos/{owner}/{repo}/pulls",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [PullRequest.from_api(item) for item in result.items if isinstance(item, dict)]


def list_pulls_for_repo(
    client: GitHubClient,
    full_name: str,
    state: str = "open",
    per_page: int = 100,
    max_pages: int = 5,
    sort: str = "updated",
    direction: str = "desc",
    etag: Optional[str] = None,
) -> list[PullRequest]:
    """List PRs using owner/repo format."""
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {full_name}, expected owner/repo")
    return list_pulls(client, parts[0], parts[1], state, per_page, max_pages, sort, direction, etag)


def get_pull(client: GitHubClient, owner: str, repo: str, number: int) -> PullRequest:
    """Get a specific pull request."""
    data = client.get(f"/repos/{owner}/{repo}/pulls/{number}")
    return PullRequest.from_api(data)
