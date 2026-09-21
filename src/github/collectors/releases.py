"""Releases collector."""
from __future__ import annotations

from typing import Dict, Optional

from src.github.client import GitHubClient
from src.github.models import Release


def list_releases(
    client: GitHubClient,
    owner: str,
    repo: str,
    per_page: int = 30,
    max_pages: int = 5,
    etag: Optional[str] = None,
) -> list[Release]:
    """List releases for a repository."""
    result = client.get_paginated(
        f"/repos/{owner}/{repo}/releases",
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [Release.from_api(item) for item in result.items if isinstance(item, dict)]


def list_releases_for_repo(
    client: GitHubClient,
    full_name: str,
    per_page: int = 30,
    max_pages: int = 5,
    etag: Optional[str] = None,
) -> list[Release]:
    """List releases using owner/repo format."""
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {full_name}, expected owner/repo")
    return list_releases(client, parts[0], parts[1], per_page, max_pages, etag)


def get_latest_release(client: GitHubClient, owner: str, repo: str) -> Optional[Release]:
    """Get the latest release for a repository."""
    try:
        data = client.get(f"/repos/{owner}/{repo}/releases/latest")
        return Release.from_api(data)
    except Exception:
        return None


def get_release_by_tag(client: GitHubClient, owner: str, repo: str, tag: str) -> Optional[Release]:
    """Get a specific release by tag."""
    try:
        data = client.get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
        return Release.from_api(data)
    except Exception:
        return None
