"""JSON state persistence with atomic writes for gh-ops.

State files are stored in data/state/ and are gitignored.
GitHub Actions cache restores/saves this directory between runs.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.errors import StateError, ErrorCode
from src.utils.logging import get_logger

logger = get_logger("core.state")

# Default state directory
_DEFAULT_STATE_DIR = "data/state"


def _state_dir() -> Path:
    """Get the state directory path."""
    root = Path(__file__).resolve().parent.parent.parent
    return root / _DEFAULT_STATE_DIR


def load(filename: str) -> dict[str, Any]:
    """Load a state file. Returns empty dict if missing or corrupt.

    Args:
        filename: Name of the state file (e.g., "repos.json").

    Returns:
        Parsed state data, or empty dict.
    """
    path = _state_dir() / filename
    if not path.exists():
        logger.debug("State file not found, returning empty: %s", filename)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("Corrupt state file %s: %s", filename, e)
        return {}
    except OSError as e:
        logger.error("Failed to read state file %s: %s", filename, e)
        return {}

    if not isinstance(data, dict):
        logger.warning("State file %s is not a dict, returning empty", filename)
        return {}

    return data


def save(filename: str, data: dict[str, Any]) -> None:
    """Save state data atomically.

    Writes to a temp file first, then renames for crash safety.
    Adds last_updated timestamp.

    Args:
        filename: Name of the state file.
        data: State data to save.

    Raises:
        StateError: If the write fails.
    """
    path = _state_dir() / filename

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Add metadata
    save_data = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        **data,
    }

    # Atomic write: write to temp file, then rename
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            suffix=".tmp",
            prefix=f".{filename}.",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, path)
            logger.debug("State saved: %s", filename)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        raise StateError(
            f"Failed to save state file {filename}: {e}",
            filename=filename,
            cause=e,
        ) from e


def diff(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, list[str]]:
    """Compare two state snapshots and return changes.

    Both dicts should have the structure:
        { "items": { "key": { ... }, ... } }

    Args:
        previous: Previous state.
        current: Current state.

    Returns:
        Dict with "added", "removed", "changed" lists of keys.
    """
    prev_items = previous.get("items", {})
    curr_items = current.get("items", {})

    prev_keys = set(prev_items.keys())
    curr_keys = set(curr_items.keys())

    added = sorted(curr_keys - prev_keys)
    removed = sorted(prev_keys - curr_keys)
    changed = sorted(
        key for key in prev_keys & curr_keys
        if prev_items[key] != curr_items[key]
    )

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def prune(state: dict[str, Any], max_age_days: int) -> dict[str, Any]:
    """Remove entries older than max_age_days from state.

    Entries must have a "last_checked" field in ISO format.

    Args:
        state: State dict with "items" key.
        max_age_days: Maximum age in days.

    Returns:
        Pruned state dict.
    """
    from src.utils.time import parse_github_timestamp, days_ago

    items = state.get("items", {})
    if not items:
        return state

    pruned_items = {}
    cutoff_days = max_age_days

    for key, value in items.items():
        last_checked = value.get("last_checked") or value.get("last_updated")
        if last_checked:
            try:
                ts = parse_github_timestamp(last_checked)
                if days_ago(ts) <= cutoff_days:
                    pruned_items[key] = value
            except (ValueError, TypeError):
                # Keep entries with unparseable timestamps
                pruned_items[key] = value
        else:
            # Keep entries without timestamps
            pruned_items[key] = value

    state["items"] = pruned_items
    return state


def merge_states(*states: dict[str, Any]) -> dict[str, Any]:
    """Merge multiple state dicts. Later states override earlier ones.

    Args:
        *states: State dicts to merge (in order of precedence).

    Returns:
        Merged state dict.
    """
    merged: dict[str, Any] = {"items": {}}

    for state in states:
        items = state.get("items", {})
        merged["items"].update(items)

    return merged
