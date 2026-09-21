"""Tests for src.monitors.release (Phase 4 release monitor)."""
import pytest
from src.core.models import (
    CurrentState,
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors.release import evaluate_release_event, monitor_releases


def _make_event(
    event_type: EventType,
    resource_id: str = "v1.0.0",
    changes: tuple[FieldChange, ...] = (),
    current: dict = None,
) -> ResourceEvent:
    return ResourceEvent(
        event_type=event_type,
        resource_type="releases",
        resource_id=resource_id,
        changes=changes,
        current=current,
    )


def _make_state(releases_status: str = "success") -> CurrentState:
    return CurrentState(
        resources={"releases": {}},
        update_log={"releases": {"status": releases_status}},
    )


class TestEvaluateReleaseEvent:
    def test_unchanged_returns_none(self):
        event = _make_event(EventType.UNCHANGED)
        result = evaluate_release_event(event, {"enabled": True})
        assert result is None

    def test_new_release(self):
        event = _make_event(
            EventType.NEW,
            current={"tag_name": "v1.0.0", "prerelease": False, "draft": False},
        )
        result = evaluate_release_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.OK
        assert result.category == MonitorCategory.RELEASE
        assert "v1.0.0" in result.summary

    def test_new_prerelease_filtered(self):
        event = _make_event(
            EventType.NEW,
            current={"tag_name": "v1.0.0-beta.1", "prerelease": True, "draft": False},
        )
        result = evaluate_release_event(
            event, {"enabled": True, "notify_prerelease": False}
        )
        assert result is None

    def test_new_prerelease_notified(self):
        event = _make_event(
            EventType.NEW,
            current={"tag_name": "v1.0.0-beta.1", "prerelease": True, "draft": False},
        )
        result = evaluate_release_event(
            event, {"enabled": True, "notify_prerelease": True}
        )
        assert result is not None
        assert "prerelease" in result.summary.lower()

    def test_new_draft_filtered(self):
        event = _make_event(
            EventType.NEW,
            current={"tag_name": "v1.0.0", "prerelease": False, "draft": True},
        )
        result = evaluate_release_event(
            event, {"enabled": True, "notify_draft": False}
        )
        assert result is None

    def test_new_draft_notified(self):
        event = _make_event(
            EventType.NEW,
            current={"tag_name": "v1.0.0", "prerelease": False, "draft": True},
        )
        result = evaluate_release_event(
            event, {"enabled": True, "notify_draft": True}
        )
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "draft" in result.summary.lower()

    def test_removed_release(self):
        event = _make_event(EventType.REMOVED)
        result = evaluate_release_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "removed" in result.summary.lower()

    def test_changed_tag_name(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(FieldChange(field="tag_name", before="v1.0.0", after="v1.1.0"),),
        )
        result = evaluate_release_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert "tag_name" in result.summary

    def test_changed_volatile_field_returns_none(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(FieldChange(field="body", before="old", after="new"),),
        )
        result = evaluate_release_event(event, {"enabled": True})
        assert result is None


class TestMonitorReleases:
    def test_disabled_returns_empty(self):
        state = _make_state()
        results = monitor_releases(state, {"enabled": False})
        assert results == []

    def test_collection_failure_returns_error(self):
        state = _make_state(releases_status="failure")
        results = monitor_releases(state, {"enabled": True})
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ERROR

    def test_successful_collection_returns_empty(self):
        state = _make_state()
        results = monitor_releases(state, {"enabled": True})
        assert results == []
