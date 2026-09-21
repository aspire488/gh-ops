"""Tests for src.core.state"""
import json
import os
import pytest
from pathlib import Path

from src.core.state import load, save, diff, prune, merge_states


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Create a temporary state directory."""
    monkeypatch.setattr("src.core.state._state_dir", lambda: tmp_path)
    return tmp_path


class TestLoad:
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

    def test_load_non_dict(self, state_dir):
        (state_dir / "list.json").write_text(json.dumps([1, 2, 3]))
        result = load("list.json")
        assert result == {}


class TestSave:
    def test_save_creates_file(self, state_dir):
        data = {"items": {"a": {"v": 1}}}
        save("test.json", data)
        assert (state_dir / "test.json").exists()

    def test_save_adds_timestamp(self, state_dir):
        save("test.json", {"items": {}})
        content = json.loads((state_dir / "test.json").read_text())
        assert "last_updated" in content

    def test_save_atomic(self, state_dir):
        """Verify no .tmp files left after save."""
        save("test.json", {"items": {}})
        tmp_files = list(state_dir.glob(".test.json.*.tmp"))
        assert len(tmp_files) == 0

    def test_save_creates_directory(self, state_dir, monkeypatch):
        subdir = state_dir / "subdir"
        monkeypatch.setattr("src.core.state._state_dir", lambda: subdir)
        save("test.json", {"items": {}})
        assert (subdir / "test.json").exists()


class TestDiff:
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

    def test_mixed(self):
        previous = {"items": {"a": {"v": 1}, "b": {"v": 2}}}
        current = {"items": {"a": {"v": 3}, "c": {"v": 4}}}
        result = diff(previous, current)
        assert result["added"] == ["c"]
        assert result["removed"] == ["b"]
        assert result["changed"] == ["a"]


class TestMergeStates:
    def test_merge_empty(self):
        result = merge_states()
        assert result["items"] == {}

    def test_merge_single(self):
        state = {"items": {"a": {"v": 1}}}
        result = merge_states(state)
        assert result["items"]["a"]["v"] == 1

    def test_merge_overrides(self):
        s1 = {"items": {"a": {"v": 1}}}
        s2 = {"items": {"a": {"v": 2}}}
        result = merge_states(s1, s2)
        assert result["items"]["a"]["v"] == 2

    def test_merge_additive(self):
        s1 = {"items": {"a": {"v": 1}}}
        s2 = {"items": {"b": {"v": 2}}}
        result = merge_states(s1, s2)
        assert len(result["items"]) == 2
