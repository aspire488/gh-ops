"""Tests for src.monitors.__init__ (Phase 4 monitor registry)."""
from src.core.models import (
    CurrentState,
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors import (
    _collector_to_monitor,
    evaluate_events,
    run_monitors,
    summarize_results,
)


def _make_event(
    event_type: EventType,
    resource_type: str = "repos",
    resource_id: str = "test/repo",
    changes: tuple[FieldChange, ...] = (),
    current: dict = None,
) -> ResourceEvent:
    return ResourceEvent(
        event_type=event_type,
        resource_type=resource_type,
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


class TestCollectorToMonitor:
    def test_repos_maps_to_repository(self):
        assert _collector_to_monitor("repos") == "repository"

    def test_issues_maps_to_repository(self):
        assert _collector_to_monitor("issues") == "repository"

    def test_pulls_maps_to_repository(self):
        assert _collector_to_monitor("pulls") == "repository"

    def test_releases_maps_to_release(self):
        assert _collector_to_monitor("releases") == "release"

    def test_workflows_maps_to_ci(self):
        assert _collector_to_monitor("workflows") == "ci"

    def test_security_maps_to_security(self):
        assert _collector_to_monitor("security") == "security"

    def test_unknown_maps_to_self(self):
        assert _collector_to_monitor("unknown") == "unknown"


class TestEvaluateEvents:
    def test_empty_events_returns_empty(self):
        state = _make_state()
        results = evaluate_events(state, {"monitors": {}}, events=[])
        assert results == []

    def test_repo_event_evaluated(self):
        event = _make_event(
            EventType.CHANGED,
            resource_type="repos",
            changes=(FieldChange(field="archived", before=False, after=True),),
        )
        state = _make_state()
        config = {"monitors": {"repository": {"enabled": True}}}
        results = evaluate_events(state, config, events=[event])
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ALERT

    def test_disabled_monitor_skipped(self):
        event = _make_event(
            EventType.CHANGED,
            resource_type="repos",
            changes=(FieldChange(field="archived", before=False, after=True),),
        )
        state = _make_state()
        config = {"monitors": {"repository": {"enabled": False}}}
        results = evaluate_events(state, config, events=[event])
        assert results == []

    def test_unknown_collector_skipped(self):
        event = _make_event(EventType.NEW, resource_type="unknown")
        state = _make_state()
        results = evaluate_events(state, {"monitors": {}}, events=[event])
        assert results == []


class TestRunMonitors:
    def test_disabled_repository_monitor(self):
        state = _make_state()
        config = {"monitors": {"repository": {"enabled": False}}}
        results = run_monitors(state, config)
        assert results == []

    def test_enabled_repository_monitor(self):
        state = _make_state(repos={"test/repo": {"name": "test/repo"}})
        config = {"monitors": {"repository": {"enabled": True}}}
        results = run_monitors(state, config)
        assert results == []

    def test_endpoint_monitor_disabled_by_default(self):
        state = _make_state()
        config = {"monitors": {"endpoint": {"enabled": False}}}
        results = run_monitors(state, config)
        assert results == []

    def test_monitor_execution_error(self):
        state = _make_state()
        config = {"monitors": {"repository": {"enabled": True}}}
        # This should not raise, it should return an error result
        results = run_monitors(state, config)
        assert isinstance(results, list)


class TestSummarizeResults:
    def test_empty_results(self):
        summary = summarize_results([])
        assert summary["total"] == 0
        assert summary["by_status"] == {}
        assert summary["by_category"] == {}
        assert summary["alerts"] == []
        assert summary["errors"] == []

    def test_mixed_results(self):
        from src.core.monitor import MonitorResult
        results = [
            MonitorResult(
                monitor="test",
                resource="repo1",
                status=MonitorStatus.OK,
                summary="OK",
                category=MonitorCategory.REPOSITORY,
            ),
            MonitorResult(
                monitor="test",
                resource="repo2",
                status=MonitorStatus.ALERT,
                summary="Alert",
                category=MonitorCategory.REPOSITORY,
            ),
            MonitorResult(
                monitor="test",
                resource="repo3",
                status=MonitorStatus.ERROR,
                summary="Error",
                category=MonitorCategory.CI,
            ),
        ]
        summary = summarize_results(results)
        assert summary["total"] == 3
        assert summary["by_status"]["ok"] == 1
        assert summary["by_status"]["alert"] == 1
        assert summary["by_status"]["error"] == 1
        assert summary["by_category"]["repository"] == 2
        assert summary["by_category"]["ci"] == 1
        assert len(summary["alerts"]) == 1
        assert len(summary["errors"]) == 1
