"""Repository lifecycle and idempotent state migration.

Tracks each repository as CONFIGURED → ACTIVE → RETIRED against
``config/repositories.yml`` (the source of truth) and prunes stale
repository-scoped items from Phase 3 state before diffing.

Design rules:
- Config removal is a *retirement*, not a GitHub repository deletion.
- Migration is idempotent: the same inputs always yield the same state.
- Global resource keys are never pruned: ``oss_opportunities``,
  ``security_notified``, ``reporting_events``, ``user``, and the lifecycle
  map itself.
- Retirement does not synthesize ResourceEvents, so monitors never see
  fake REMOVED events for config-driven drops.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from src.core.models import CurrentState
from src.utils.logging import get_logger

logger = get_logger("core.lifecycle")

#: State resource key holding the lifecycle map (repo → status record).
REPOSITORY_LIFECYCLE_KEY = "repository_lifecycle"

#: Resource types whose item keys are scoped to a single repository.
PER_REPO_RESOURCES = frozenset(
    {"repos", "issues", "pulls", "releases", "workflows", "security"}
)

#: Resource keys that must survive every migration untouched.
GLOBAL_RESOURCE_KEYS = frozenset(
    {
        "oss_opportunities",
        "security_notified",
        "reporting_events",
        "user",
        REPOSITORY_LIFECYCLE_KEY,
    }
)

STATUS_CONFIGURED = "configured"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


def repository_of_key(key: str) -> str:
    """Extract ``owner/repo`` from a per-repository state item key.

    Handles the collector key shapes:
        owner/repo
        owner/repo#123
        owner/repo@v1.2.3
        owner/repo/run/456
        owner/repo/dependabot/7
        owner/repo/codescan/8
    """
    base = key.split("#", 1)[0].split("@", 1)[0]
    parts = base.split("/")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f"{parts[0]}/{parts[1]}"
    return base


def discover_repository_names(state: CurrentState) -> set[str]:
    """All ``owner/repo`` names present in per-repository resources."""
    found: set[str] = set()
    for resource in PER_REPO_RESOURCES:
        items = state.resources.get(resource) or {}
        for key in items:
            found.add(repository_of_key(key))
    return found


def load_lifecycle(state: CurrentState) -> dict[str, dict]:
    """Read the lifecycle map from state (always a plain dict of dicts)."""
    raw = state.resources.get(REPOSITORY_LIFECYCLE_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile_repository_lifecycle(
    state: CurrentState,
    configured: Iterable[str],
) -> tuple[CurrentState, list[str]]:
    """Reconcile lifecycle against config and prune stale repo-scoped items.

    Transitions:
        in config + present in state  → active
        in config + absent from state → configured
        not in config                 → retired (items pruned)

    Args:
        state: Previous current state (may contain stale repositories).
        configured: ``owner/repo`` names from ``config/repositories.yml``.

    Returns:
        ``(migrated_state, newly_retired)`` where ``newly_retired`` lists
        repositories that transitioned into retired on this call only.
        Running twice with the same inputs yields an empty second list
        and an equal state (idempotent).
    """
    configured_set = {name for name in configured if name}
    lifecycle = load_lifecycle(state)
    present = discover_repository_names(state)

    newly_retired: list[str] = []
    next_lifecycle: dict[str, dict] = {}

    # Preserve prior non-retired history for re-configured repos; rebuild map.
    candidates = set(lifecycle) | present | configured_set
    for repo in sorted(candidates):
        prior = lifecycle.get(repo) or {}
        if repo in configured_set:
            status = STATUS_ACTIVE if repo in present else STATUS_CONFIGURED
            entry: dict = {"status": status}
            if prior.get("since"):
                entry["since"] = prior["since"]
            else:
                entry["since"] = _utc_now()
            if prior.get("retired_at"):
                # Re-configured after retirement: clear retirement timestamp.
                entry["reconfigured_at"] = _utc_now()
            next_lifecycle[repo] = entry
        else:
            if prior.get("status") != STATUS_RETIRED:
                newly_retired.append(repo)
                next_lifecycle[repo] = {
                    "status": STATUS_RETIRED,
                    "retired_at": _utc_now(),
                    "reason": "removed_from_config",
                    **({"since": prior["since"]} if prior.get("since") else {}),
                }
            else:
                # Already retired: keep the original retirement record.
                next_lifecycle[repo] = dict(prior)

    new_resources = dict(state.resources)
    pruned_counts: dict[str, int] = {}
    for resource in PER_REPO_RESOURCES:
        items = new_resources.get(resource)
        if not items:
            continue
        kept = {
            key: value
            for key, value in items.items()
            if repository_of_key(key) in configured_set
        }
        removed = len(items) - len(kept)
        if removed:
            pruned_counts[resource] = removed
            new_resources[resource] = kept

    new_resources[REPOSITORY_LIFECYCLE_KEY] = next_lifecycle

    migrated = CurrentState(
        resources=new_resources,
        etags=dict(state.etags),
        last_updated=state.last_updated,
        schema_version=state.schema_version,
        update_log=dict(state.update_log),
    )

    if newly_retired:
        logger.info(
            "Retired %d repositories no longer in config: %s",
            len(newly_retired),
            ", ".join(newly_retired),
        )
    if pruned_counts:
        logger.info(
            "Pruned stale repository state: %s",
            ", ".join(f"{k}=-{v}" for k, v in sorted(pruned_counts.items())),
        )

    return migrated, newly_retired


def active_repository_names(state: CurrentState) -> list[str]:
    """Configured repositories currently marked active, sorted."""
    return sorted(
        repo
        for repo, entry in load_lifecycle(state).items()
        if entry.get("status") == STATUS_ACTIVE
    )


def retired_repository_names(state: CurrentState) -> list[str]:
    """Repositories marked retired, sorted."""
    return sorted(
        repo
        for repo, entry in load_lifecycle(state).items()
        if entry.get("status") == STATUS_RETIRED
    )
