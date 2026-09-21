"""Workflows/Actions collector."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.github.client import GitHubClient
from src.github.models import WorkflowRun


def list_workflow_runs(
    client: GitHubClient,
    owner: str,
    repo: str,
    per_page: int = 30,
    max_pages: int = 5,
    status: str = "",
    etag: Optional[str] = None,
) -> list[WorkflowRun]:
    """List workflow runs for a repository."""
    params: Dict[str, Any] = {}
    if status:
        params["status"] = status

    result = client.get_paginated(
        f"/repos/{owner}/{repo}/actions/runs",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []
    return [WorkflowRun.from_api(item) for item in result.items if isinstance(item, dict)]


def list_workflow_runs_for_repo(
    client: GitHubClient,
    full_name: str,
    per_page: int = 30,
    max_pages: int = 5,
    status: str = "",
    etag: Optional[str] = None,
) -> list[WorkflowRun]:
    """List workflow runs using owner/repo format."""
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {full_name}, expected owner/repo")
    return list_workflow_runs(client, parts[0], parts[1], per_page, max_pages, status, etag)


def get_workflow_run(client: GitHubClient, owner: str, repo: str, run_id: int) -> Optional[WorkflowRun]:
    """Get a specific workflow run."""
    try:
        data = client.get(f"/repos/{owner}/{repo}/actions/runs/{run_id}")
        return WorkflowRun.from_api(data)
    except Exception:
        return None
