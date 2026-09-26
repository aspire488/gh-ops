"""Developer activity normalization for gh-ops Phase 6.

Extracts normalized developer activity from Phase 3 state and events.
Answers factual questions: what happened, when, where.

CRITICAL SEMANTICS:
- Activity timestamps come from resource fields (created_at, closed_at, etc.),
  NOT from collection time. "Issue currently closed" ≠ "when it was closed."
- Phase 3 events tell us WHAT changed. Resource timestamps tell us WHEN.
- A failed collector → activity type marked is_complete=False, NOT zero activity.
- No LLM, no scoring, no personality inference. Descriptive only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.core.events import (
    diff_resources,
    events_by_resource_type,
)
from src.core.models import CurrentState, EventType, ResourceEvent
from src.utils.logging import get_logger
from src.utils.time import parse_github_timestamp

logger = get_logger("developer.activity")


# ── Activity types ───────────────────────────────────────────────


class ActivityType(str, Enum):
    """Types of developer activity.

    Each maps to a concrete GitHub event, not an inference.
    """

    ISSUE_CREATED = "issue_created"
    ISSUE_CLOSED = "issue_closed"
    ISSUE_COMMENTED = "issue_commented"
    PR_OPENED = "pr_opened"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    PR_REVIEWED = "pr_reviewed"
    RELEASE_PUBLISHED = "release_published"
    WORKFLOW_RUN = "workflow_run"


# ── Activity record ──────────────────────────────────────────────


@dataclass(frozen=True)
class ActivityRecord:
    """A single normalized developer activity event.

    Attributes:
        activity_type: What kind of activity this is.
        timestamp: When the activity occurred (from resource field, NOT collection time).
        repository: Repository full name (owner/repo).
        item_id: Stable identifier (e.g., "owner/repo#123", "owner/repo@v1.0").
        title: Human-readable title (issue title, PR title, etc.).
        state: Current state (open/closed/merged for issues/PRs).
        url: HTML URL to the resource.
        meta: Additional structured metadata (labels, additions, etc.).
    """

    activity_type: ActivityType
    timestamp: datetime
    repository: str
    item_id: str
    title: str = ""
    state: str = ""
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        return {
            "activity_type": self.activity_type.value,
            "timestamp": self.timestamp.isoformat(),
            "repository": self.repository,
            "item_id": self.item_id,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "meta": dict(sorted(self.meta.items())),
        }


# ── Data quality ─────────────────────────────────────────────────


@dataclass(frozen=True)
class DataQuality:
    """Tracks data completeness for a collection run.

    A failed collector does NOT become zero activity.
    It means activity of that type is incomplete.

    Attributes:
        collector: Which collector produced this data.
        is_complete: Whether the data for this type is complete.
        observed_at: When this data was last observed.
        error: Error message if collection failed.
        item_count: Number of items collected.
    """

    collector: str
    is_complete: bool
    observed_at: str = ""
    error: str | None = None
    item_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "is_complete": self.is_complete,
            "observed_at": self.observed_at,
            "error": self.error,
            "item_count": self.item_count,
        }


# ── Activity extraction ──────────────────────────────────────────


def _parse_ts(value: Any) -> datetime | None:
    """Safely parse a timestamp string to datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        return parse_github_timestamp(value)
    except (ValueError, TypeError):
        return None


def _extract_repo_name(repository_url: str) -> str:
    """Extract owner/repo from a GitHub API repository_url."""
    parts = repository_url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return ""


def _extract_issue_activity(
    events: tuple[ResourceEvent, ...],
    username: str | None,
) -> list[ActivityRecord]:
    """Extract issue activity from Phase 3 events.

    Uses resource timestamps (created_at, closed_at) for timing.
    Phase 3 events tell us WHAT changed; resource fields tell us WHEN.
    """
    records: list[ActivityRecord] = []

    for event in events:
        data = event.current or event.previous or {}
        if not data:
            continue

        user_login = data.get("user", "")
        if username and user_login != username:
            continue

        repo_url = data.get("repository_url", "")
        repo = _extract_repo_name(repo_url)
        number = data.get("number", 0)
        title = data.get("title", "")
        state = data.get("state", "")
        url = data.get("html_url", "")
        labels = data.get("labels", [])
        author_association = data.get("author_association", "")

        item_id = f"{repo}#{number}" if repo else f"#{number}"

        if event.event_type == EventType.NEW:
            created_at = _parse_ts(data.get("created_at"))
            if created_at:
                records.append(ActivityRecord(
                    activity_type=ActivityType.ISSUE_CREATED,
                    timestamp=created_at,
                    repository=repo,
                    item_id=item_id,
                    title=title,
                    state=state,
                    url=url,
                    meta={
                        "labels": list(labels) if labels else [],
                        "author_association": author_association,
                    },
                ))

            # If the issue is already closed on first observation,
            # also emit the CLOSED activity (we missed the transition)
            if state == "closed":
                closed_at = _parse_ts(data.get("closed_at"))
                if closed_at:
                    records.append(ActivityRecord(
                        activity_type=ActivityType.ISSUE_CLOSED,
                        timestamp=closed_at,
                        repository=repo,
                        item_id=item_id,
                        title=title,
                        state="closed",
                        url=url,
                        meta={
                            "labels": list(labels) if labels else [],
                            "author_association": author_association,
                        },
                    ))

        elif event.event_type == EventType.CHANGED:
            changes = {c.field: c for c in event.changes}

            if "state" in changes and changes["state"].after == "closed":
                closed_at = _parse_ts(data.get("closed_at"))
                if closed_at:
                    records.append(ActivityRecord(
                        activity_type=ActivityType.ISSUE_CLOSED,
                        timestamp=closed_at,
                        repository=repo,
                        item_id=item_id,
                        title=title,
                        state="closed",
                        url=url,
                        meta={
                            "labels": list(labels) if labels else [],
                            "author_association": author_association,
                        },
                    ))

            if "comments" in changes:
                after_comments = changes["comments"].after
                before_comments = changes["comments"].before
                if (
                    isinstance(after_comments, int)
                    and isinstance(before_comments, int)
                    and after_comments > before_comments
                ):
                    updated_at = _parse_ts(data.get("updated_at"))
                    if updated_at:
                        records.append(ActivityRecord(
                            activity_type=ActivityType.ISSUE_COMMENTED,
                            timestamp=updated_at,
                            repository=repo,
                            item_id=item_id,
                            title=title,
                            state=state,
                            url=url,
                            meta={
                                "comments_added": after_comments - before_comments,
                                "total_comments": after_comments,
                                "author_association": author_association,
                            },
                        ))

    return records


def _extract_pr_activity(
    events: tuple[ResourceEvent, ...],
    username: str | None,
) -> list[ActivityRecord]:
    """Extract pull request activity from Phase 3 events.

    Uses resource timestamps for timing.
    Detects: PR opened, merged, closed (with state transitions).
    """
    records: list[ActivityRecord] = []

    for event in events:
        data = event.current or event.previous or {}
        if not data:
            continue

        user_login = data.get("user", "")
        if username and user_login != username:
            continue

        repo_url = data.get("repository_url", "")
        repo = _extract_repo_name(repo_url)
        number = data.get("number", 0)
        title = data.get("title", "")
        state = data.get("state", "")
        url = data.get("html_url", "")
        merged = data.get("merged", False)
        author_association = data.get("author_association", "")
        additions = data.get("additions", 0)
        deletions = data.get("deletions", 0)
        changed_files = data.get("changed_files", 0)

        item_id = f"{repo}#{number}" if repo else f"#{number}"

        if event.event_type == EventType.NEW:
            created_at = _parse_ts(data.get("created_at"))
            if created_at:
                records.append(ActivityRecord(
                    activity_type=ActivityType.PR_OPENED,
                    timestamp=created_at,
                    repository=repo,
                    item_id=item_id,
                    title=title,
                    state=state,
                    url=url,
                    meta={
                        "author_association": author_association,
                        "additions": additions,
                        "deletions": deletions,
                        "changed_files": changed_files,
                    },
                ))

            # If the PR is already merged on first observation,
            # also emit the MERGED activity (we missed the transition)
            if merged:
                merged_at = _parse_ts(data.get("merged_at"))
                if merged_at:
                    records.append(ActivityRecord(
                        activity_type=ActivityType.PR_MERGED,
                        timestamp=merged_at,
                        repository=repo,
                        item_id=item_id,
                        title=title,
                        state="merged",
                        url=url,
                        meta={
                            "additions": additions,
                            "deletions": deletions,
                            "changed_files": changed_files,
                        },
                    ))

        elif event.event_type == EventType.CHANGED:
            changes = {c.field: c for c in event.changes}

            if "merged" in changes and changes["merged"].after is True:
                merged_at = _parse_ts(data.get("merged_at"))
                if merged_at:
                    records.append(ActivityRecord(
                        activity_type=ActivityType.PR_MERGED,
                        timestamp=merged_at,
                        repository=repo,
                        item_id=item_id,
                        title=title,
                        state="merged",
                        url=url,
                        meta={
                            "additions": additions,
                            "deletions": deletions,
                            "changed_files": changed_files,
                        },
                    ))

            if "state" in changes and changes["state"].after == "closed" and not merged:
                closed_at = _parse_ts(data.get("closed_at"))
                if closed_at:
                    records.append(ActivityRecord(
                        activity_type=ActivityType.PR_CLOSED,
                        timestamp=closed_at,
                        repository=repo,
                        item_id=item_id,
                        title=title,
                        state="closed",
                        url=url,
                        meta={
                            "additions": additions,
                            "deletions": deletions,
                            "changed_files": changed_files,
                        },
                    ))

    return records


def _extract_release_activity(
    events: tuple[ResourceEvent, ...],
    username: str | None,
) -> list[ActivityRecord]:
    """Extract release activity from Phase 3 events.

    Uses published_at for timing.
    """
    records: list[ActivityRecord] = []

    for event in events:
        data = event.current or event.previous or {}
        if not data:
            continue

        author = data.get("author", "")
        if username and author != username:
            continue

        tag_name = data.get("tag_name", "")
        name = data.get("name", "")
        url = data.get("html_url", "")
        prerelease = data.get("prerelease", False)
        draft = data.get("draft", False)
        assets_count = data.get("assets_count", 0)

        display_name = name or tag_name
        item_id = f"{tag_name}"

        if event.event_type == EventType.NEW:
            published_at = _parse_ts(data.get("published_at"))
            if published_at:
                records.append(ActivityRecord(
                    activity_type=ActivityType.RELEASE_PUBLISHED,
                    timestamp=published_at,
                    repository="",
                    item_id=item_id,
                    title=display_name,
                    state="published",
                    url=url,
                    meta={
                        "tag_name": tag_name,
                        "prerelease": prerelease,
                        "draft": draft,
                        "assets_count": assets_count,
                    },
                ))

    return records


def _workflow_repository(resource_id: str) -> str:
    """Derive owner/repo from a workflow_run resource id (``o/r/run/123``)."""
    if not resource_id or "/run/" not in resource_id:
        return ""
    return resource_id.rsplit("/run/", 1)[0]


def _extract_workflow_activity(
    events: tuple[ResourceEvent, ...],
    username: str | None,
) -> list[ActivityRecord]:
    """Extract workflow run activity from Phase 3 events.

    Uses run_started_at or created_at for timing.     gh-ops's own workflow
    runs are excluded: they are system execution, not developer activity.
    """
    records: list[ActivityRecord] = []

    # Lazy import: src.reporting's package init pulls in convert.py, which
    # imports this module — a module-level import would cycle.
    from src.reporting.model import is_gh_ops_system_workflow

    for event in events:
        data = event.current or event.previous or {}
        if not data:
            continue

        name = data.get("name", "")
        status = data.get("status", "")
        conclusion = data.get("conclusion", "")
        url = data.get("html_url", "")
        event_type_name = data.get("event", "")
        head_branch = data.get("head_branch", "")
        repository = _workflow_repository(event.resource_id)

        if is_gh_ops_system_workflow(repository, str(name)):
            continue

        item_id = f"workflow/{data.get('id', 0)}"

        if event.event_type in (EventType.NEW, EventType.CHANGED):
            ts = _parse_ts(data.get("run_started_at")) or _parse_ts(data.get("created_at"))
            if ts:
                records.append(ActivityRecord(
                    activity_type=ActivityType.WORKFLOW_RUN,
                    timestamp=ts,
                    repository=repository,
                    item_id=item_id,
                    title=name,
                    state=conclusion or status,
                    url=url,
                    meta={
                        "event": event_type_name,
                        "status": status,
                        "conclusion": conclusion,
                        "head_branch": head_branch,
                    },
                ))

    return records


def extract_activity(
    state: CurrentState,
    events: tuple[ResourceEvent, ...] = (),
    username: str | None = None,
) -> tuple[list[ActivityRecord], list[DataQuality]]:
    """Extract normalized developer activity from Phase 3 state and events.

    This is the main entry point. It processes all resource types and
    produces a unified timeline of developer activity.

    Args:
        state: Current state containing collected resources.
        events: Phase 3 events from diff_resources (optional, used for
                detecting state transitions like open→closed).
        username: Filter to this developer's activity only. If None,
                  returns all activity (useful for repo-level reports).

    Returns:
        Tuple of (activity records sorted by timestamp, data quality info).
    """
    all_records: list[ActivityRecord] = []
    data_quality: list[DataQuality] = []

    # Track data quality from state update_log
    for collector_name, log_entry in state.update_log.items():
        status = log_entry.get("status", "unknown")
        observed_at = log_entry.get("observed_at", "")
        item_count = log_entry.get("item_count", 0)
        error = log_entry.get("error") if status == "failure" else None

        data_quality.append(DataQuality(
            collector=collector_name,
            is_complete=status == "success",
            observed_at=observed_at,
            error=error,
            item_count=item_count,
        ))

    # Extract activity from current state resources
    issues_data = state.resources.get("issues", {})
    pulls_data = state.resources.get("pulls", {})
    releases_data = state.resources.get("releases", {})
    workflows_data = state.resources.get("workflows", {})

    # Convert state resources to ResourceEvents for extraction
    # We treat current state as "current" with empty "previous" to get NEW events
    # for all items. This captures the snapshot of what exists.
    if issues_data:
        issue_events = diff_resources(
            resource_type="issues",
            previous_items={},
            current_items=issues_data,
        )
        records = _extract_issue_activity(issue_events, username)
        all_records.extend(records)

    if pulls_data:
        pr_events = diff_resources(
            resource_type="pulls",
            previous_items={},
            current_items=pulls_data,
        )
        records = _extract_pr_activity(pr_events, username)
        all_records.extend(records)

    if releases_data:
        release_events = diff_resources(
            resource_type="releases",
            previous_items={},
            current_items=releases_data,
        )
        records = _extract_release_activity(release_events, username)
        all_records.extend(records)

    if workflows_data:
        workflow_events = diff_resources(
            resource_type="workflows",
            previous_items={},
            current_items=workflows_data,
        )
        records = _extract_workflow_activity(workflow_events, username)
        all_records.extend(records)

    # Also extract from explicit events (for state transitions like open→closed)
    if events:
        issue_events = events_by_resource_type(events, "issues")
        pr_events = events_by_resource_type(events, "pulls")
        release_events = events_by_resource_type(events, "releases")
        workflow_events = events_by_resource_type(events, "workflows")

        all_records.extend(_extract_issue_activity(issue_events, username))
        all_records.extend(_extract_pr_activity(pr_events, username))
        all_records.extend(_extract_release_activity(release_events, username))
        all_records.extend(_extract_workflow_activity(workflow_events, username))

    # Sort by timestamp (oldest first)
    all_records.sort(key=lambda r: r.timestamp)

    logger.info(
        "Extracted %d activity records (%d data quality entries)",
        len(all_records),
        len(data_quality),
    )

    return all_records, data_quality


def filter_activity(
    records: list[ActivityRecord],
    *,
    activity_types: list[ActivityType] | None = None,
    repositories: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[ActivityRecord]:
    """Filter activity records by type, repository, and time range.

    Args:
        records: Activity records to filter.
        activity_types: If provided, only include these types.
        repositories: If provided, only include these repos.
        since: If provided, only include activities at or after this time.
        until: If provided, only include activities at or before this time.

    Returns:
        Filtered list of activity records.
    """
    result = list(records)

    if activity_types:
        type_values = {t.value for t in activity_types}
        result = [r for r in result if r.activity_type.value in type_values]

    if repositories:
        repo_set = set(repositories)
        result = [r for r in result if r.repository in repo_set]

    if since:
        result = [r for r in result if r.timestamp >= since]

    if until:
        result = [r for r in result if r.timestamp <= until]

    return result


def deduplicate_activity(records: list[ActivityRecord]) -> list[ActivityRecord]:
    """Remove duplicate activity records.

    Two records are considered duplicates if they have the same
    (activity_type, item_id, timestamp). Keeps the first occurrence.

    Args:
        records: Activity records to deduplicate.

    Returns:
        Deduplicated list.
    """
    seen: set[tuple[str, str, str]] = set()
    result: list[ActivityRecord] = []

    for record in records:
        key = (record.activity_type.value, record.item_id, record.timestamp.isoformat())
        if key not in seen:
            seen.add(key)
            result.append(record)

    return result


def summarize_activity(records: list[ActivityRecord]) -> dict[str, int]:
    """Summarize activity counts by type.

    Args:
        records: Activity records.

    Returns:
        Dict mapping activity_type.value → count.
    """
    summary: dict[str, int] = {}
    for record in records:
        key = record.activity_type.value
        summary[key] = summary.get(key, 0) + 1
    return summary
