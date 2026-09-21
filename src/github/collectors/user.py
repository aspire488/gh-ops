"""User/identity collector."""
from __future__ import annotations

from typing import Optional

from src.github.client import GitHubClient
from src.github.models import User


def get_authenticated_user(client: GitHubClient) -> Optional[User]:
    """Get the currently authenticated user."""
    try:
        data = client.get("/user")
        return User.from_api(data)
    except Exception:
        return None


def get_user(client: GitHubClient, username: str) -> Optional[User]:
    """Get a public user profile."""
    try:
        data = client.get(f"/users/{username}")
        return User.from_api(data)
    except Exception:
        return None


def check_rate_limit(client: GitHubClient) -> dict:
    """Check the authenticated rate limit."""
    try:
        data = client.get("/rate_limit")
        if isinstance(data, dict):
            return data.get("resources", {})
    except Exception:
        pass
    return {}
