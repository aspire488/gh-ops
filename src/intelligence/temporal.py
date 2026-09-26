"""Temporal intelligence over the cross-run reporting ledger.

Classifies ledger conditions and recurring patterns into verified temporal
states so briefs and interpretations can say how long something has been
going on, whether it escalated, reopened, recovered, recurred, or went
quiet — all derived from ledger data, never from model output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from src.reporting.ledger import STATUS_RESOLVED, condition_key
from src.reporting.model import (
    SEVERITY_RANK,
    SUBSYSTEM_LABEL,
    Severity,
)
from src.utils.time import parse_event_timestamp

#: An active condition with no re-observation for this long is stale.
DEFAULT_STALE_AFTER_DAYS = 7

#: Distinct conditions of one trend required to call it recurring.
RECURRING_MIN_CONDITIONS = 3

#: Distinct conditions required before a fully-resolved trend is quieted.
QUIETED_MIN_CONDITIONS = 2


class TemporalState(str, Enum):
    """Verified temporal state of one ledger condition."""

    NEW = "new"
    ONGOING = "ongoing"
    PERSISTENT = "persistent"
    ESCALATING = "escalating"
    REOPENED = "reopened"
    RECOVERED = "recovered"
    STALE = "stale"


@dataclass(frozen=True)
class TemporalProfile:
    """Temporal classification of a single ledger condition."""

    condition: str
    state: TemporalState
    observations: int
    first_observed: str
    last_observed: str

    @property
    def summary(self) -> str:
        """Short human phrase for digests and evidence lines."""
        if self.state is TemporalState.PERSISTENT:
            return f"persistent across {self.observations} observations"
        if self.state is TemporalState.ONGOING:
            return f"ongoing across {self.observations} observations"
        return self.state.value


def _escalated(entry: dict) -> bool:
    """True when the entry's severity rank improved past announce time."""
    announced = str(entry.get("announced_severity") or "")
    current = str(entry.get("severity") or "")
    if not announced or announced == current:
        return False
    try:
        return SEVERITY_RANK[Severity(current)] < SEVERITY_RANK[Severity(announced)]
    except ValueError:
        return False


def _is_stale(entry: dict, *, now: datetime, stale_after_days: int) -> bool:
    """Active condition not re-observed within the staleness window."""
    status = str(entry.get("status") or "")
    if status not in ("new", "notified", "open"):
        return False
    last = parse_event_timestamp(
        str(entry.get("last_observed") or entry.get("timestamp") or "")
    )
    if last is None:
        return False
    return last < now - timedelta(days=stale_after_days)


def classify_entry(
    entry: dict,
    *,
    now: datetime,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> TemporalProfile:
    """Classify one ledger entry into a temporal state.

    Precedence: recovered → escalating → reopened → stale → persistent
    (≥3 observations) → new (1 observation) → ongoing (2).
    """
    severity = str(entry.get("severity") or "")
    status = str(entry.get("status") or "")
    observations = max(1, int(entry.get("observations") or 1))
    first = str(entry.get("first_observed") or entry.get("timestamp") or "")
    last = str(entry.get("last_observed") or entry.get("timestamp") or "")
    cond = str(entry.get("condition") or "")

    if severity == Severity.RESOLVED.value or status == STATUS_RESOLVED:
        state = TemporalState.RECOVERED
    elif _escalated(entry):
        state = TemporalState.ESCALATING
    elif entry.get("reopened"):
        state = TemporalState.REOPENED
    elif _is_stale(entry, now=now, stale_after_days=stale_after_days):
        state = TemporalState.STALE
    elif observations >= 3:
        state = TemporalState.PERSISTENT
    elif observations <= 1:
        state = TemporalState.NEW
    else:
        state = TemporalState.ONGOING

    return TemporalProfile(
        condition=cond,
        state=state,
        observations=observations,
        first_observed=first,
        last_observed=last,
    )


def build_profiles(
    ledger: dict[str, dict],
    *,
    now: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, TemporalProfile]:
    """Temporal profiles keyed by condition (first entry per condition wins).

    Failure/recovery entries of one condition share a condition key; the
    lexicographically first ledger key (typically the failure) represents it.
    """
    current = now or datetime.now(timezone.utc)
    profiles: dict[str, TemporalProfile] = {}
    for key in sorted(ledger):
        entry = ledger[key]
        profile = classify_entry(entry, now=current, stale_after_days=stale_after_days)
        profiles.setdefault(profile.condition or condition_key(key), profile)
    return profiles


@dataclass(frozen=True)
class TrendProfile:
    """Cross-condition recurring pattern for one (subsystem, repo, type)."""

    key: str
    subsystem: str
    repository: str
    event_type: str
    conditions: int
    state: str  # "recurring" | "quieted"

    @property
    def line(self) -> str:
        """One-line trend statement for briefs and digests."""
        label = SUBSYSTEM_LABEL.get(self.subsystem, self.subsystem.upper())
        where = self.repository or "all repositories"
        if self.state == "recurring":
            return f"{label} {self.event_type} in {where} recurring across {self.conditions} conditions"
        return f"{label} {self.event_type} in {where} quieted after {self.conditions} conditions resolved"


def build_trends(
    ledger: dict[str, dict],
    *,
    now: datetime | None = None,
) -> tuple[TrendProfile, ...]:
    """Recurring and quieted trends from the whole ledger.

    - recurring: ≥3 distinct conditions share subsystem + repository + type
      (regardless of current status).
    - quieted: ≥2 distinct conditions, all resolved, and not recurring.
    """
    current = now or datetime.now(timezone.utc)
    groups: dict[str, dict] = {}
    for key in sorted(ledger):
        entry = ledger[key]
        subsystem = str(entry.get("subsystem") or "")
        repository = str(entry.get("repository") or "")
        event_type = str(entry.get("event_type") or "event")
        cond = str(entry.get("condition") or condition_key(key))
        group_key = f"{subsystem}|{repository}|{event_type}"
        group = groups.setdefault(
            group_key,
            {
                "subsystem": subsystem,
                "repository": repository,
                "event_type": event_type,
                "conditions": set(),
                "resolved": set(),
            },
        )
        group["conditions"].add(cond)
        profile = classify_entry(entry, now=current)
        if profile.state is TemporalState.RECOVERED:
            group["resolved"].add(cond)

    trends: list[TrendProfile] = []
    for group_key in sorted(groups):
        group = groups[group_key]
        conditions = group["conditions"]
        if len(conditions) >= RECURRING_MIN_CONDITIONS:
            state = "recurring"
        elif len(conditions) >= QUIETED_MIN_CONDITIONS and conditions == group["resolved"]:
            state = "quieted"
        else:
            continue
        trends.append(
            TrendProfile(
                key=group_key,
                subsystem=group["subsystem"],
                repository=group["repository"],
                event_type=group["event_type"],
                conditions=len(conditions),
                state=state,
            )
        )
    return tuple(trends)
