"""Normalized data models for GitHub API responses.

Minimal dataclasses that handle optional fields and provide
deterministic serialization. No credentials in models.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from src.utils.time import parse_github_timestamp


def _parse_ts(value: Any) -> Optional[datetime]:
    """Safely parse a timestamp string."""
    if not value or not isinstance(value, str):
        return None
    try:
        return parse_github_timestamp(value)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Repository:
    """Normalized repository metadata."""

    id: int
    full_name: str
    owner: str
    name: str
    description: Optional[str] = None
    html_url: Optional[str] = None
    language: Optional[str] = None
    stargazers_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0
    watchers_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    default_branch: str = "main"
    topics: tuple[str, ...] = ()
    license_key: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
            elif isinstance(value, tuple):
                d[key] = list(value)
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Repository:
        """Parse from GitHub API response."""
        owner_data = data.get("owner", {})
        owner_login = owner_data.get("login", "") if isinstance(owner_data, dict) else ""
        license_data = data.get("license")
        license_key = license_data.get("key") if isinstance(license_data, dict) else None
        return cls(
            id=data.get("id", 0),
            full_name=data.get("full_name", ""),
            owner=owner_login,
            name=data.get("name", ""),
            description=data.get("description"),
            html_url=data.get("html_url"),
            language=data.get("language"),
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            watchers_count=data.get("watchers_count", 0),
            created_at=_parse_ts(data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            pushed_at=_parse_ts(data.get("pushed_at")),
            archived=data.get("archived", False),
            disabled=data.get("disabled", False),
            fork=data.get("fork", False),
            default_branch=data.get("default_branch", "main"),
            topics=tuple(data.get("topics", [])),
            license_key=license_key,
        )


@dataclass(frozen=True)
class Issue:
    """Normalized issue."""

    id: int
    number: int
    title: str
    state: str
    repository_url: str
    user: Optional[str] = None
    labels: tuple[str, ...] = ()
    assignee: Optional[str] = None
    assignees: tuple[str, ...] = ()
    comments: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    body: Optional[str] = None
    html_url: Optional[str] = None
    author_association: Optional[str] = None
    locked: bool = False
    draft: bool = False

    @property
    def repo_full_name(self) -> str:
        """Extract owner/repo from repository_url."""
        parts = self.repository_url.rstrip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
            elif isinstance(value, tuple):
                d[key] = list(value)
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Issue:
        """Parse from GitHub API response."""
        user_data = data.get("user")
        user_login = user_data.get("login") if isinstance(user_data, dict) else None
        labels_list = data.get("labels", [])
        labels = tuple(l.get("name", "") for l in labels_list if isinstance(l, dict))
        assignee_data = data.get("assignee")
        assignee = assignee_data.get("login") if isinstance(assignee_data, dict) else None
        assignees_list = data.get("assignees", [])
        assignees = tuple(a.get("login", "") for a in assignees_list if isinstance(a, dict))
        return cls(
            id=data.get("id", 0),
            number=data.get("number", 0),
            title=data.get("title", ""),
            state=data.get("state", ""),
            repository_url=data.get("repository_url", ""),
            user=user_login,
            labels=labels,
            assignee=assignee,
            assignees=assignees,
            comments=data.get("comments", 0),
            created_at=_parse_ts(data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            closed_at=_parse_ts(data.get("closed_at")),
            body=data.get("body"),
            html_url=data.get("html_url"),
            author_association=data.get("author_association"),
            locked=data.get("locked", False),
            draft=data.get("draft", False),
        )


@dataclass(frozen=True)
class PullRequest:
    """Normalized pull request."""

    id: int
    number: int
    title: str
    state: str
    repository_url: str
    user: Optional[str] = None
    head_branch: Optional[str] = None
    base_branch: Optional[str] = None
    draft: bool = False
    merged: bool = False
    merged_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    html_url: Optional[str] = None
    author_association: Optional[str] = None
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0

    @property
    def repo_full_name(self) -> str:
        """Extract owner/repo from repository_url."""
        parts = self.repository_url.rstrip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PullRequest:
        """Parse from GitHub API response."""
        user_data = data.get("user")
        user_login = user_data.get("login") if isinstance(user_data, dict) else None
        head_data = data.get("head", {})
        head_branch = head_data.get("ref") if isinstance(head_data, dict) else None
        base_data = data.get("base", {})
        base_branch = base_data.get("ref") if isinstance(base_data, dict) else None
        repo_url = data.get("repository_url", "")
        if not repo_url and isinstance(base_data, dict):
            repo_data = base_data.get("repo", {})
            if isinstance(repo_data, dict):
                repo_url = repo_data.get("url", "")
        return cls(
            id=data.get("id", 0),
            number=data.get("number", 0),
            title=data.get("title", ""),
            state=data.get("state", ""),
            repository_url=repo_url,
            user=user_login,
            head_branch=head_branch,
            base_branch=base_branch,
            draft=data.get("draft", False),
            merged=data.get("merged", False),
            merged_at=_parse_ts(data.get("merged_at")),
            created_at=_parse_ts(data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            closed_at=_parse_ts(data.get("closed_at")),
            html_url=data.get("html_url"),
            author_association=data.get("author_association"),
            changed_files=data.get("changed_files", 0),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
        )


@dataclass(frozen=True)
class Release:
    """Normalized release."""

    id: int
    tag_name: str
    name: Optional[str] = None
    body: Optional[str] = None
    draft: bool = False
    prerelease: bool = False
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    html_url: Optional[str] = None
    author: Optional[str] = None
    assets_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Release:
        """Parse from GitHub API response."""
        author_data = data.get("author")
        author = author_data.get("login") if isinstance(author_data, dict) else None
        assets = data.get("assets", [])
        return cls(
            id=data.get("id", 0),
            tag_name=data.get("tag_name", ""),
            name=data.get("name"),
            body=data.get("body"),
            draft=data.get("draft", False),
            prerelease=data.get("prerelease", False),
            created_at=_parse_ts(data.get("created_at")),
            published_at=_parse_ts(data.get("published_at")),
            html_url=data.get("html_url"),
            author=author,
            assets_count=len(assets) if isinstance(assets, list) else 0,
        )


@dataclass(frozen=True)
class WorkflowRun:
    """Normalized workflow run."""

    id: int
    name: str
    status: str
    conclusion: Optional[str] = None
    run_number: int = 0
    event: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    run_started_at: Optional[datetime] = None
    html_url: Optional[str] = None
    head_branch: Optional[str] = None
    head_sha: Optional[str] = None

    @property
    def is_failure(self) -> bool:
        """True if the run failed."""
        return self.conclusion in ("failure", "timed_out", "cancelled")

    @property
    def is_success(self) -> bool:
        """True if the run succeeded."""
        return self.conclusion == "success"

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> WorkflowRun:
        """Parse from GitHub API response."""
        return cls(
            id=data.get("id", 0),
            name=data.get("name", ""),
            status=data.get("status", ""),
            conclusion=data.get("conclusion"),
            run_number=data.get("run_number", 0),
            event=data.get("event", ""),
            created_at=_parse_ts(data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            run_started_at=_parse_ts(data.get("run_started_at")),
            html_url=data.get("html_url"),
            head_branch=data.get("head_branch"),
            head_sha=data.get("head_sha"),
        )


@dataclass(frozen=True)
class SecurityAlert:
    """Normalized Dependabot/security alert."""

    id: int
    package_name: str
    severity: str
    summary: str
    state: str = "open"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    html_url: Optional[str] = None
    repository_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any], repo_url: str = "") -> SecurityAlert:
        """Parse from GitHub API response."""
        security_advisory = data.get("security_advisory", {})
        summary = ""
        severity = "unknown"
        if isinstance(security_advisory, dict):
            summary = security_advisory.get("summary", "")
            severity = security_advisory.get("severity", "unknown")
        security_vulnerability = data.get("security_vulnerability", {})
        package_name = ""
        if isinstance(security_vulnerability, dict):
            package_data = security_vulnerability.get("package", {})
            if isinstance(package_data, dict):
                package_name = package_data.get("name", "")
        return cls(
            id=data.get("number", data.get("id", 0)),
            package_name=package_name,
            severity=severity,
            summary=summary,
            state=data.get("state", "open"),
            created_at=_parse_ts(data.get("created_at")),
            updated_at=_parse_ts(data.get("updated_at")),
            html_url=data.get("html_url"),
            repository_url=repo_url or data.get("repository_url"),
        )


@dataclass(frozen=True)
class User:
    """Normalized authenticated user info."""

    id: int
    login: str
    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    public_repos: int = 0
    followers: int = 0
    following: int = 0
    created_at: Optional[datetime] = None
    html_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, datetime):
                d[key] = value.isoformat()
        return d

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> User:
        """Parse from GitHub API response."""
        return cls(
            id=data.get("id", 0),
            login=data.get("login", ""),
            name=data.get("name"),
            email=data.get("email"),
            bio=data.get("bio"),
            public_repos=data.get("public_repos", 0),
            followers=data.get("followers", 0),
            following=data.get("following", 0),
            created_at=_parse_ts(data.get("created_at")),
            html_url=data.get("html_url"),
        )


@dataclass(frozen=True)
class PaginatedResult:
    """Wrapper for paginated API responses."""

    items: tuple[Any, ...]
    total_count: int = 0
    page: int = 1
    per_page: int = 30
    has_next: bool = False
    etag: Optional[str] = None
    not_modified: bool = False

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __bool__(self) -> bool:
        return len(self.items) > 0
