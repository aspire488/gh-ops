"""Tests for monitor → ReportEvent conversion.

Covers the hard silence rules (successful CI produces no event, recoveries
surface as RESOLVED) and GH-OPS · SYSTEM routing for gh-ops's own workflow runs.
"""
from __future__ import annotations

from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.reporting.convert import MONITORING_SUBSYSTEMS, report_event_from_monitor
from src.reporting.model import (
    SUBSYSTEM_CI,
    SUBSYSTEM_LABEL,
    SUBSYSTEM_SYSTEM,
    Severity,
)

_EVALUATED_AT = "2026-09-24T12:00:00+00:00"


class _Event:
    """Minimal event payload stand-in with a workflow_run-shaped current."""

    def __init__(self, **fields):
        self.current = fields


def _ci_result(
    status: MonitorStatus,
    *,
    metadata: dict | None = None,
    resource: str = "owner/repo/run/9",
    events: tuple = (),
) -> MonitorResult:
    return MonitorResult(
        monitor="ci",
        resource=resource,
        status=status,
        summary="Workflow result",
        category=MonitorCategory.CI,
        metadata=dict(metadata or {}),
        events=tuple(events),
        evaluated_at=_EVALUATED_AT,
    )


class TestCISilence:
    def test_ci_success_is_silent(self):
        result = _ci_result(
            MonitorStatus.OK, metadata={"event_type": "success", "conclusion": "success"}
        )
        assert report_event_from_monitor(result) is None

    def test_ci_success_chained_is_silent(self):
        result = _ci_result(
            MonitorStatus.OK,
            metadata={"event_type": "success", "old_conclusion": "failure", "new_conclusion": "success"},
        )
        assert report_event_from_monitor(result) is None

    def test_ci_recovery_is_resolved(self):
        result = _ci_result(
            MonitorStatus.OK,
            metadata={"event_type": "recovery", "branch": "main"},
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.RESOLVED
        assert event.subsystem == SUBSYSTEM_CI

    def test_ci_failure_on_main_is_action_required(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure", "branch": "main", "workflow_name": "CI"},
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.ACTION_REQUIRED
        assert event.subsystem == SUBSYSTEM_CI

    def test_ci_failure_on_branch_is_important(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure", "branch": "develop"},
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.IMPORTANT

    def test_ci_failure_branch_falls_back_to_event_payload(self):
        # CHANGED-derived results carry the branch only in the run payload.
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure"},
            events=(_Event(head_branch="master", name="CI", conclusion="failure"),),
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.ACTION_REQUIRED
        assert event.metadata["branch"] == "master"
        assert event.metadata["workflow_name"] == "CI"


class TestSystemWorkflowRouting:
    def test_system_failure_classified_system(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={
                "event_type": "failure",
                "workflow_name": "Security",
                "branch": "master",
            },
            resource="aspire488/gh-ops/run/12",
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.subsystem == SUBSYSTEM_SYSTEM
        assert SUBSYSTEM_LABEL[SUBSYSTEM_SYSTEM] == "SYSTEM"
        assert SUBSYSTEM_SYSTEM in MONITORING_SUBSYSTEMS

    def test_system_success_is_silent(self):
        result = _ci_result(
            MonitorStatus.OK,
            metadata={"event_type": "success", "workflow_name": "Daily"},
            resource="aspire488/gh-ops/run/13",
        )
        assert report_event_from_monitor(result) is None

    def test_system_recovery_is_resolved_system_event(self):
        result = _ci_result(
            MonitorStatus.OK,
            metadata={"event_type": "recovery", "workflow_name": "Monitoring"},
            resource="aspire488/gh-ops/run/14",
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.RESOLVED
        assert event.subsystem == SUBSYSTEM_SYSTEM

    def test_workflow_name_from_event_payload_classifies_system(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure", "branch": "main"},
            resource="aspire488/gh-ops/run/15",
            events=(_Event(name="Security", head_branch="main"),),
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.subsystem == SUBSYSTEM_SYSTEM

    def test_unlisted_workflow_in_ghops_repo_stays_ci(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure", "workflow_name": "Deploy"},
            resource="aspire488/gh-ops/run/16",
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.subsystem == SUBSYSTEM_CI

    def test_system_workflow_name_on_other_repo_stays_ci(self):
        result = _ci_result(
            MonitorStatus.ALERT,
            metadata={"event_type": "failure", "workflow_name": "Security"},
            resource="aspire488/Kio/run/3",
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.subsystem == SUBSYSTEM_CI
