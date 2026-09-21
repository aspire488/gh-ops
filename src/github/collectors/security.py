"""Security alerts collector (Dependabot)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.github.client import GitHubClient
from src.github.models import SecurityAlert


def list_dependabot_alerts(
    client: GitHubClient,
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 30,
    max_pages: int = 5,
    etag: Optional[str] = None,
) -> list[SecurityAlert]:
    """List Dependabot alerts for a repository.

    Note: Requires 'security_events' scope on the token.
    """
    params: Dict[str, Any] = {"state": state}
    repo_url = f"/repos/{owner}/{repo}"

    result = client.get_paginated(
        f"{repo_url}/dependabot/alerts",
        params=params,
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []

    alerts = []
    for item in result.items:
        if isinstance(item, dict):
            alerts.append(SecurityAlert.from_api(item, repo_url=repo_url))
    return alerts


def list_dependabot_alerts_for_repo(
    client: GitHubClient,
    full_name: str,
    state: str = "open",
    per_page: int = 30,
    max_pages: int = 5,
    etag: Optional[str] = None,
) -> list[SecurityAlert]:
    """List Dependabot alerts using owner/repo format."""
    parts = full_name.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {full_name}, expected owner/repo")
    return list_dependabot_alerts(client, parts[0], parts[1], state, per_page, max_pages, etag)


def list_code_scanning_alerts(
    client: GitHubClient,
    owner: str,
    repo: str,
    per_page: int = 30,
    max_pages: int = 3,
    etag: Optional[str] = None,
) -> list[SecurityAlert]:
    """List code scanning alerts.

    Note: Requires 'security_events' scope on the token.
    """
    result = client.get_paginated(
        f"/repos/{owner}/{repo}/code-scanning/alerts",
        per_page=per_page,
        max_pages=max_pages,
        etag=etag,
    )
    if result.not_modified:
        return []

    alerts = []
    for item in result.items:
        if isinstance(item, dict):
            rule = item.get("rule", {})
            severity = rule.get("severity", "unknown") if isinstance(rule, dict) else "unknown"
            alerts.append(SecurityAlert(
                id=item.get("number", 0),
                package_name=rule.get("id", "") if isinstance(rule, dict) else "",
                severity=severity,
                summary=rule.get("description", "") if isinstance(rule, dict) else "",
                state=item.get("state", "open"),
                html_url=item.get("html_url"),
            ))
    return alerts
