"""Tests for src.core.monitor (Phase 4 monitor result model)."""
import pytest

from src.core.models import FieldChange
from src.core.monitor import (
    MonitorCategory,
    MonitorResult,
    MonitorStatus,
    classify_changes_as_alert,
)


class TestMonitorStatus:
    def test_status_values(self):
        assert MonitorStatus.OK.value == "ok"
        assert MonitorStatus.CHANGED.value == "changed"
        assert MonitorStatus.ALERT.value == "alert"
        assert MonitorStatus.ERROR.value == "error"
        assert MonitorStatus.SKIPPED.value == "skipped"

    def test_status_all_members(self):
        assert len(MonitorStatus) == 5

    def test_status_string_comparison(self):
        assert MonitorStatus.OK == "ok"
        assert MonitorStatus.ALERT == "alert"


class TestMonitorCategory:
    def test_category_values(self):
        assert MonitorCategory.REPOSITORY.value == "repository"
        assert MonitorCategory.CI.value == "ci"
        assert MonitorCategory.RELEASE.value == "release"
        assert MonitorCategory.ENDPOINT.value == "endpoint"
        assert MonitorCategory.SECURITY.value == "security"

    def test_category_all_members(self):
        assert len(MonitorCategory) == 5


class TestMonitorResult:
    def test_frozen(self):
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.OK,
            summary="Test",
            category=MonitorCategory.REPOSITORY,
        )
        with pytest.raises(AttributeError):
            result.status = MonitorStatus.ALERT

    def test_to_dict_minimal(self):
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.OK,
            summary="Test summary",
            category=MonitorCategory.REPOSITORY,
        )
        d = result.to_dict()
        assert d["monitor"] == "test"
        assert d["resource"] == "repo"
        assert d["status"] == "ok"
        assert d["summary"] == "Test summary"
        assert d["category"] == "repository"
        assert "evaluated_at" in d

    def test_to_dict_with_events(self):
        event = FieldChange(field="archived", before=False, after=True)
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.CHANGED,
            summary="Changed",
            category=MonitorCategory.REPOSITORY,
            changes=(event,),
        )
        d = result.to_dict()
        assert len(d["changes"]) == 1
        assert d["changes"][0]["field"] == "archived"
        assert d["changes"][0]["before"] is False
        assert d["changes"][0]["after"] is True

    def test_to_dict_with_metadata(self):
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.OK,
            summary="OK",
            category=MonitorCategory.REPOSITORY,
            metadata={"key": "value"},
        )
        d = result.to_dict()
        assert d["metadata"] == {"key": "value"}

    def test_to_dict_empty_events_omitted(self):
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.OK,
            summary="OK",
            category=MonitorCategory.REPOSITORY,
        )
        d = result.to_dict()
        assert "events" not in d
        assert "changes" not in d

    def test_evaluated_at_auto_populated(self):
        result = MonitorResult(
            monitor="test",
            resource="repo",
            status=MonitorStatus.OK,
            summary="OK",
            category=MonitorCategory.REPOSITORY,
        )
        assert result.evaluated_at is not None
        assert "T" in result.evaluated_at  # ISO format


class TestClassifyChangesAsAlert:
    def test_alert_on_critical_field(self):
        changes = (
            FieldChange(field="archived", before=False, after=True),
            FieldChange(field="description", before="old", after="new"),
        )
        alert_fields = frozenset({"archived", "disabled"})
        assert classify_changes_as_alert(changes, alert_fields) is True

    def test_no_alert_on_info_field(self):
        changes = (
            FieldChange(field="description", before="old", after="new"),
            FieldChange(field="stargazers_count", before=10, after=20),
        )
        alert_fields = frozenset({"archived", "disabled"})
        assert classify_changes_as_alert(changes, alert_fields) is False

    def test_empty_changes(self):
        changes = ()
        alert_fields = frozenset({"archived"})
        assert classify_changes_as_alert(changes, alert_fields) is False
