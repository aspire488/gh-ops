"""Tests for repository lifecycle and idempotent state migration."""
from __future__ import annotations

from src.core.events import diff_resources
from src.core.lifecycle import (
    REPOSITORY_LIFECYCLE_KEY,
    STATUS_ACTIVE,
    STATUS_CONFIGURED,
    STATUS_RETIRED,
    active_repository_names,
    discover_repository_names,
    load_lifecycle,
    reconcile_repository_lifecycle,
    repository_of_key,
    retired_repository_names,
)
from src.core.models import CollectorResult, CollectorStatus, CurrentState, Snapshot
from src.jobs.pipeline import apply_and_diff


def _state_with_repos() -> CurrentState:
    return CurrentState(
        resources={
            "repos": {
                "aspire488/gh-ops": {"full_name": "aspire488/gh-ops"},
                "aspire488/old-repo": {"full_name": "aspire488/old-repo"},
            },
            "issues": {
                "aspire488/gh-ops#1": {"number": 1},
                "aspire488/old-repo#9": {"number": 9},
            },
            "pulls": {
                "aspire488/gh-ops#2": {"number": 2},
                "aspire488/old-repo#8": {"number": 8},
            },
            "releases": {
                "aspire488/gh-ops@v1": {"tag_name": "v1"},
                "aspire488/old-repo@v0": {"tag_name": "v0"},
            },
            "workflows": {
                "aspire488/gh-ops/run/1": {"id": 1},
                "aspire488/old-repo/run/2": {"id": 2},
            },
            "security": {
                "aspire488/gh-ops/dependabot/3": {"id": 3},
                "aspire488/old-repo/dependabot/4": {"id": 4},
            },
            "oss_opportunities": {"someone/repo#1": {"notified": True}},
            "security_notified": {"alert-1": {"notified": True}},
            "reporting_events": {"ci:run:1": {"delivered": True}},
            "user": {"aspire488": {"login": "aspire488"}},
        },
        etags={"repos": 'W/"abc"'},
        update_log={"repos": {"status": "success", "item_count": 2}},
    )


class TestRepositoryOfKey:
    def test_plain_full_name(self):
        assert repository_of_key("aspire488/gh-ops") == "aspire488/gh-ops"

    def test_issue_key(self):
        assert repository_of_key("aspire488/gh-ops#42") == "aspire488/gh-ops"

    def test_release_key(self):
        assert repository_of_key("aspire488/gh-ops@v1.2.3") == "aspire488/gh-ops"

    def test_workflow_key(self):
        assert repository_of_key("aspire488/gh-ops/run/99") == "aspire488/gh-ops"

    def test_security_keys(self):
        assert repository_of_key("aspire488/gh-ops/dependabot/7") == "aspire488/gh-ops"
        assert repository_of_key("aspire488/gh-ops/codescan/8") == "aspire488/gh-ops"


class TestDiscover:
    def test_finds_all_repo_scoped_names(self):
        state = _state_with_repos()
        assert discover_repository_names(state) == {
            "aspire488/gh-ops",
            "aspire488/old-repo",
        }


class TestReconcile:
    def test_prunes_stale_repo_items_across_all_resources(self):
        state = _state_with_repos()
        migrated, retired = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )

        assert retired == ["aspire488/old-repo"]
        for resource in (
            "repos",
            "issues",
            "pulls",
            "releases",
            "workflows",
            "security",
        ):
            keys = migrated.resources[resource]
            assert all(
                repository_of_key(k) == "aspire488/gh-ops" for k in keys
            ), f"{resource} still holds stale keys: {list(keys)}"
            assert "aspire488/old-repo" not in " ".join(keys)

        assert "aspire488/gh-ops" in migrated.resources["repos"]
        assert "aspire488/old-repo" not in migrated.resources["repos"]

    def test_preserves_global_keys(self):
        state = _state_with_repos()
        migrated, _ = reconcile_repository_lifecycle(state, ["aspire488/gh-ops"])

        assert migrated.resources["oss_opportunities"] == {
            "someone/repo#1": {"notified": True}
        }
        assert migrated.resources["security_notified"] == {
            "alert-1": {"notified": True}
        }
        assert migrated.resources["reporting_events"] == {
            "ci:run:1": {"delivered": True}
        }
        assert migrated.resources["user"] == {"aspire488": {"login": "aspire488"}}

    def test_preserves_etags_and_update_log(self):
        state = _state_with_repos()
        migrated, _ = reconcile_repository_lifecycle(state, ["aspire488/gh-ops"])

        assert migrated.etags == state.etags
        assert migrated.update_log == state.update_log

    def test_is_idempotent(self):
        state = _state_with_repos()
        first, retired1 = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )
        second, retired2 = reconcile_repository_lifecycle(
            first, ["aspire488/gh-ops"]
        )

        assert retired1 == ["aspire488/old-repo"]
        assert retired2 == []
        assert second.resources == first.resources
        assert second.etags == first.etags
        assert second.update_log == first.update_log

    def test_lifecycle_statuses(self):
        state = _state_with_repos()
        migrated, _ = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops", "aspire488/brand-new"]
        )

        lifecycle = load_lifecycle(migrated)
        assert lifecycle["aspire488/gh-ops"]["status"] == STATUS_ACTIVE
        assert lifecycle["aspire488/brand-new"]["status"] == STATUS_CONFIGURED
        assert lifecycle["aspire488/old-repo"]["status"] == STATUS_RETIRED

        assert active_repository_names(migrated) == ["aspire488/gh-ops"]
        assert retired_repository_names(migrated) == ["aspire488/old-repo"]

    def test_readding_retired_repo_clears_retirement(self):
        state = _state_with_repos()
        once, _ = reconcile_repository_lifecycle(state, ["aspire488/gh-ops"])
        twice, newly = reconcile_repository_lifecycle(
            once, ["aspire488/gh-ops", "aspire488/old-repo"]
        )

        assert newly == []
        lifecycle = load_lifecycle(twice)
        assert lifecycle["aspire488/old-repo"]["status"] == STATUS_CONFIGURED
        assert "retired_at" not in lifecycle["aspire488/old-repo"]

    def test_empty_state_with_config_marks_configured(self):
        migrated, retired = reconcile_repository_lifecycle(
            CurrentState(), ["aspire488/gh-ops"]
        )
        assert retired == []
        assert load_lifecycle(migrated)["aspire488/gh-ops"]["status"] == (
            STATUS_CONFIGURED
        )

    def test_no_synthetic_events_for_config_retirement(self):
        """Pruning previous state before diff must not emit REMOVED events."""
        state = _state_with_repos()
        previous, _ = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )
        # Snapshot only contains the still-configured repo (as collection would).
        snapshot = Snapshot(
            results={
                "repos": CollectorResult(
                    collector="repos",
                    status=CollectorStatus.SUCCESS,
                    items={"aspire488/gh-ops": {"full_name": "aspire488/gh-ops"}},
                    item_count=1,
                ),
                "issues": CollectorResult(
                    collector="issues",
                    status=CollectorStatus.SUCCESS,
                    items={"aspire488/gh-ops#1": {"number": 1}},
                    item_count=1,
                ),
            }
        )
        _new_state, events = apply_and_diff(previous, snapshot)
        removed = [e for e in events if e.event_type.value == "removed"]
        assert removed == []

    def test_stale_repo_absent_after_next_collection_diff(self):
        """Mandate test: old repos are gone from the next monitoring state."""
        state = _state_with_repos()
        previous, _ = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )
        snapshot = Snapshot(
            results={
                "repos": CollectorResult(
                    collector="repos",
                    status=CollectorStatus.SUCCESS,
                    items={"aspire488/gh-ops": {"full_name": "aspire488/gh-ops"}},
                    item_count=1,
                ),
            }
        )
        new_state, _events = apply_and_diff(previous, snapshot)

        assert "aspire488/old-repo" not in new_state.resources.get("repos", {})
        assert discover_repository_names(new_state) == {"aspire488/gh-ops"}
        # Lifecycle map survives apply_snapshot (not a collector key).
        assert REPOSITORY_LIFECYCLE_KEY in new_state.resources

    def test_failed_collector_preserves_pruned_previous(self):
        state = _state_with_repos()
        previous, _ = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )
        snapshot = Snapshot(
            results={
                "repos": CollectorResult(
                    collector="repos",
                    status=CollectorStatus.FAILURE,
                    items=None,
                    error="boom",
                    item_count=0,
                ),
            }
        )
        new_state, events = apply_and_diff(previous, snapshot)

        # Failure preserves previous (already pruned) items — no stale return.
        assert "aspire488/old-repo" not in new_state.resources.get("repos", {})
        assert new_state.update_log["repos"]["status"] == "failure"
        removed = [e for e in events if e.event_type.value == "removed"]
        assert removed == []


class TestDiffAfterMigration:
    def test_diff_resources_on_migrated_previous_is_clean(self):
        state = _state_with_repos()
        previous, _ = reconcile_repository_lifecycle(
            state, ["aspire488/gh-ops"]
        )
        events = diff_resources(
            resource_type="repos",
            previous_items=previous.resources["repos"],
            current_items=previous.resources["repos"],
        )
        assert events == ()
