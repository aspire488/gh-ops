"""State models for gh-ops Phase 3.

Defines the four distinct concepts:
    Snapshot   — What was observed during this collection run.
    CurrentState — The latest successfully accepted observation.
    Event      — The deterministic transition between observations.
    HistoryEntry — What has previously been observed / emitted.

These are kept SEPARATE. Do not collapse into one JSON file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


# ── Schema versioning ────────────────────────────────────────────

STATE_SCHEMA_VERSION = 1


# ── Collector result status ──────────────────────────────────────

class CollectorStatus(str, Enum):
    """Outcome of a collector run.

    An empty successful collection and a failed collection are NOT
    the same thing.
    """
    SUCCESS = "success"
    FAILURE = "failure"
    NOT_MODIFIED = "not_modified"  # 304 — preserve existing state


# ── Collector result ─────────────────────────────────────────────

@dataclass(frozen=True)
class CollectorResult:
    """Result of a single collector execution.

    Attributes:
        collector: Collector name (e.g. "repos", "issues").
        status: Outcome of the collection.
        items: Collected items keyed by stable resource ID. None on failure.
        etag: ETag returned by the API. None if not available.
        error: Error message if status is FAILURE. None otherwise.
        item_count: Number of items collected (for logging).
        observed_at: ISO timestamp of when this observation was made.
    """
    collector: str
    status: CollectorStatus
    items: dict[str, dict[str, Any]] | None = None
    etag: str | None = None
    error: str | None = None
    item_count: int = 0
    observed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Snapshot ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    """What was observed during a single collection run.

    A snapshot contains one CollectorResult per resource type.
    Partial failures are explicit: a failed collector's items are None,
    but successful collectors' items are preserved.
    """
    results: dict[str, CollectorResult] = field(default_factory=dict)
    schema_version: int = STATE_SCHEMA_VERSION
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def success_count(self) -> int:
        return sum(
            1 for r in self.results.values()
            if r.status == CollectorStatus.SUCCESS
        )

    @property
    def failure_count(self) -> int:
        return sum(
            1 for r in self.results.values()
            if r.status == CollectorStatus.FAILURE
        )

    @property
    def not_modified_count(self) -> int:
        return sum(
            1 for r in self.results.values()
            if r.status == CollectorStatus.NOT_MODIFIED
        )

    def successful_collectors(self) -> dict[str, CollectorResult]:
        return {
            k: v for k, v in self.results.items()
            if v.status == CollectorStatus.SUCCESS
        }

    def failed_collectors(self) -> dict[str, CollectorResult]:
        return {
            k: v for k, v in self.results.items()
            if v.status == CollectorStatus.FAILURE
        }


# ── Current State ────────────────────────────────────────────────

@dataclass
class CurrentState:
    """The latest successfully accepted observation per resource type.

    This is what gets persisted to data/state/.
    Failed collectors do NOT overwrite previous valid state.

    Attributes:
        resources: Map of collector name → items dict.
        etags: Map of collector name → ETag string.
        last_updated: ISO timestamp of last successful update.
        schema_version: State schema version.
        update_log: Map of collector name → {status, observed_at, item_count}.
    """
    resources: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    last_updated: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = STATE_SCHEMA_VERSION
    update_log: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization for JSON persistence."""
        return {
            "schema_version": self.schema_version,
            "last_updated": self.last_updated,
            "resources": self.resources,
            "etags": self.etags,
            "update_log": self.update_log,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CurrentState:
        """Deserialize from JSON. Does NOT validate — use validate_state for that."""
        return cls(
            resources=data.get("resources", {}),
            etags=data.get("etags", {}),
            last_updated=data.get("last_updated", ""),
            schema_version=data.get("schema_version", STATE_SCHEMA_VERSION),
            update_log=data.get("update_log", {}),
        )


# ── State validation ─────────────────────────────────────────────

class StateStatus(str, Enum):
    """Classification of state file condition."""
    MISSING = "missing"
    VALID = "valid"
    CORRUPT = "corrupt"
    UNSUPPORTED_VERSION = "unsupported_version"


@dataclass(frozen=True)
class StateValidation:
    """Result of validating a state file."""
    status: StateStatus
    version: int | None = None
    error: str | None = None


# ── Field change tracking ────────────────────────────────────────

@dataclass(frozen=True)
class FieldChange:
    """A single field-level change between two resource states."""
    field: str
    before: Any
    after: Any


# ── Event ────────────────────────────────────────────────────────

class EventType(str, Enum):
    """Event types for state transitions."""
    NEW = "new"
    CHANGED = "changed"
    REMOVED = "removed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class ResourceEvent:
    """A deterministic event from state comparison.

    For CHANGED events, includes field-level change details.
    Events are ordered deterministically by resource key.
    """
    event_type: EventType
    resource_type: str
    resource_id: str
    previous: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    changes: tuple[FieldChange, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_type": self.event_type.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
        }
        if self.previous is not None:
            d["previous"] = self.previous
        if self.current is not None:
            d["current"] = self.current
        if self.changes:
            d["changes"] = [
                {"field": c.field, "before": c.before, "after": c.after}
                for c in self.changes
            ]
        return d


# ── History ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class HistoryEntry:
    """A record of what was previously observed/emitted.

    History = "what have we observed?" — separate from current state.
    Retention policy is deferred; this is the minimal interface.
    """
    timestamp: str
    events: tuple[ResourceEvent, ...]
    schema_version: int = STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
            "events": [e.to_dict() for e in self.events],
        }
