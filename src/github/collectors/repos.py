"""Repository collector. Fetches repository metadata."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.github.client import GitHubClient
from src.github.models import Repository


def list_user_repos(
    client: GitHubClient,
    per_page: int = 100,
    max_pages: int = 5,
    visibility: str = "all",
    sort: str = "updated",
    etag: Optional[str] = None,
) -> list[Repository]:
    """List repositories for the authenticated user."""
    params: Dict[str, Any] = {"visibility": visibility, "sort": sort}
    result = client.get_paginated(
        "/user/repos",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [Repository.from_api(item) for item in result.items if isinstance(item, dict)]


def list_org_repos(
    client: GitHubClient,
    org: str,
    per_page: int = 100,
    max_pages: int = 5,
    sort: str = "updated",
    etag: Optional[str] = None,
) -> list[Repository]:
    """List repositories for an organization."""
    params: Dict[str, Any] = {"sort": sort}
    result = client.get_paginated(
        f"/orgs/{org}/repos",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [Repository.from_api(item) for item in result.items if isinstance(item, dict)]


def get_repo(client: GitHubClient, owner: str, repo: str) -> Repository:
    """Get a specific repository."""
    data = client.get(f"/repos/{owner}/{repo}")
    return Repository.from_api(data)


def list_repo_topics(client: GitHubClient, owner: str, repo: str) -> list[str]:
    """List repository topics."""
    data = client.get(f"/repos/{owner}/{repo}/topics")
    if isinstance(data, dict):
        return data.get("names", [])
    return []


def get_readme(client: GitHubClient, owner: str, repo: str) -> Optional[str]:
    """Get README content (raw text, decoded from base64)."""
    import base64
    try:
        data = client.get(f"/repos/{owner}/{repo}/readme")
        if isinstance(data, dict) and "content" in data:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception:
        return None
    return None
