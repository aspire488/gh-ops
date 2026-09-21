"""Tests for src.core.events Phase 3 additions."""
import pytest
from src.core.events import (
    Event,
    detect_events,
    events_by_type,
    events_by_source,
    summarize_events,
    diff_resources,
    events_by_resource_type,
    events_by_event_type,
    summarize_resource_events,
    _compute_field_changes,
    _normalize_value,
    _is_ignored_field,
    _IGNORED_FIELDS_EXACT,
)
from src.core.state import diff
from src.core.models import EventType, FieldChange, ResourceEvent


# ── Phase 1 backward compatibility ───────────────────────────────


class TestDetectEventsBackwardCompat:
    def test_no_changes(self):
        state = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        events = detect_events("test", state, state)
        assert len(events) == 0

    def test_new_items(self):
        previous = {"items": {"a": {"v": 1}}}
        current = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        events = detect_events("test", previous, current)
        assert len(events) == 1
        assert events[0].type == "new"
        assert events[0].key == "b"

    def test_removed_items(self):
        previous = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        current = {"items": {"a": {"v": 1}}}
        events = detect_events("test", previous, current)
        assert len(events) == 1
        assert events[0].type == "removed"
        assert events[0].key == "b"

    def test_changed_items(self):
        previous = {"items": {"a": {"v": 1}}}
        current = {"items": {"a": {"v": 2}}}
        events = detect_events("test", previous, current)
        assert len(events) == 1
        assert events[0].type == "changed"
        assert events[0].key == "a"

    def test_mixed_events(self):
        previous = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        current = {"items": {"a": {"v": 3}, "c": {"v": 4}}}
        events = detect_events("test", previous, current)
        types = {e.type for e in events}
        assert "new" in types
        assert "removed" in types
        assert "changed" in types

    def test_empty_states(self):
        events = detect_events("test", {}, {})
        assert len(events) == 0


# ── Phase 3: Normalization ───────────────────────────────────────


class TestNormalizeValue:
    def test_none(self):
        assert _normalize_value(None) is None

    def test_primitives(self):
        assert _normalize_value(42) == 42
        assert _normalize_value("hello") == "hello"
        assert _normalize_value(True) is True

    def test_list_sorted(self):
        assert _normalize_value([3, 1, 2]) == [1, 2, 3]

    def test_list_of_dicts(self):
        result = _normalize_value([{"b": 2}, {"a": 1}])
        assert result == [{"a": 1}, {"b": 2}]

    def test_nested_dict(self):
        result = _normalize_value({"b": 2, "a": 1})
        assert result == {"a": 1, "b": 2}

    def test_tuple_becomes_list(self):
        result = _normalize_value((3, 1, 2))
        assert result == [1, 2, 3]
        assert isinstance(result, list)


# ── Phase 3: Field change computation ────────────────────────────


class TestComputeFieldChanges:
    def test_no_changes(self):
        changes = _compute_field_changes({"a": 1, "b": 2}, {"a": 1, "b": 2})
        assert changes == ()

    def test_single_change(self):
        changes = _compute_field_changes(
            {"state": "open"},
            {"state": "closed"},
        )
        assert len(changes) == 1
        assert changes[0].field == "state"
        assert changes[0].before == "open"
        assert changes[0].after == "closed"

    def test_multiple_changes_sorted(self):
        changes = _compute_field_changes(
            {"state": "open", "title": "old", "labels": ["bug"]},
            {"state": "closed", "title": "new", "labels": ["bug", "fix"]},
        )
        assert len(changes) == 3
        assert changes[0].field == "labels"
        assert changes[1].field == "state"
        assert changes[2].field == "title"

    def test_ignored_fields_excluded(self):
        changes = _compute_field_changes(
            {"state": "open", "updated_at": "2026-01-01"},
            {"state": "closed", "updated_at": "2026-01-02"},
        )
        assert len(changes) == 1
        assert changes[0].field == "state"

    def test_new_field_added(self):
        changes = _compute_field_changes(
            {"a": 1},
            {"a": 1, "b": 2},
        )
        assert len(changes) == 1
        assert changes[0].field == "b"
        assert changes[0].before is None
        assert changes[0].after == 2

    def test_field_removed(self):
        changes = _compute_field_changes(
            {"a": 1, "b": 2},
            {"a": 1},
        )
        assert len(changes) == 1
        assert changes[0].field == "b"
        assert changes[0].before == 2
        assert changes[0].after is None

    def test_list_changes(self):
        changes = _compute_field_changes(
            {"labels": ["bug"]},
            {"labels": ["bug", "fix"]},
        )
        assert len(changes) == 1
        assert changes[0].field == "labels"

    def test_deterministic_order(self):
        c1 = _compute_field_changes({"z": 1, "a": 2, "m": 3}, {"z": 2, "a": 3, "m": 4})
        c2 = _compute_field_changes({"z": 1, "a": 2, "m": 3}, {"z": 2, "a": 3, "m": 4})
        assert [c.field for c in c1] == [c.field for c in c2]


# ── Phase 3: Deterministic diff engine ───────────────────────────


class TestDiffResources:
    def test_empty_previous_and_current(self):
        events = diff_resources("repos", {}, {})
        assert events == ()

    def test_new_items(self):
        events = diff_resources(
            "repos",
            {},
            {"a/b": {"name": "a/b"}},
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.NEW
        assert events[0].resource_id == "a/b"
        assert events[0].resource_type == "repos"

    def test_removed_items(self):
        events = diff_resources(
            "repos",
            {"a/b": {"name": "a/b"}},
            {},
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.REMOVED

    def test_changed_items_with_field_tracking(self):
        events = diff_resources(
            "issues",
            {"#1": {"state": "open", "title": "bug"}},
            {"#1": {"state": "closed", "title": "bug"}},
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.CHANGED
        assert len(events[0].changes) == 1
        assert events[0].changes[0].field == "state"
        assert events[0].changes[0].before == "open"
        assert events[0].changes[0].after == "closed"

    def test_unchanged_not_included_by_default(self):
        events = diff_resources(
            "repos",
            {"a/b": {"name": "a/b"}},
            {"a/b": {"name": "a/b"}},
        )
        assert events == ()

    def test_unchanged_included_when_requested(self):
        events = diff_resources(
            "repos",
            {"a/b": {"name": "a/b"}},
            {"a/b": {"name": "a/b"}},
            include_unchanged=True,
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.UNCHANGED

    def test_mixed_events(self):
        events = diff_resources(
            "repos",
            {"a/b": {"name": "old"}, "c/d": {"name": "c/d"}},
            {"a/b": {"name": "new"}, "e/f": {"name": "e/f"}},
        )
        types = {e.event_type for e in events}
        assert EventType.CHANGED in types
        assert EventType.REMOVED in types
        assert EventType.NEW in types

    def test_deterministic_ordering(self):
        items = {f"repo{i}": {"name": f"repo{i}"} for i in range(10)}
        e1 = diff_resources("repos", {}, items)
        e2 = diff_resources("repos", {}, items)
        assert [e.resource_id for e in e1] == [e.resource_id for e in e2]

    def test_determinism_same_input_same_output(self):
        prev = {"a": {"v": 1}, "b": {"v": 2}}
        curr = {"a": {"v": 3}, "c": {"v": 4}}
        e1 = diff_resources("test", prev, curr)
        e2 = diff_resources("test", prev, curr)
        assert e1 == e2

    def test_no_changes_same_state(self):
        state = {"a": {"v": 1}, "b": {"v": 2}}
        events = diff_resources("test", state, state)
        assert events == ()

    def test_returns_tuple(self):
        events = diff_resources("repos", {}, {"a/b": {}})
        assert isinstance(events, tuple)

    def test_ignored_fields_not_in_changes(self):
        events = diff_resources(
            "repos",
            {"a/b": {"name": "a", "updated_at": "old"}},
            {"a/b": {"name": "a", "updated_at": "new"}},
        )
        # name unchanged, updated_at ignored → no CHANGED event
        assert events == ()


# ── Phase 3: Filter functions ────────────────────────────────────


class TestFilterResourceEvents:
    @pytest.fixture
    def sample_events(self):
        return (
            ResourceEvent(event_type=EventType.NEW, resource_type="repos", resource_id="a"),
            ResourceEvent(event_type=EventType.CHANGED, resource_type="repos", resource_id="b"),
            ResourceEvent(event_type=EventType.NEW, resource_type="releases", resource_id="c"),
            ResourceEvent(event_type=EventType.REMOVED, resource_type="repos", resource_id="d"),
        )

    def test_by_resource_type(self, sample_events):
        repo_events = events_by_resource_type(sample_events, "repos")
        assert len(repo_events) == 3

    def test_by_event_type(self, sample_events):
        new_events = events_by_event_type(sample_events, EventType.NEW)
        assert len(new_events) == 2

    def test_summarize(self, sample_events):
        summary = summarize_resource_events(sample_events)
        assert summary["repos:new"] == 1
        assert summary["repos:changed"] == 1
        assert summary["repos:removed"] == 1
        assert summary["releases:new"] == 1


# ── Phase 3: Determinism property tests ──────────────────────────


class TestDeterminism:
    def test_diff_deterministic(self):
        """diff(A, B) always equals diff(A, B)."""
        prev = {"items": {"a": {"x": 1}, "b": {"x": 2}}}
        curr = {"items": {"a": {"x": 3}, "c": {"x": 4}}}
        for _ in range(10):
            result = diff(prev, curr)
            assert result == {"added": ["c"], "removed": ["b"], "changed": ["a"]}

    def test_diff_resources_deterministic(self):
        """diff_resources(A, B) always equals diff_resources(A, B)."""
        prev = {"a": {"state": "open"}, "b": {"state": "closed"}}
        curr = {"a": {"state": "closed"}, "c": {"state": "open"}}
        for _ in range(10):
            events = diff_resources("test", prev, curr)
            assert len(events) == 3
            assert events[0].resource_id == "a"
            assert events[1].resource_id == "b"
            assert events[2].resource_id == "c"

    def test_no_changes_same_state(self):
        """diff(X, X) == [] for ResourceEvents."""
        state = {"a": {"v": 1}, "b": {"v": 2}}
        events = diff_resources("test", state, state)
        assert events == ()

    def test_field_changes_deterministic(self):
        """_compute_field_changes always produces same result."""
        prev = {"state": "open", "labels": ["bug"], "title": "old"}
        curr = {"state": "closed", "labels": ["bug", "fix"], "title": "new"}
        for _ in range(10):
            changes = _compute_field_changes(prev, curr)
            assert len(changes) == 3
            assert [c.field for c in changes] == ["labels", "state", "title"]
