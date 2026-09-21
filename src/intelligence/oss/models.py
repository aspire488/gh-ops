"""OSS Contribution Opportunity models.

Frozen dataclass for a single contribution opportunity.
No external dependencies in runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScoreBreakdown:
    """Transparent breakdown of how a total score was computed."""

    repo_activity: float = 0.0
    issue_freshness: float = 0.0
    label_signal: float = 0.0
    discussion_activity: float = 0.0
    assignment_status: float = 0.0
    maintainer_activity: float = 0.0
    scope_signal: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_activity": self.repo_activity,
            "issue_freshness": self.issue_freshness,
            "label_signal": self.label_signal,
            "discussion_activity": self.discussion_activity,
            "assignment_status": self.assignment_status,
            "maintainer_activity": self.maintainer_activity,
            "scope_signal": self.scope_signal,
        }


@dataclass(frozen=True)
class Opportunity:
    """A single OSS contribution opportunity.

    Identity is (repo_full_name, issue_number) — NOT title.
    Provenance tracks which query discovered this opportunity.
    """

    repo_full_name: str
    issue_number: int
    issue_id: int
    title: str
    html_url: str
    state: str
    user: str
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    assignees: tuple[str, ...] = ()
    comments: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    body: str | None = None

    # Repository metadata
    repo_stars: int = 0
    repo_forks: int = 0
    repo_open_issues: int = 0
    repo_language: str | None = None
    repo_description: str | None = None

    # Scoring
    score: float = 0.0
    score_breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)

    # Provenance
    source_query: str = ""
    source_queries: tuple[str, ...] = ()
    discovered_at: datetime | None = None

    def identity(self) -> tuple[str, int]:
        """Unique identity: (repo_full_name, issue_number)."""
        return (self.repo_full_name, self.issue_number)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d: dict[str, Any] = {
            "repo_full_name": self.repo_full_name,
            "issue_number": self.issue_number,
            "issue_id": self.issue_id,
            "title": self.title,
            "html_url": self.html_url,
            "state": self.state,
            "user": self.user,
            "labels": list(self.labels),
            "assignee": self.assignee,
            "assignees": list(self.assignees),
            "comments": self.comments,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "body": self.body,
            "repo_stars": self.repo_stars,
            "repo_forks": self.repo_forks,
            "repo_open_issues": self.repo_open_issues,
            "repo_language": self.repo_language,
            "repo_description": self.repo_description,
            "score": self.score,
            "score_breakdown": self.score_breakdown.to_dict(),
            "source_query": self.source_query,
            "source_queries": list(self.source_queries),
            "discovered_at": self.discovered_at.isoformat() if self.discovered_at else None,
        }
        return d

    @classmethod
    def from_search_result(
        cls,
        data: dict[str, Any],
        source_query: str = "",
        repo_stars: int = 0,
        repo_forks: int = 0,
        repo_open_issues: int = 0,
        repo_language: str | None = None,
        repo_description: str | None = None,
    ) -> Opportunity:
        """Parse from GitHub search/issues API response item."""
        from src.utils.time import parse_github_timestamp

        user_data = data.get("user")
        user_login = user_data.get("login", "") if isinstance(user_data, dict) else ""
        labels_list = data.get("labels", [])
        labels = tuple(l.get("name", "") for l in labels_list if isinstance(l, dict))
        assignee_data = data.get("assignee")
        assignee = assignee_data.get("login") if isinstance(assignee_data, dict) else None
        assignees_list = data.get("assignees", [])
        assignees = tuple(a.get("login", "") for a in assignees_list if isinstance(a, dict))

        # Extract repo_full_name from repository_url
        repo_url = data.get("repository_url", "")
        repo_parts = repo_url.rstrip("/").split("/")
        repo_full_name = f"{repo_parts[-2]}/{repo_parts[-1]}" if len(repo_parts) >= 2 else ""

        return cls(
            repo_full_name=repo_full_name,
            issue_number=data.get("number", 0),
            issue_id=data.get("id", 0),
            title=data.get("title", ""),
            html_url=data.get("html_url", ""),
            state=data.get("state", ""),
            user=user_login,
            labels=labels,
            assignee=assignee,
            assignees=assignees,
            comments=data.get("comments", 0),
            created_at=parse_github_timestamp(data["created_at"]) if data.get("created_at") else None,
            updated_at=parse_github_timestamp(data["updated_at"]) if data.get("updated_at") else None,
            body=data.get("body"),
            repo_stars=repo_stars,
            repo_forks=repo_forks,
            repo_open_issues=repo_open_issues,
            repo_language=repo_language,
            repo_description=repo_description,
            source_query=source_query,
        )
