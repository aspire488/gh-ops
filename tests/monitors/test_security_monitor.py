"""Tests for src.monitors.security."""
from __future__ import annotations

from src.core.models import (
    CurrentState,
    EventType,
    FieldChange,
    ResourceEvent,
)
from src.core.monitor import MonitorCategory, MonitorStatus
from src.monitors import _collector_to_monitor, evaluate_events, run_monitors
from src.monitors.security import evaluate_security_event, monitor_security


def _make_event(
    event_type: EventType,
    resource_id: str = "octo/repo/dependabot/1",
    changes: tuple[FieldChange, ...] = (),
    current: dict | None = None,
) -> ResourceEvent:
    return ResourceEvent(
        event_type=event_type,
        resource_type="security",
        resource_id=resource_id,
        changes=changes,
        current=current,
    )


def _make_state(status: str = "success", security: dict | None = None) -> CurrentState:
    return CurrentState(
        resources={"security": security or {}},
        update_log={"security": {"status": status}},
    )


class TestEvaluateSecurityEvent:
    def test_unchanged_returns_none(self):
        assert evaluate_security_event(_make_event(EventType.UNCHANGED), {}) is None

    def test_new_critical_is_alert(self):
        event = _make_event(
            EventType.NEW,
            current={
                "severity": "critical",
                "package_name": "lodash",
                "summary": "Prototype pollution",
                "state": "open",
            },
        )
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT
        assert result.category == MonitorCategory.SECURITY
        assert result.metadata["severity"] == "critical"
        assert "lodash" in result.summary

    def test_new_high_is_alert_by_default(self):
        event = _make_event(EventType.NEW, current={"severity": "high", "state": "open"})
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT

    def test_new_medium_is_changed_not_alert(self):
        event = _make_event(EventType.NEW, current={"severity": "medium", "state": "open"})
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert result.metadata["alertable"] is False

    def test_new_closed_alert_ignored(self):
        event = _make_event(EventType.NEW, current={"severity": "critical", "state": "dismissed"})
        assert evaluate_security_event(event, {"enabled": True}) is None

    def test_custom_alert_severities(self):
        event = _make_event(EventType.NEW, current={"severity": "medium", "state": "open"})
        result = evaluate_security_event(
            event, {"enabled": True, "alert_severities": ["medium"]}
        )
        assert result is not None
        assert result.status == MonitorStatus.ALERT

    def test_removed_is_changed(self):
        event = _make_event(EventType.REMOVED)
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED
        assert result.metadata["event_type"] == "alert_removed"

    def test_changed_state_to_open_at_critical_is_alert(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(FieldChange(field="state", before="dismissed", after="open"),),
            current={"severity": "critical", "state": "open"},
        )
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.ALERT

    def test_changed_severity_only_is_changed(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(FieldChange(field="severity", before="low", after="high"),),
            current={"severity": "high", "state": "open"},
        )
        result = evaluate_security_event(event, {"enabled": True})
        assert result is not None
        assert result.status == MonitorStatus.CHANGED

    def test_changed_irrelevant_field_returns_none(self):
        event = _make_event(
            EventType.CHANGED,
            changes=(FieldChange(field="html_url", before="a", after="b"),),
            current={"severity": "high", "state": "open"},
        )
        assert evaluate_security_event(event, {"enabled": True}) is None


class TestMonitorSecurity:
    def test_disabled_returns_empty(self):
        assert monitor_security(_make_state(), {"enabled": False}) == []

    def test_collection_failure_is_error(self):
        state = _make_state(status="failure")
        results = monitor_security(state, {"enabled": True})
        assert len(results) == 1
        assert results[0].status == MonitorStatus.ERROR
        assert results[0].category == MonitorCategory.SECURITY

    def test_success_returns_empty_batch(self):
        assert monitor_security(_make_state(), {"enabled": True}) == []


class TestRegistryWiring:
    def test_security_collector_maps_to_security_monitor(self):
        assert _collector_to_monitor("security") == "security"

    def test_evaluate_events_routes_security(self):
        event = _make_event(
            EventType.NEW,
            current={"severity": "critical", "package_name": "x", "state": "open"},
        )
        config = {"monitors": {"security": {"enabled": True}}}
        results = evaluate_events(_make_state(), config, events=[event])
        assert len(results) == 1
        assert results[0].monitor == "security"

    def test_evaluate_events_skips_disabled_security(self):
        event = _make_event(
            EventType.NEW,
            current={"severity": "critical", "state": "open"},
        )
        config = {"monitors": {"security": {"enabled": False}}}
        assert evaluate_events(_make_state(), config, events=[event]) == []

    def test_run_monitors_includes_security(self):
        config = {"monitors": {"security": {"enabled": True}}}
        results = run_monitors(_make_state(status="failure"), config)
        assert any(r.monitor == "security" for r in results)
