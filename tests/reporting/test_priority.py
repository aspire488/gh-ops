"""Tests for Priority (distinct from Severity) and event_block context/links."""
from __future__ import annotations

from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.reporting.aggregate import prioritize
from src.reporting.builders import build_report, event_block, event_context
from src.reporting.convert import report_event_from_monitor
from src.reporting.model import (
    PRIORITY_LABEL,
    Priority,
    ReportEvent,
    Severity,
    default_priority,
    parse_priority,
)


def _event(
    *,
    subsystem: str = "ci",
    severity: Severity = Severity.IMPORTANT,
    priority: Priority | None = None,
    title: str = "CI failed",
    repository: str = "owner/repo",
    url: str = "",
    identity: str = "ci:owner/repo/run/1:failure",
    timestamp: str = "2026-09-24T12:00:00+00:00",
    metadata: dict | None = None,
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="failure",
        severity=severity,
        title=title,
        repository=repository,
        url=url,
        identity=identity,
        timestamp=timestamp,
        source=subsystem,
        metadata=metadata or {},
        priority=priority,
    )


class TestPriorityModel:
    def test_priority_is_distinct_from_severity(self):
        assert Priority.P0 != Severity.ACTION_REQUIRED
        assert default_priority(Severity.ACTION_REQUIRED) is Priority.P0
        assert default_priority(Severity.IMPORTANT) is Priority.P1
        assert default_priority(Severity.INFORMATION) is Priority.P2
        assert default_priority(Severity.RESOLVED) is Priority.P3

    def test_explicit_priority_overrides_default(self):
        demoted = _event(severity=Severity.ACTION_REQUIRED, priority=Priority.P2)
        assert demoted.effective_priority is Priority.P2
        promoted = _event(severity=Severity.INFORMATION, priority=Priority.P0)
        assert promoted.effective_priority is Priority.P0

    def test_parse_priority(self):
        assert parse_priority("p1") is Priority.P1
        assert parse_priority("P0") is Priority.P0
        assert parse_priority(None) is None
        assert parse_priority("nope") is None
        assert parse_priority("nope", Priority.P3) is Priority.P3

    def test_roundtrip_dict_preserves_explicit_priority(self):
        original = _event(priority=Priority.P2)
        restored = ReportEvent.from_dict(original.to_dict())
        assert restored.priority is Priority.P2
        assert restored.effective_priority is Priority.P2

    def test_dict_omits_absent_priority(self):
        data = _event().to_dict()
        assert "priority" not in data
        assert ReportEvent.from_dict(data).priority is None

    def test_priority_labels(self):
        assert PRIORITY_LABEL[Priority.P0] == "P0"
        assert PRIORITY_LABEL[Priority.P3] == "P3"


class TestPrioritizeWithPriority:
    def test_priority_sorts_before_severity(self):
        """A P0 important event outranks a P1 action-required event."""
        p0_important = _event(
            identity="a",
            severity=Severity.IMPORTANT,
            priority=Priority.P0,
        )
        p1_action = _event(
            identity="b",
            severity=Severity.ACTION_REQUIRED,
            priority=Priority.P1,
        )
        ordered = prioritize([p1_action, p0_important])
        assert [e.identity for e in ordered] == ["a", "b"]

    def test_equal_priority_falls_back_to_severity(self):
        action = _event(identity="a", severity=Severity.ACTION_REQUIRED)
        info = _event(identity="b", severity=Severity.INFORMATION)
        ordered = prioritize([info, action])
        assert [e.identity for e in ordered] == ["a", "b"]

    def test_same_priority_and_severity_sorts_by_timestamp(self):
        early = _event(identity="a", timestamp="2026-09-24T10:00:00+00:00")
        late = _event(identity="b", timestamp="2026-09-24T12:00:00+00:00")
        ordered = prioritize([late, early])
        assert [e.identity for e in ordered] == ["a", "b"]


class TestEventBlockContextAndLinks:
    def test_block_includes_priority_subsystem_and_link(self):
        event = _event(
            severity=Severity.ACTION_REQUIRED,
            url="https://github.com/owner/repo/actions/runs/1",
            metadata={"branch": "main", "workflow_name": "CI", "conclusion": "failure"},
        )
        block = event_block(event)
        assert block.startswith("• [P0] owner/repo · CI")
        assert "CI failed" in block
        assert "branch: main" in block
        assert "workflow: CI" in block
        assert "conclusion: failure" in block
        assert "https://github.com/owner/repo/actions/runs/1" in block

    def test_block_uses_explicit_priority_not_severity(self):
        event = _event(severity=Severity.ACTION_REQUIRED, priority=Priority.P3)
        assert event_block(event).startswith("• [P3]")

    def test_block_without_title_still_has_header(self):
        event = _event(title="")
        lines = event_block(event).splitlines()
        assert lines[0].startswith("• [P1] owner/repo · CI")

    def test_context_excludes_raw_status_and_resource(self):
        event = _event(
            metadata={
                "resource": "owner/repo",
                "other_resource": "x",
                "branch": "",
                "package_name": "requests",
                "severity": "high",
                "status": "alert",
            },
        )
        context = event_context(event)
        assert "package: requests" in context
        assert "alert severity: high" in context
        # Raw monitor bookkeeping never reaches the user-facing message.
        assert not any(line.startswith("resource:") for line in context)
        assert not any(line.startswith("status:") for line in context)

    def test_build_report_groups_and_uses_event_block(self):
        action = _event(
            identity="a",
            severity=Severity.ACTION_REQUIRED,
            title="Broken",
            url="https://example.com/a",
        )
        info = _event(identity="b", severity=Severity.INFORMATION, title="FYI")
        built = build_report([info, action], report_name="ALERTS")
        assert built is not None
        title, blocks = built
        assert "🔴" in title
        assert "ACTION REQUIRED" in blocks
        joined = "\n".join(blocks)
        assert "• [P0] owner/repo · CI" in joined
        assert "https://example.com/a" in joined
        assert "• [P2] owner/repo · CI" in joined


class TestConvertContext:
    def test_ci_failure_pulls_branch_and_run_url(self):
        class _Event:
            current = {
                "html_url": "https://github.com/owner/repo/actions/runs/9",
                "head_branch": "main",
                "name": "CI",
                "conclusion": "failure",
            }

        result = MonitorResult(
            monitor="ci",
            resource="owner/repo/run/9",
            status=MonitorStatus.ALERT,
            summary="Workflow CI failed",
            category=MonitorCategory.CI,
            events=(_Event(),),
            metadata={"event_type": "failure", "workflow_name": "CI", "branch": "main"},
        )
        event = report_event_from_monitor(result)
        assert event is not None
        assert event.severity is Severity.ACTION_REQUIRED
        assert event.url.endswith("/runs/9")
        assert event.metadata["branch"] == "main"
        assert event.metadata["workflow_name"] == "CI"
        block = event_block(event)
        assert "branch: main" in block
        assert "https://github.com/owner/repo/actions/runs/9" in block
