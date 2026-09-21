"""Tests for src.core.models (Phase 3 state models)."""
import pytest
from src.core.models import (
    STATE_SCHEMA_VERSION,
    CollectorResult,
    CollectorStatus,
    CurrentState,
    EventType,
    FieldChange,
    HistoryEntry,
    ResourceEvent,
    Snapshot,
    StateStatus,
    StateValidation,
)


class TestCollectorResult:
    def test_success_result(self):
        r = CollectorResult(
            collector="repos",
            status=CollectorStatus.SUCCESS,
            items={"a/b": {"name": "a/b"}},
            etag='W/"abc"',
            item_count=1,
        )
        assert r.status == CollectorStatus.SUCCESS
        assert r.items is not None
        assert r.etag == 'W/"abc"'
        assert r.error is None

    def test_failure_result(self):
        r = CollectorResult(
            collector="releases",
            status=CollectorStatus.FAILURE,
            error="API timeout",
        )
        assert r.status == CollectorStatus.FAILURE
        assert r.items is None
        assert r.error == "API timeout"

    def test_not_modified_result(self):
        r = CollectorResult(
            collector="issues",
            status=CollectorStatus.NOT_MODIFIED,
        )
        assert r.status == CollectorStatus.NOT_MODIFIED
        assert r.items is None

    def test_frozen(self):
        r = CollectorResult(collector="repos", status=CollectorStatus.SUCCESS)
        with pytest.raises(AttributeError):
            r.collector = "other"


class TestSnapshot:
    def test_empty_snapshot(self):
        s = Snapshot()
        assert s.success_count == 0
        assert s.failure_count == 0
        assert s.not_modified_count == 0

    def test_mixed_results(self):
        s = Snapshot(results={
            "repos": CollectorResult(collector="repos", status=CollectorStatus.SUCCESS, items={}, item_count=0),
            "releases": CollectorResult(collector="releases", status=CollectorStatus.FAILURE, error="timeout"),
            "issues": CollectorResult(collector="issues", status=CollectorStatus.NOT_MODIFIED),
        })
        assert s.success_count == 1
        assert s.failure_count == 1
        assert s.not_modified_count == 1

    def test_successful_collectors(self):
        s = Snapshot(results={
            "repos": CollectorResult(collector="repos", status=CollectorStatus.SUCCESS, items={}),
            "releases": CollectorResult(collector="releases", status=CollectorStatus.FAILURE, error="x"),
        })
        successful = s.successful_collectors()
        assert "repos" in successful
        assert "releases" not in successful

    def test_failed_collectors(self):
        s = Snapshot(results={
            "repos": CollectorResult(collector="repos", status=CollectorStatus.SUCCESS, items={}),
            "releases": CollectorResult(collector="releases", status=CollectorStatus.FAILURE, error="x"),
        })
        failed = s.failed_collectors()
        assert "releases" in failed
        assert "repos" not in failed

    def test_schema_version(self):
        s = Snapshot()
        assert s.schema_version == STATE_SCHEMA_VERSION


class TestCurrentState:
    def test_empty_state(self):
        state = CurrentState()
        assert state.resources == {}
        assert state.etags == {}
        assert state.schema_version == STATE_SCHEMA_VERSION

    def test_to_dict_roundtrip(self):
        state = CurrentState(
            resources={"repos": {"a/b": {"name": "a/b"}}},
            etags={"repos": 'W/"abc"'},
            last_updated="2026-01-01T00:00:00+00:00",
            schema_version=1,
            update_log={"repos": {"status": "success", "observed_at": "2026-01-01T00:00:00+00:00", "item_count": 1}},
        )
        d = state.to_dict()
        restored = CurrentState.from_dict(d)
        assert restored.resources == state.resources
        assert restored.etags == state.etags
        assert restored.schema_version == state.schema_version
        assert restored.update_log == state.update_log

    def test_from_dict_minimal(self):
        state = CurrentState.from_dict({})
        assert state.resources == {}
        assert state.etags == {}
        assert state.schema_version == STATE_SCHEMA_VERSION


class TestStateValidation:
    def test_missing(self):
        v = StateValidation(status=StateStatus.MISSING)
        assert v.status == StateStatus.MISSING

    def test_valid(self):
        v = StateValidation(status=StateStatus.VALID, version=1)
        assert v.status == StateStatus.VALID
        assert v.version == 1

    def test_corrupt(self):
        v = StateValidation(status=StateStatus.CORRUPT, error="not a dict")
        assert v.status == StateStatus.CORRUPT

    def test_unsupported_version(self):
        v = StateValidation(status=StateStatus.UNSUPPORTED_VERSION, version=999)
        assert v.status == StateStatus.UNSUPPORTED_VERSION
        assert v.version == 999


class TestFieldChange:
    def test_creation(self):
        fc = FieldChange(field="state", before="open", after="closed")
        assert fc.field == "state"
        assert fc.before == "open"
        assert fc.after == "closed"

    def test_frozen(self):
        fc = FieldChange(field="x", before=1, after=2)
        with pytest.raises(AttributeError):
            fc.field = "y"


class TestResourceEvent:
    def test_new_event(self):
        e = ResourceEvent(
            event_type=EventType.NEW,
            resource_type="repos",
            resource_id="a/b",
            current={"name": "a/b"},
        )
        assert e.event_type == EventType.NEW
        assert e.changes == ()

    def test_changed_event_with_changes(self):
        changes = (FieldChange(field="state", before="open", after="closed"),)
        e = ResourceEvent(
            event_type=EventType.CHANGED,
            resource_type="issues",
            resource_id="1",
            previous={"state": "open"},
            current={"state": "closed"},
            changes=changes,
        )
        assert len(e.changes) == 1
        assert e.changes[0].field == "state"

    def test_to_dict(self):
        e = ResourceEvent(
            event_type=EventType.NEW,
            resource_type="repos",
            resource_id="a/b",
            current={"name": "a/b"},
        )
        d = e.to_dict()
        assert d["event_type"] == "new"
        assert d["resource_type"] == "repos"
        assert d["resource_id"] == "a/b"
        assert d["current"] == {"name": "a/b"}

    def test_to_dict_with_changes(self):
        e = ResourceEvent(
            event_type=EventType.CHANGED,
            resource_type="issues",
            resource_id="1",
            changes=(FieldChange(field="state", before="open", after="closed"),),
        )
        d = e.to_dict()
        assert "changes" in d
        assert d["changes"][0]["field"] == "state"


class TestHistoryEntry:
    def test_creation(self):
        entry = HistoryEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            events=(),
        )
        assert entry.timestamp == "2026-01-01T00:00:00+00:00"
        assert entry.events == ()
        assert entry.schema_version == STATE_SCHEMA_VERSION

    def test_to_dict_roundtrip(self):
        event = ResourceEvent(
            event_type=EventType.NEW,
            resource_type="repos",
            resource_id="a/b",
            current={"name": "a/b"},
        )
        entry = HistoryEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            events=(event,),
        )
        d = entry.to_dict()
        assert len(d["events"]) == 1
        assert d["events"][0]["event_type"] == "new"
