"""Tests for src.monitors.ci (Phase 4 CI monitor)."""
import pytest
from src.core.models import (
    CurrentState,
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors.ci import evaluate_workflow_event, monitor_ci


def _make_event(
    event_type: EventType,
    resource_id: str = "workflow:build",
    changes: tuple[FieldChange, ...] = (),
    current: dict = None,
) -> ResourceEvent:
    return ResourceEvent(
        event_type=event_type,
        resource_type="workflows",
        resource_id=resource_id,
        changes=changes,
        current=current,
    )


def _make_state(workflows_status: str = "success") -> CurrentState:
    return CurrentState(
        resources={"workflows": {}},
        update_log={"workflows": {"status": workflows_status}},
    )


class TestEvaluateWorkflowEvent:
    def test_unchanged_returns_none(self):
        event = _make_event(EventType.UNCHANGED)
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is None

    def test_new_workflow_failure(self):
        event = _make_event(
            EventType.NEW,
            current={"name": "build", "conclusion": "failure"},
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT
        assert result.category == MonitorCategory.CI
        assert "failed" in result.summary.lower()
        assert result.metadata["conclusion"] == "failure"

    def test_new_workflow_success(self):
        event = _make_event(
            EventType.NEW,
            current={"name": "build", "conclusion": "success"},
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.OK
        assert "completed successfully" in result.summary

    def test_new_workflow_other_status(self):
        event = _make_event(
            EventType.NEW,
            current={"name": "build", "conclusion": "in_progress"},
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert result.metadata["event_type"] == "status_change"

    def test_removed_workflow_returns_none(self):
        event = _make_event(EventType.REMOVED)
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is None

    def test_changed_recovery(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="conclusion", before="failure", after="success"),
            ),
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "recovered" in result.summary.lower()
        assert result.metadata["event_type"] == "recovery"

    def test_changed_new_failure(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="conclusion", before="success", after="failure"),
            ),
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT
        assert "failed" in result.summary.lower()
        assert result.metadata["event_type"] == "failure"

    def test_changed_status_only(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(
                FieldChange(field="status", before="queued", after="in_progress"),
            ),
        )
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert result.metadata["event_type"] == "status_change"

    def test_changed_empty_changes_returns_none(self):
        event = _make_event(EventType.CHANGED, changes=())
        result = evaluate_workflow_event(event, {"enabled": True})
        assert result is None


class TestMonitorCi:
    def test_disabled_returns_empty(self):
        state = _make_state()
        results = monitor_ci(state, {"enabled": False})
        assert results == []

    def test_collection_failure_returns_error(self):
        state = _make_state(workflows_status="failure")
        results = monitor_ci(state, {"enabled": True})
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ERROR
        assert "collection failed" in results[0].summary.lower()

    def test_successful_collection_returns_empty(self):
        state = _make_state()
        results = monitor_ci(state, {"enabled": True})
        assert results == []
