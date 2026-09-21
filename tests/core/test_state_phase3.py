"""Tests for src.core.state Phase 3 additions."""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from src.core.models import (
    CollectorResult,
    CollectorStatus,
    CurrentState,
    HistoryEntry,
    ResourceEvent,
    EventType,
    Snapshot,
    StateStatus,
)
from src.core.state import (
    load,
    save,
    diff,
    prune,
    merge_states,
    validate_state,
    load_validated,
    save_atomic,
    load_current_state,
    save_current_state,
    apply_snapshot,
    load_etags,
    save_etag,
    save_history,
    load_history,
    _state_dir,
    _history_dir,
)


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Create a temporary state directory."""
    monkeypatch.setattr("src.core.state._state_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def history_dir(tmp_path, monkeypatch):
    """Create a temporary history directory."""
    monkeypatch.setattr("src.core.state._history_dir", lambda: tmp_path)
    return tmp_path


# ── Phase 1 backward compatibility ───────────────────────────────


class TestLoadBackwardCompat:
    def test_load_missing_file(self, state_dir):
        result = load("nonexistent.json")
        assert result == {}

    def test_load_valid_file(self, state_dir):
        data = {"items": {"a": {"v": 1}}}
        (state_dir / "test.json").write_text(json.dumps(data))
        result = load("test.json")
        assert result["items"]["a"]["v"] == 1

    def test_load_corrupt_file(self, state_dir):
        (state_dir / "corrupt.json").write_text("not valid json {{{")
        result = load("corrupt.json")
        assert result == {}


class TestSaveBackwardCompat:
    def test_save_creates_file(self, state_dir):
        data = {"items": {"a": {"v": 1}}}
        save("test.json", data)
        assert (state_dir / "test.json").exists()

    def test_save_adds_timestamp(self, state_dir):
        save("test.json", {"items": {}})
        content = json.loads((state_dir / "test.json").read_text())
        assert "last_updated" in content

    def test_save_atomic_no_tmp_left(self, state_dir):
        save("test.json", {"items": {}})
        tmp_files = list(state_dir.glob(".test.json.*.tmp"))
        assert len(tmp_files) == 0


class TestDiffBackwardCompat:
    def test_no_changes(self):
        state = {"items": {"a": {"v": 1}}}
        result = diff(state, state)
        assert result == {"added": [], "removed": [], "changed": []}

    def test_added(self):
        previous = {"items": {}}
        current = {"items": {"a": {"v": 1}}}
        result = diff(previous, current)
        assert result["added"] == ["a"]

    def test_removed(self):
        previous = {"items": {"a": {"v": 1}}}
        current = {"items": {}}
        result = diff(previous, current)
        assert result["removed"] == ["a"]

    def test_changed(self):
        previous = {"items": {"a": {"v": 1}}}
        current = {"items": {"a": {"v": 2}}}
        result = diff(previous, current)
        assert result["changed"] == ["a"]


class TestMergeStatesBackwardCompat:
    def test_merge_empty(self):
        result = merge_states()
        assert result["items"] == {}

    def test_merge_overrides(self):
        s1 = {"items": {"a": {"v": 1}}}
        s2 = {"items": {"a": {"v": 2}}}
        result = merge_states(s1, s2)
        assert result["items"]["a"]["v"] == 2


# ── Phase 3: State validation ────────────────────────────────────


class TestValidateState:
    def test_missing(self):
        v = validate_state(None)
        assert v.status == StateStatus.MISSING

    def test_valid(self):
        v = validate_state({"schema_version": 1, "resources": {}})
        assert v.status == StateStatus.VALID
        assert v.version == 1

    def test_valid_no_version(self):
        v = validate_state({"resources": {}})
        assert v.status == StateStatus.VALID

    def test_corrupt_not_dict(self):
        v = validate_state([1, 2, 3])
        assert v.status == StateStatus.CORRUPT

    def test_corrupt_bad_version_type(self):
        v = validate_state({"schema_version": "1"})
        assert v.status == StateStatus.CORRUPT

    def test_unsupported_version(self):
        v = validate_state({"schema_version": 999})
        assert v.status == StateStatus.UNSUPPORTED_VERSION
        assert v.version == 999


class TestLoadValidated:
    def test_missing_file(self, state_dir):
        data, validation = load_validated("nonexistent.json")
        assert validation.status == StateStatus.MISSING
        assert data == {}

    def test_valid_file(self, state_dir):
        content = {"schema_version": 1, "resources": {"repos": {}}}
        (state_dir / "state.json").write_text(json.dumps(content))
        data, validation = load_validated("state.json")
        assert validation.status == StateStatus.VALID
        assert data["resources"]["repos"] == {}

    def test_corrupt_file(self, state_dir):
        (state_dir / "bad.json").write_text("not json")
        data, validation = load_validated("bad.json")
        assert validation.status == StateStatus.CORRUPT
        assert validation.error is not None

    def test_unsupported_version(self, state_dir):
        content = {"schema_version": 999}
        (state_dir / "future.json").write_text(json.dumps(content))
        data, validation = load_validated("future.json")
        assert validation.status == StateStatus.UNSUPPORTED_VERSION


# ── Phase 3: Atomic persistence ──────────────────────────────────


class TestSaveAtomic:
    def test_creates_file(self, state_dir):
        save_atomic("test.json", {"key": "value"})
        assert (state_dir / "test.json").exists()

    def test_content_correct(self, state_dir):
        data = {"resources": {"repos": {"a/b": {"name": "a/b"}}}}
        save_atomic("test.json", data)
        loaded = json.loads((state_dir / "test.json").read_text())
        assert loaded == data

    def test_no_tmp_files_left(self, state_dir):
        save_atomic("test.json", {"key": "value"})
        tmp_files = list(state_dir.glob(".test.json.*.tmp"))
        assert len(tmp_files) == 0

    def test_creates_directory(self, state_dir, monkeypatch):
        subdir = state_dir / "sub"
        monkeypatch.setattr("src.core.state._state_dir", lambda: subdir)
        save_atomic("test.json", {"key": "value"})
        assert (subdir / "test.json").exists()

    def test_overwrites_existing(self, state_dir):
        save_atomic("test.json", {"v": 1})
        save_atomic("test.json", {"v": 2})
        loaded = json.loads((state_dir / "test.json").read_text())
        assert loaded["v"] == 2


# ── Phase 3: CurrentState persistence ────────────────────────────


class TestCurrentStatePersistence:
    def test_load_empty(self, state_dir):
        state = load_current_state()
        assert isinstance(state, CurrentState)
        assert state.resources == {}

    def test_save_and_load(self, state_dir):
        state = CurrentState(
            resources={"repos": {"a/b": {"name": "a/b"}}},
            etags={"repos": 'W/"abc"'},
        )
        save_current_state(state)
        loaded = load_current_state()
        assert loaded.resources == {"repos": {"a/b": {"name": "a/b"}}}
        assert loaded.etags == {"repos": 'W/"abc"'}

    def test_preserves_update_log(self, state_dir):
        state = CurrentState(
            resources={},
            update_log={"repos": {"status": "success", "item_count": 5}},
        )
        save_current_state(state)
        loaded = load_current_state()
        assert loaded.update_log["repos"]["item_count"] == 5


# ── Phase 3: Apply snapshot ──────────────────────────────────────


class TestApplySnapshot:
    def test_success_overwrites_previous(self):
        previous = CurrentState(
            resources={"repos": {"old/repo": {"name": "old"}}},
        )
        snapshot = Snapshot(results={
            "repos": CollectorResult(
                collector="repos",
                status=CollectorStatus.SUCCESS,
                items={"new/repo": {"name": "new"}},
                item_count=1,
            ),
        })
        result = apply_snapshot(previous, snapshot)
        assert "new/repo" in result.resources["repos"]
        assert "old/repo" not in result.resources["repos"]

    def test_failure_preserves_previous(self):
        previous = CurrentState(
            resources={"releases": {"v1.0": {"tag": "v1.0"}}},
        )
        snapshot = Snapshot(results={
            "releases": CollectorResult(
                collector="releases",
                status=CollectorStatus.FAILURE,
                error="timeout",
            ),
        })
        result = apply_snapshot(previous, snapshot)
        assert result.resources["releases"] == {"v1.0": {"tag": "v1.0"}}

    def test_not_modified_preserves_previous(self):
        previous = CurrentState(
            resources={"issues": {"#1": {"title": "bug"}}},
            etags={"issues": 'W/"old"'},
        )
        snapshot = Snapshot(results={
            "issues": CollectorResult(
                collector="issues",
                status=CollectorStatus.NOT_MODIFIED,
            ),
        })
        result = apply_snapshot(previous, snapshot)
        assert result.resources["issues"] == {"#1": {"title": "bug"}}

    def test_partial_failure_preserves_successful(self):
        previous = CurrentState(
            resources={
                "repos": {"old/repo": {"name": "old"}},
                "releases": {"v1.0": {"tag": "v1.0"}},
            },
        )
        snapshot = Snapshot(results={
            "repos": CollectorResult(
                collector="repos",
                status=CollectorStatus.SUCCESS,
                items={"new/repo": {"name": "new"}},
                item_count=1,
            ),
            "releases": CollectorResult(
                collector="releases",
                status=CollectorStatus.FAILURE,
                error="timeout",
            ),
        })
        result = apply_snapshot(previous, snapshot)
        # repos updated
        assert "new/repo" in result.resources["repos"]
        # releases preserved
        assert result.resources["releases"] == {"v1.0": {"tag": "v1.0"}}

    def test_empty_successful_collection(self):
        previous = CurrentState(
            resources={"repos": {"a/b": {"name": "a/b"}}},
        )
        snapshot = Snapshot(results={
            "repos": CollectorResult(
                collector="repos",
                status=CollectorStatus.SUCCESS,
                items={},
                item_count=0,
            ),
        })
        result = apply_snapshot(previous, snapshot)
        # Empty success clears previous (it's a valid observation)
        assert result.resources["repos"] == {}

    def test_update_log_records_status(self):
        previous = CurrentState()
        snapshot = Snapshot(results={
            "repos": CollectorResult(
                collector="repos",
                status=CollectorStatus.SUCCESS,
                items={},
                item_count=0,
            ),
            "releases": CollectorResult(
                collector="releases",
                status=CollectorStatus.FAILURE,
                error="timeout",
            ),
        })
        result = apply_snapshot(previous, snapshot)
        assert result.update_log["repos"]["status"] == "success"
        assert result.update_log["releases"]["status"] == "failure"
        assert result.update_log["releases"]["error"] == "timeout"


# ── Phase 3: ETag persistence ────────────────────────────────────


class TestETagPersistence:
    def test_load_etags_empty(self, state_dir):
        etags = load_etags()
        assert etags == {}

    def test_save_and_load_etag(self, state_dir):
        save_current_state(CurrentState())
        save_etag("repos", 'W/"abc"')
        etags = load_etags()
        assert etags["repos"] == 'W/"abc"'

    def test_etag_preserved_across_updates(self, state_dir):
        save_current_state(CurrentState())
        save_etag("repos", 'W/"old"')
        save_etag("repos", 'W/"new"')
        etags = load_etags()
        assert etags["repos"] == 'W/"new"'


# ── Phase 3: History ─────────────────────────────────────────────


class TestHistory:
    def test_save_and_load_history(self, history_dir):
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
        save_history(entry)
        entries = load_history()
        assert len(entries) == 1
        assert entries[0].timestamp == "2026-01-01T00:00:00+00:00"

    def test_load_empty_history(self, history_dir):
        entries = load_history()
        assert entries == []

    def test_load_max_entries(self, history_dir):
        for i in range(5):
            entry = HistoryEntry(
                timestamp=f"2026-01-0{i+1}T00:00:00+00:00",
                events=(),
            )
            save_history(entry)
        entries = load_history(max_entries=3)
        assert len(entries) == 3

    def test_history_preserves_event_details(self, history_dir):
        event = ResourceEvent(
            event_type=EventType.CHANGED,
            resource_type="issues",
            resource_id="#1",
            previous={"state": "open"},
            current={"state": "closed"},
            changes=(
                __import__("src.core.models", fromlist=["FieldChange"]).FieldChange(
                    field="state", before="open", after="closed",
                ),
            ),
        )
        entry = HistoryEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            events=(event,),
        )
        save_history(entry)
        entries = load_history()
        assert len(entries) == 1
        assert len(entries[0].events) == 1
        assert entries[0].events[0].event_type == EventType.CHANGED
        assert entries[0].events[0].changes[0].field == "state"
