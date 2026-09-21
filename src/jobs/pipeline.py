"""Shared plumbing that sequences the existing gh-ops subsystems.

This module deliberately contains **no business logic of its own**. It only
composes the public APIs that Phases 1-7 already provide:

    GitHubClient + collectors   (Phase 2)   → fetch
    apply_snapshot              (Phase 3)   → deterministic state
    diff_resources              (Phase 3)   → events
    run_monitors                (Phase 4)   → monitor results
    run_hunter                  (Phase 5)   → OSS opportunities
    extract_activity/report     (Phase 6)   → developer report
    notify_*                    (Phase 7)   → delivery

Nothing here re-implements intelligence, scoring, state diffing, or
formatting. If a behaviour is not already owned by a Phase 1-7 module, it does
not belong in this file.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from src.core.config import Config
from src.core.events import diff_resources
from src.core.models import (
    CollectorResult,
    CollectorStatus,
    CurrentState,
    ResourceEvent,
    Snapshot,
)
from src.core.state import apply_snapshot, load_current_state, save_current_state
from src.github.client import GitHubClient
from src.github.collectors.issues import list_issues_for_repo
from src.github.collectors.pulls import list_pulls_for_repo
from src.github.collectors.releases import list_releases_for_repo
from src.github.collectors.repos import get_repo
from src.github.collectors.security import (
    list_code_scanning_alerts,
    list_dependabot_alerts,
)
from src.github.collectors.user import get_authenticated_user
from src.github.collectors.workflows import list_workflow_runs_for_repo
from src.utils.logging import get_logger

logger = get_logger("jobs.pipeline")

#: Resource types that require a configured repository list.
PER_REPO_RESOURCES = ("repos", "issues", "pulls", "releases", "workflows", "security")

#: All resource types the runtime can collect.
ALL_RESOURCES = PER_REPO_RESOURCES + ("user",)


# ── Configuration and client ─────────────────────────────────────


def load_runtime_config(config_dir: str | None = None) -> Config:
    """Load gh-ops configuration.

    Args:
        config_dir: Optional config directory override.

    Returns:
        Loaded Config.
    """
    return Config.load(config_dir)


def monitor_config(config: Config) -> dict[str, Any]:
    """Extract the monitor configuration block for ``run_monitors``.

    ``run_monitors`` expects ``{"monitors": {...}}``; config/monitoring.yml is
    stored under the ``monitoring`` key, so this returns the file contents.
    """
    return config.get("monitoring", default={}) or {}


def hunter_config(config: Config) -> dict[str, Any]:
    """Extract the OSS hunter configuration block."""
    return config.get("oss_hunter", default={}) or {}


def monitored_repository_names(config: Config) -> list[str]:
    """Return the configured ``owner/repo`` names, in configuration order."""
    return [f"{r.owner}/{r.repo}" for r in config.repositories]


# ── Collection ───────────────────────────────────────────────────


def _collect_across_repos(
    client: GitHubClient,
    repositories: Sequence[Any],
    resource: str,
    fetch: Callable[[GitHubClient, str], Iterable[tuple[str, dict[str, Any]]]],
) -> CollectorResult:
    """Collect one resource type across every monitored repository.

    Partial failure is normal: a repository that raises is recorded and the
    remaining repositories are still collected. The collector only reports
    FAILURE when every attempted repository failed, because Phase 3 treats a
    failed collector and an empty successful collection as different things.

    Args:
        client: Authenticated GitHub client.
        repositories: Configured repositories (objects with owner/repo).
        resource: Resource type name (the state key).
        fetch: Yields ``(stable_key, payload)`` pairs for one repository.

    Returns:
        CollectorResult for this resource type.
    """
    items: dict[str, dict[str, Any]] = {}
    failed: list[str] = []

    for repository in repositories:
        full_name = f"{repository.owner}/{repository.repo}"
        try:
            for key, payload in fetch(client, full_name):
                items[key] = payload
        except Exception as exc:
            # A failed repository must not discard other repositories' data.
            failed.append(f"{full_name} ({type(exc).__name__})")
            logger.warning("Collection failed for %s/%s: %s", resource, full_name, exc)

    if not repositories:
        logger.info("No repositories configured; %s collected 0 items", resource)
        return CollectorResult(
            collector=resource,
            status=CollectorStatus.SUCCESS,
            items={},
            item_count=0,
        )

    if failed and not items:
        return CollectorResult(
            collector=resource,
            status=CollectorStatus.FAILURE,
            items=None,
            error=f"all repositories failed: {'; '.join(failed)}",
            item_count=0,
        )

    if failed:
        logger.warning(
            "%s partially collected: %d repositories failed (%s)",
            resource,
            len(failed),
            "; ".join(failed),
        )

    return CollectorResult(
        collector=resource,
        status=CollectorStatus.SUCCESS,
        items=items,
        item_count=len(items),
    )


def _fetch_repos(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    owner, repo = full_name.split("/", 1)
    model = get_repo(client, owner, repo)
    return [(model.full_name or full_name, model.to_dict())]


def _fetch_issues(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{full_name}#{issue.number}", issue.to_dict())
        for issue in list_issues_for_repo(client, full_name)
    ]


def _fetch_pulls(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{full_name}#{pull.number}", pull.to_dict())
        for pull in list_pulls_for_repo(client, full_name)
    ]


def _fetch_releases(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{full_name}@{release.tag_name}", release.to_dict())
        for release in list_releases_for_repo(client, full_name)
    ]


def _fetch_workflows(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{full_name}/run/{run.id}", run.to_dict())
        for run in list_workflow_runs_for_repo(client, full_name)
    ]


def _fetch_security(client: GitHubClient, full_name: str) -> list[tuple[str, dict[str, Any]]]:
    """Collect Dependabot and code-scanning alerts for one repository.

    Dependabot needs the ``security_events`` scope. When it is unavailable the
    repository is not silently reported as clean: the error propagates so the
    collector is marked as a partial failure.
    """
    owner, repo = full_name.split("/", 1)
    pairs: list[tuple[str, dict[str, Any]]] = []
    for alert in list_dependabot_alerts(client, owner, repo):
        pairs.append((f"{full_name}/dependabot/{alert.id}", alert.to_dict()))
    for alert in list_code_scanning_alerts(client, owner, repo):
        pairs.append((f"{full_name}/codescan/{alert.id}", alert.to_dict()))
    return pairs


_PER_REPO_FETCHERS: dict[str, Callable[[GitHubClient, str], list[tuple[str, dict[str, Any]]]]] = {
    "repos": _fetch_repos,
    "issues": _fetch_issues,
    "pulls": _fetch_pulls,
    "releases": _fetch_releases,
    "workflows": _fetch_workflows,
    "security": _fetch_security,
}


def collect_user(client: GitHubClient) -> CollectorResult:
    """Collect the authenticated user (not a per-repository resource)."""
    try:
        user = get_authenticated_user(client)
    except Exception as exc:
        logger.warning("Collecting the authenticated user failed: %s", exc)
        return CollectorResult(
            collector="user",
            status=CollectorStatus.FAILURE,
            items=None,
            error=f"{type(exc).__name__}",
            item_count=0,
        )

    if user is None:
        return CollectorResult(
            collector="user",
            status=CollectorStatus.SUCCESS,
            items={},
            item_count=0,
        )

    items = {user.login: user.to_dict()}
    return CollectorResult(
        collector="user",
        status=CollectorStatus.SUCCESS,
        items=items,
        item_count=len(items),
    )


def build_snapshot(
    client: GitHubClient,
    config: Config,
    resources: Sequence[str] = ALL_RESOURCES,
) -> Snapshot:
    """Collect the requested resource types into one Snapshot.

    Args:
        client: Authenticated GitHub client.
        config: Loaded configuration.
        resources: Resource type names to collect.

    Returns:
        Snapshot with one CollectorResult per requested resource type.
    """
    repositories = config.repositories
    results: dict[str, CollectorResult] = {}

    for resource in resources:
        if resource == "user":
            results[resource] = collect_user(client)
            continue

        fetch = _PER_REPO_FETCHERS.get(resource)
        if fetch is None:
            logger.warning("Unknown resource type requested: %s", resource)
            continue

        results[resource] = _collect_across_repos(client, repositories, resource, fetch)

    return Snapshot(results=results)


# ── State ────────────────────────────────────────────────────────


def apply_and_diff(
    previous: CurrentState,
    snapshot: Snapshot,
) -> tuple[CurrentState, tuple[ResourceEvent, ...]]:
    """Apply a snapshot and produce the events it represents.

    Args:
        previous: Current state before this run.
        snapshot: What was observed.

    Returns:
        Tuple of (new state, events). Events are derived with Phase 3's
        ``diff_resources``, so Phase 6 activity extraction consumes exactly what
        the state engine produced.
    """
    new_state = apply_snapshot(previous, snapshot)

    events: list[ResourceEvent] = []
    for resource in sorted(snapshot.results):
        events.extend(
            diff_resources(
                resource_type=resource,
                previous_items=previous.resources.get(resource, {}),
                current_items=new_state.resources.get(resource, {}),
            )
        )

    return new_state, tuple(events)


def persist_state(state: CurrentState) -> None:
    """Persist current state atomically through Phase 3 (no second mechanism)."""
    save_current_state(state)


def load_previous_state() -> CurrentState:
    """Load the previous state, or an empty state when there is none."""
    return load_current_state()


def authenticated_username(state: CurrentState) -> str | None:
    """Derive the authenticated login from collected state, if present."""
    users = state.resources.get("user", {})
    if len(users) == 1:
        return next(iter(users))
    return None


def state_summary(state: CurrentState) -> dict[str, Any]:
    """Deterministic summary of what state currently holds."""
    return {
        "schema_version": state.schema_version,
        "last_updated": state.last_updated,
        "resources": {name: len(items) for name, items in sorted(state.resources.items())},
        "update_log": {
            name: entry.get("status", "unknown")
            for name, entry in sorted(state.update_log.items())
        },
    }


def failed_collectors(snapshot: Snapshot) -> list[str]:
    """Names of collectors that failed in this snapshot."""
    return sorted(snapshot.failed_collectors())
