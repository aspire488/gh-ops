"""Convert domain results into ReportEvents."""
from __future__ import annotations

from typing import Any

from src.core.monitor import MonitorResult, MonitorStatus
from src.developer.activity import ActivityRecord, ActivityType
from src.reporting.model import (
    SUBSYSTEM_CI,
    SUBSYSTEM_DEVELOPER,
    SUBSYSTEM_ENDPOINT,
    SUBSYSTEM_MONITORING,
    SUBSYSTEM_OSS,
    SUBSYSTEM_RELEASE,
    SUBSYSTEM_REPOSITORY,
    SUBSYSTEM_SECURITY,
    ReportEvent,
    Severity,
)

#: Monitor names that belong to the alerts batch (immediate Telegram path).
MONITORING_SUBSYSTEMS = frozenset(
    {
        SUBSYSTEM_MONITORING,
        SUBSYSTEM_CI,
        SUBSYSTEM_RELEASE,
        SUBSYSTEM_REPOSITORY,
        SUBSYSTEM_ENDPOINT,
        SUBSYSTEM_SECURITY,
    }
)

_ACTIONABLE_SECURITY_SEVERITIES = frozenset({"critical", "high"})

_ACTIVITY_SEVERITY: dict[ActivityType, Severity] = {
    ActivityType.ISSUE_CREATED: Severity.INFORMATION,
    ActivityType.ISSUE_CLOSED: Severity.INFORMATION,
    ActivityType.ISSUE_COMMENTED: Severity.INFORMATION,
    ActivityType.PR_OPENED: Severity.INFORMATION,
    ActivityType.PR_MERGED: Severity.INFORMATION,
    ActivityType.PR_CLOSED: Severity.INFORMATION,
    ActivityType.PR_REVIEWED: Severity.INFORMATION,
    ActivityType.RELEASE_PUBLISHED: Severity.INFORMATION,
    ActivityType.WORKFLOW_RUN: Severity.INFORMATION,
}

_ACTIVITY_LABEL: dict[ActivityType, str] = {
    ActivityType.ISSUE_CREATED: "Issue opened",
    ActivityType.ISSUE_CLOSED: "Issue closed",
    ActivityType.ISSUE_COMMENTED: "Issue commented",
    ActivityType.PR_OPENED: "PR opened",
    ActivityType.PR_MERGED: "PR merged",
    ActivityType.PR_CLOSED: "PR closed",
    ActivityType.PR_REVIEWED: "PR reviewed",
    ActivityType.RELEASE_PUBLISHED: "Release published",
    ActivityType.WORKFLOW_RUN: "Workflow run",
}


def _repository_from_resource(resource: str) -> str:
    """Extract owner/repo from a monitor resource id."""
    if not resource or resource == "*":
        return ""
    if resource.startswith("run/"):
        return ""
    parts = resource.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return resource


def _subsystem_for(result: MonitorResult) -> str:
    monitor = (result.monitor or "").lower()
    if monitor in ("ci", "workflows"):
        return SUBSYSTEM_CI
    if monitor == "release":
        return SUBSYSTEM_RELEASE
    if monitor in ("repository", "repos"):
        return SUBSYSTEM_REPOSITORY
    if monitor == "endpoint":
        return SUBSYSTEM_ENDPOINT
    if monitor == "security":
        return SUBSYSTEM_SECURITY
    return SUBSYSTEM_MONITORING


def _severity_for(result: MonitorResult) -> Severity | None:
    """Map a MonitorResult to a report severity, or None when not notifiable."""
    metadata = result.metadata or {}
    event_type = str(metadata.get("event_type") or "")
    monitor = (result.monitor or "").lower()

    if result.status is MonitorStatus.ERROR:
        return Severity.IMPORTANT
    if result.status is MonitorStatus.SKIPPED:
        return None

    if monitor == "security":
        if result.status is MonitorStatus.ALERT:
            return Severity.ACTION_REQUIRED
        if event_type == "alert_removed":
            return Severity.RESOLVED
        # Non-actionable security noise is not user-facing.
        return None

    if monitor == "ci" or result.category.value == "ci":
        if event_type == "recovery" or (
            result.status is MonitorStatus.OK and event_type == "success"
        ):
            return Severity.RESOLVED
        if result.status is MonitorStatus.ALERT:
            branch = str(metadata.get("branch") or metadata.get("head_branch") or "")
            if branch in ("main", "master"):
                return Severity.ACTION_REQUIRED
            return Severity.IMPORTANT
        if result.status is MonitorStatus.CHANGED:
            return None
        return None

    if monitor == "release" or result.category.value == "release":
        if event_type == "new_release" and result.status is MonitorStatus.OK:
            return Severity.INFORMATION
        if event_type == "release_removed":
            return Severity.INFORMATION
        if result.status is MonitorStatus.ALERT:
            return Severity.IMPORTANT
        return None

    if monitor in ("repository", "repos") or result.category.value == "repository":
        action = str(metadata.get("action") or "")
        if action == "removed" or result.status is MonitorStatus.CHANGED and action == "removed":
            return Severity.INFORMATION
        if result.status is MonitorStatus.ALERT:
            return Severity.IMPORTANT
        if action == "tracked" or result.status is MonitorStatus.OK:
            return None
        return None

    if result.status is MonitorStatus.ALERT:
        severity = str(metadata.get("severity") or "").lower()
        if severity in _ACTIONABLE_SECURITY_SEVERITIES:
            return Severity.ACTION_REQUIRED
        return Severity.IMPORTANT
    if result.status is MonitorStatus.CHANGED:
        return None
    return None


def report_event_from_monitor(result: MonitorResult) -> ReportEvent | None:
    """Convert one MonitorResult into a ReportEvent, or None if not notifiable."""
    severity = _severity_for(result)
    if severity is None:
        return None

    metadata = dict(result.metadata or {})
    event_type = str(metadata.get("event_type") or result.status.value)
    repository = _repository_from_resource(result.resource)
    if not repository:
        repository = str(metadata.get("repository") or "")

    title = result.summary or f"{result.monitor}: {result.resource}"
    identity = f"{result.monitor}:{result.resource}:{event_type}"
    if result.status is MonitorStatus.ERROR:
        identity = f"{result.monitor}:collection_error:{result.resource}"

    # Prefer explicit metadata URL, then the triggering event payload.
    url = str(metadata.get("url") or metadata.get("html_url") or "")
    if not url and result.events:
        first = result.events[0]
        if hasattr(first, "current") and isinstance(first.current, dict):
            url = str(first.current.get("html_url") or first.current.get("url") or "")

    # Context carried into Telegram event blocks (branch, workflow, package, …).
    context: dict[str, Any] = {
        "monitor": result.monitor,
        "resource": result.resource,
        "status": result.status.value,
    }
    for key in ("branch", "head_branch", "workflow_name", "conclusion", "package_name", "severity"):
        if metadata.get(key) not in (None, ""):
            context[key] = metadata[key]
    if result.events:
        first = result.events[0]
        current = getattr(first, "current", None)
        if isinstance(current, dict):
            for key in ("branch", "head_branch", "workflow_name", "conclusion", "html_url"):
                if key in current and current[key] not in (None, ""):
                    context.setdefault(key, current[key])
            if not url:
                url = str(current.get("html_url") or current.get("url") or "")

    # Normalize head_branch → branch for the shared context renderer.
    if "branch" not in context and "head_branch" in context:
        context["branch"] = context.pop("head_branch")
    context.pop("head_branch", None)

    return ReportEvent(
        subsystem=_subsystem_for(result),
        event_type=event_type or result.status.value,
        severity=severity,
        title=title,
        description="",
        repository=repository or result.resource,
        url=url,
        identity=identity,
        timestamp=str(result.evaluated_at or ""),
        source=result.monitor,
        metadata=context,
    )


def report_event_from_activity(record: ActivityRecord) -> ReportEvent | None:
    """Convert an ActivityRecord into an INFORMATION ReportEvent."""
    severity = _ACTIVITY_SEVERITY.get(record.activity_type, Severity.INFORMATION)
    label = _ACTIVITY_LABEL.get(record.activity_type, record.activity_type.value)
    title = record.title or record.item_id
    timestamp = record.timestamp.isoformat() if record.timestamp else ""
    identity = f"dev:{record.activity_type.value}:{record.item_id}:{timestamp}"
    return ReportEvent(
        subsystem=SUBSYSTEM_DEVELOPER,
        event_type=record.activity_type.value,
        severity=severity,
        title=f"{label}: {title}" if title else label,
        description="",
        repository=record.repository,
        url=record.url,
        identity=identity,
        timestamp=timestamp,
        source="developer",
        metadata={"item_id": record.item_id, "state": record.state},
    )


def report_event_from_opportunity(opportunity: Any) -> ReportEvent | None:
    """Convert an OSS Opportunity into an INFORMATION ReportEvent."""
    repo = str(getattr(opportunity, "repo_full_name", "") or "")
    number = getattr(opportunity, "issue_number", 0) or 0
    if not repo and not number:
        return None
    title = str(getattr(opportunity, "title", "") or f"{repo}#{number}")
    url = str(getattr(opportunity, "html_url", "") or "")
    identity = f"{repo}#{number}" if repo else str(number)
    return ReportEvent(
        subsystem=SUBSYSTEM_OSS,
        event_type="opportunity",
        severity=Severity.INFORMATION,
        title=f"OSS opportunity: {title}",
        repository=repo,
        url=url,
        identity=identity,
        source="oss",
    )
