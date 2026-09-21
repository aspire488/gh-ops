"""JSON state persistence with atomic writes for gh-ops.

State files are stored in data/state/ and are gitignored.
GitHub Actions cache restores/saves this directory between runs.

Phase 3 additions:
- Schema versioning (STATE_SCHEMA_VERSION)
- State validation (MISSING, VALID, CORRUPT, UNSUPPORTED_VERSION)
- Atomic persistence with fsync
- CurrentState persistence (per-resource-type atomic updates)
- ETag persistence
- Partial failure semantics (failed collector preserves previous state)
- History observation boundary (minimal interface, retention deferred)
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.errors import StateError, ErrorCode
from src.core.models import (
    STATE_SCHEMA_VERSION,
    CollectorResult,
    CollectorStatus,
    CurrentState,
    FieldChange,
    HistoryEntry,
    ResourceEvent,
    StateStatus,
    StateValidation,
    Snapshot,
)
from src.utils.logging import get_logger

logger = get_logger("core.state")

# Default state directory
_DEFAULT_STATE_DIR = "data/state"
_DEFAULT_HISTORY_DIR = "data/history"


def _safe_filename(name: str) -> str:
    """Validate a filename is safe (no path traversal).

    Args:
        name: Filename to validate.

    Returns:
        The validated filename.

    Raises:
        ValueError: If filename contains path traversal or absolute paths.
    """
    from pathlib import PurePosixPath

    p = PurePosixPath(name)
    if p.is_absolute():
        raise ValueError(f"Absolute path not allowed: {name}")
    if ".." in p.parts:
        raise ValueError(f"Path traversal not allowed: {name}")
    if not name:
        raise ValueError("Filename cannot be empty")
    return name


def _state_dir() -> Path:
    """Get the state directory path."""
    root = Path(__file__).resolve().parent.parent.parent
    return root / _DEFAULT_STATE_DIR


def _history_dir() -> Path:
    """Get the history directory path."""
    root = Path(__file__).resolve().parent.parent.parent
    return root / _DEFAULT_HISTORY_DIR


# ── Phase 1 functions (preserved) ────────────────────────────────


def load(filename: str) -> dict[str, Any]:
    """Load a state file. Returns empty dict if missing or corrupt.

    Args:
        filename: Name of the state file (e.g., "repos.json").

    Returns:
        Parsed state data, or empty dict.
    """
    _safe_filename(filename)
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
    _safe_filename(filename)
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

    for key, value in items.items():
        last_checked = value.get("last_checked") or value.get("last_updated")
        if last_checked:
            try:
                ts = parse_github_timestamp(last_checked)
                if days_ago(ts) <= max_age_days:
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


# ── Phase 3: State validation ────────────────────────────────────


def validate_state(data: dict[str, Any] | None) -> StateValidation:
    """Validate a loaded state dict.

    Classification:
        MISSING — file does not exist or is None
        VALID — valid JSON dict with supported schema version
        CORRUPT — file exists but is not valid JSON or not a dict
        UNSUPPORTED_VERSION — valid dict but schema_version too high

    Args:
        data: Raw loaded data (result of json.load or None).

    Returns:
        StateValidation with status and optional version/error info.
    """
    if data is None:
        return StateValidation(status=StateStatus.MISSING)

    if not isinstance(data, dict):
        return StateValidation(
            status=StateStatus.CORRUPT,
            error=f"Expected dict, got {type(data).__name__}",
        )

    version = data.get("schema_version")
    if version is not None and not isinstance(version, int):
        return StateValidation(
            status=StateStatus.CORRUPT,
            error=f"schema_version must be int, got {type(version).__name__}",
        )

    if version is not None and version > STATE_SCHEMA_VERSION:
        return StateValidation(
            status=StateStatus.UNSUPPORTED_VERSION,
            version=version,
            error=f"Schema version {version} > supported {STATE_SCHEMA_VERSION}",
        )

    return StateValidation(
        status=StateStatus.VALID,
        version=version or STATE_SCHEMA_VERSION,
    )


def load_validated(filename: str) -> tuple[dict[str, Any], StateValidation]:
    """Load a state file and return it with validation result.

    Does NOT return empty dict on failure — returns the raw data and
    lets the caller decide based on validation status.

    Args:
        filename: Name of the state file.

    Returns:
        Tuple of (raw_data_or_empty_dict, validation_result).
    """
    _safe_filename(filename)
    path = _state_dir() / filename
    if not path.exists():
        return {}, StateValidation(status=StateStatus.MISSING)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {}, StateValidation(
            status=StateStatus.CORRUPT,
            error=str(e),
        )
    except OSError as e:
        return {}, StateValidation(
            status=StateStatus.CORRUPT,
            error=str(e),
        )

    if not isinstance(data, dict):
        return {}, StateValidation(
            status=StateStatus.CORRUPT,
            error=f"Expected dict, got {type(data).__name__}",
        )

    validation = validate_state(data)
    return data, validation


# ── Phase 3: Atomic persistence with fsync ───────────────────────


def save_atomic(filename: str, data: dict[str, Any]) -> None:
    """Save state data atomically with fsync.

    Write path: temp file → write → flush → fsync → atomic replace.

    This is a stricter version of save() for Phase 3 data.
    The final state file must never contain a partially written JSON document.

    Args:
        filename: Name of the state file.
        data: State data to save.

    Raises:
        StateError: If the write fails.
    """
    _safe_filename(filename)
    path = _state_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            suffix=".tmp",
            prefix=f".{filename}.",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            logger.debug("State saved atomically: %s", filename)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        raise StateError(
            f"Failed to save state file {filename}: {e}",
            filename=filename,
            code=ErrorCode.STATE_SAVE_FAILED,
            cause=e,
        ) from e


# ── Phase 3: CurrentState persistence ────────────────────────────


def load_current_state() -> CurrentState:
    """Load the current state from data/state/current_state.json.

    Returns empty CurrentState if file is missing.
    Raises StateError if file is corrupt or has unsupported version.

    Returns:
        CurrentState instance.
    """
    filename = "current_state.json"
    data, validation = load_validated(filename)

    if validation.status == StateStatus.MISSING:
        return CurrentState()

    if validation.status == StateStatus.CORRUPT:
        raise StateError(
            f"Corrupt state file {filename}: {validation.error}",
            filename=filename,
            code=ErrorCode.STATE_CORRUPTED,
        )

    if validation.status == StateStatus.UNSUPPORTED_VERSION:
        raise StateError(
            f"Unsupported state version {validation.version}: {validation.error}",
            filename=filename,
            code=ErrorCode.STATE_UNSUPPORTED_VERSION,
        )

    return CurrentState.from_dict(data)


def save_current_state(state: CurrentState) -> None:
    """Save current state to data/state/current_state.json atomically.

    Args:
        state: CurrentState to persist.

    Raises:
        StateError: If the write fails.
    """
    data = state.to_dict()
    save_atomic("current_state.json", data)


# ── Phase 3: Apply snapshot to current state ─────────────────────


def apply_snapshot(
    previous: CurrentState,
    snapshot: Snapshot,
) -> CurrentState:
    """Apply a snapshot to the current state with partial failure semantics.

    Rules:
    - SUCCESS: overwrite that collector's items in current state
    - NOT_MODIFIED: preserve existing items, update observed_at
    - FAILURE: preserve previous items, record the failure

    The failed collector MUST NOT cause valid previous state to disappear.
    An empty successful collection and a failed collection are NOT the same.

    Args:
        previous: The current state before this snapshot.
        snapshot: What was observed during this collection run.

    Returns:
        New CurrentState with accepted results merged in.
    """
    new_resources = dict(previous.resources)
    new_etags = dict(previous.etags)
    new_update_log = dict(previous.update_log)

    for name, result in snapshot.results.items():
        if result.status == CollectorStatus.SUCCESS:
            # Accept new data
            new_resources[name] = result.items or {}
            if result.etag:
                new_etags[name] = result.etag
            new_update_log[name] = {
                "status": "success",
                "observed_at": result.observed_at,
                "item_count": result.item_count,
            }

        elif result.status == CollectorStatus.NOT_MODIFIED:
            # Preserve existing items, update timestamp
            new_update_log[name] = {
                "status": "not_modified",
                "observed_at": result.observed_at,
                "item_count": previous.update_log.get(name, {}).get("item_count", 0),
            }

        elif result.status == CollectorStatus.FAILURE:
            # Preserve previous state, record failure
            new_update_log[name] = {
                "status": "failure",
                "observed_at": result.observed_at,
                "error": result.error,
                "item_count": previous.update_log.get(name, {}).get("item_count", 0),
            }

    return CurrentState(
        resources=new_resources,
        etags=new_etags,
        last_updated=snapshot.collected_at,
        schema_version=STATE_SCHEMA_VERSION,
        update_log=new_update_log,
    )


# ── Phase 3: ETag persistence ────────────────────────────────────


def load_etags() -> dict[str, str]:
    """Load persisted ETags from state.

    Returns:
        Dict mapping collector name → ETag string.
    """
    state = load_current_state()
    return dict(state.etags)


def save_etag(collector: str, etag: str) -> None:
    """Save a single ETag to the current state.

    Loads current state, updates the ETag, saves back.
    Only updates the ETag, preserves everything else.

    Args:
        collector: Collector name.
        etag: ETag value.
    """
    state = load_current_state()
    state.etags[collector] = etag
    save_current_state(state)


# ── Phase 3: History ─────────────────────────────────────────────


def save_history(entry: HistoryEntry) -> None:
    """Append a history entry.

    History files are named by timestamp for append-only semantics.
    Retention policy is deferred — interface is clean for future extension.

    Args:
        entry: HistoryEntry to persist.
    """
    history_dir = _history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)

    # Use timestamp as filename for deterministic, sortable naming
    safe_ts = entry.timestamp.replace(":", "-").replace("/", "-")
    filename = f"{safe_ts}.json"
    path = history_dir / filename

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=history_dir,
            suffix=".tmp",
            prefix=f".{filename}.",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        raise StateError(
            f"Failed to save history entry: {e}",
            filename=filename,
            code=ErrorCode.STATE_SAVE_FAILED,
            cause=e,
        ) from e


def load_history(max_entries: int = 100) -> list[HistoryEntry]:
    """Load recent history entries.

    Args:
        max_entries: Maximum number of entries to return.

    Returns:
        List of HistoryEntry, most recent first.
    """
    history_dir = _history_dir()
    if not history_dir.exists():
        return []

    entries: list[HistoryEntry] = []
    # Sort by filename (ISO timestamp format) — lexicographic sort works because
    # ISO 8601 timestamps are designed to sort correctly as strings.
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        if len(entries) >= max_entries:
            break
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = tuple(
                ResourceEvent(
                    event_type=e["event_type"],
                    resource_type=e["resource_type"],
                    resource_id=e["resource_id"],
                    previous=e.get("previous"),
                    current=e.get("current"),
                    changes=tuple(
                        FieldChange(
                            field=c["field"],
                            before=c["before"],
                            after=c["after"],
                        )
                        for c in e.get("changes", [])
                    ),
                )
                for e in data.get("events", [])
            )
            entries.append(
                HistoryEntry(
                    timestamp=data["timestamp"],
                    events=events,
                    schema_version=data.get("schema_version", STATE_SCHEMA_VERSION),
                )
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Skipping corrupt history file %s: %s", path.name, e)
            continue

    return entries
