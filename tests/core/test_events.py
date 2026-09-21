"""Tests for src.core.events"""
import pytest
from src.core.events import (
    Event,
    detect_events,
    events_by_type,
    events_by_source,
    summarize_events,
)


class TestDetectEvents:
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
        assert events[0].current == {"v": 2}

    def test_removed_items(self):
        previous = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        current = {"items": {"a": {"v": 1}}}
        events = detect_events("test", previous, current)
        assert len(events) == 1
        assert events[0].type == "removed"
        assert events[0].key == "b"
        assert events[0].previous == {"v": 2}

    def test_changed_items(self):
        previous = {"items": {"a": {"v": 1}}}
        current = {"items": {"a": {"v": 2}}}
        events = detect_events("test", previous, current)
        assert len(events) == 1
        assert events[0].type == "changed"
        assert events[0].key == "a"
        assert events[0].previous == {"v": 1}
        assert events[0].current == {"v": 2}

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

    def test_source_is_set(self):
        previous = {"items": {}}
        current = {"items": {"a": {"v": 1}}}
        events = detect_events("repos", previous, current)
        assert events[0].source == "repos"


class TestFilterEvents:
    @pytest.fixture
    def sample_events(self):
        return [
            Event(type="new", source="repos", key="a"),
            Event(type="changed", source="repos", key="b"),
            Event(type="new", source="releases", key="c"),
            Event(type="removed", source="repos", key="d"),
        ]

    def test_filter_by_type(self, sample_events):
        new_events = events_by_type(sample_events, "new")
        assert len(new_events) == 2
        assert all(e.type == "new" for e in new_events)

    def test_filter_by_source(self, sample_events):
        repo_events = events_by_source(sample_events, "repos")
        assert len(repo_events) == 3
        assert all(e.source == "repos" for e in repo_events)


class TestSummarizeEvents:
    def test_summary(self):
        events = [
            Event(type="new", source="repos", key="a"),
            Event(type="new", source="repos", key="b"),
            Event(type="changed", source="releases", key="c"),
        ]
        summary = summarize_events(events)
        assert summary["repos:new"] == 2
        assert summary["releases:changed"] == 1
