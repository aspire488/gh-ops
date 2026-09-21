"""Tests for src.monitors.repository (Phase 4 repository monitor)."""
import pytest
from src.core.models import (
    CurrentState,
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors.repository import (
    evaluate_repository_event,
    monitor_repositories,
)


def _make_event(
    event_type: EventType,
    resource_id: str = "test/repo",
    changes: tuple[FieldChange, ...] = (),
    current: dict = None,
) -> ResourceEvent:
    return ResourceEvent(
        event_type=event_type,
        resource_type="repos",
        resource_id=resource_id,
        changes=changes,
        current=current,
    )


def _make_state(
    repos: dict = None,
    repos_status: str = "success",
) -> CurrentState:
    return CurrentState(
        resources={"repos": repos or {}},
        update_log={"repos": {"status": repos_status}},
    )


class TestEvaluateRepositoryEvent:
    def test_unchanged_returns_none(self):
        event = _make_event(EventType.UNCHANGED)
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is None

    def test_new_repo(self):
        event = _make_event(
            EventType.NEW,
            current={"name": "test/repo"},
        )
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.OK
        assert result.category == MonitorCategory.REPOSITORY
        assert "New repository tracked" in result.summary

    def test_removed_repo(self):
        event = _make_event(EventType.REMOVED)
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "removed from tracking" in result.summary

    def test_changed_alert_fields(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="archived", before=False, after=True),
                FieldChange(field="description", before="old", after="new"),
            ),
        )
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT
        assert "Critical repository change" in result.summary
        assert "archived" in result.summary

    def test_changed_info_fields(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="stargazers_count", before=10, after=20),
                FieldChange(field="description", before="old", after="new"),
            ),
        )
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "metadata changed" in result.summary

    def test_changed_volatile_fields_returns_none(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="updated_at", before="2024-01-01", after="2024-01-02"),
                FieldChange(field="pushed_at", before="2024-01-01", after="2024-01-02"),
            ),
        )
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is None

    def test_changed_empty_changes_returns_none(self):
        event = _make_event(EventType.CHANGED, changes=())
        result = evaluate_repository_event(event, {"enabled": True})
        assert result is None

    def test_disabled_monitor_returns_none(self):
        event = _make_event(
            EventType.NEW,
            current={"name": "test/repo"},
        )
        result = evaluate_repository_event(event, {"enabled": False})
        assert result is None


class TestMonitorRepositories:
    def test_disabled_returns_empty(self):
        state = _make_state(repos={"test/repo": {"name": "test/repo"}})
        results = monitor_repositories(state, {"enabled": False})
        assert results == []

    def test_empty_repos_returns_empty(self):
        state = _make_state(repos={})
        results = monitor_repositories(state, {"enabled": True})
        assert results == []

    def test_collection_failure_returns_error(self):
        state = _make_state(repos_status="failure")
        results = monitor_repositories(state, {"enabled": True})
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ERROR
        assert "collection failed" in results[0].summary.lower()

    def test_successful_collection_returns_empty(self):
        state = _make_state(repos={"test/repo": {"name": "test/repo"}})
        results = monitor_repositories(state, {"enabled": True})
        assert results == []
