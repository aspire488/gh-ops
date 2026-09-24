"""Tests for reporting event lifecycle and persistent-condition suppression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.reporting.ledger import (
    STATUS_NEW,
    STATUS_NOTIFIED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    STATUS_RETIRED,
    condition_key,
    event_should_notify,
    events_for_brief,
    filter_notifiable,
    mark_briefed,
    mark_delivered,
    merge_events,
    prune_ledger,
    resolve_conditions,
)
from src.reporting.model import ReportEvent, Severity


def _event(
    identity: str = "ci:owner/repo/run/1:failure",
    *,
    severity: Severity = Severity.IMPORTANT,
    event_type: str = "failure",
    title: str = "CI failed",
    timestamp: str = "2026-09-24T12:00:00+00:00",
) -> ReportEvent:
    return ReportEvent(
        subsystem="ci",
        event_type=event_type,
        severity=severity,
        title=title,
        repository="owner/repo",
        identity=identity,
        timestamp=timestamp,
        source="ci",
    )


class TestConditionKey:
    def test_strips_event_type_suffix(self):
        assert condition_key("ci:owner/repo/run/1:failure") == "ci:owner/repo/run/1"

    def test_recovery_shares_condition_with_failure(self):
        assert condition_key("ci:owner/repo/run/1:failure") == condition_key(
            "ci:owner/repo/run/1:recovery"
        )

    def test_identity_without_suffix_unchanged(self):
        assert condition_key("owner/repo#42") == "owner/repo#42"


class TestMergeLifecycle:
    def test_first_sighting_is_new(self):
        ledger = merge_events({}, [_event()])
        entry = ledger["ci:owner/repo/run/1:failure"]
        assert entry["status"] == STATUS_NEW
        assert entry["delivered"] is False
        assert entry["condition"] == "ci:owner/repo/run/1"

    def test_delivered_then_reobserved_becomes_open(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        assert ledger["ci:owner/repo/run/1:failure"]["status"] == STATUS_NOTIFIED

        ledger = merge_events(ledger, [_event()])
        entry = ledger["ci:owner/repo/run/1:failure"]
        assert entry["status"] == STATUS_OPEN
        assert entry["delivered"] is True

    def test_resolution_delivered_marks_resolved(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])

        recovery = _event(
            "ci:owner/repo/run/1:recovery",
            severity=Severity.RESOLVED,
            event_type="recovery",
            title="CI recovered",
        )
        ledger = merge_events(ledger, [recovery])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:recovery"])
        ledger = resolve_conditions(ledger, [recovery])

        assert ledger["ci:owner/repo/run/1:recovery"]["status"] == STATUS_RESOLVED
        assert ledger["ci:owner/repo/run/1:failure"]["status"] == STATUS_RESOLVED

    def test_preserves_briefed_flags_on_remerge(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_briefed(ledger, ["ci:owner/repo/run/1:failure"], "daily")
        ledger = merge_events(ledger, [_event()])
        assert ledger["ci:owner/repo/run/1:failure"]["briefed_daily"] is True


class TestSuppression:
    def test_new_event_notifies(self):
        assert event_should_notify({}, _event()) is True

    def test_undelivered_event_notifies(self):
        ledger = merge_events({}, [_event()])
        assert event_should_notify(ledger, _event()) is True

    def test_delivered_same_severity_suppressed(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        assert event_should_notify(ledger, _event()) is False

    def test_open_status_suppressed(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        ledger = merge_events(ledger, [_event()])
        assert ledger["ci:owner/repo/run/1:failure"]["status"] == STATUS_OPEN
        assert event_should_notify(ledger, _event()) is False

    def test_escalation_notifies(self):
        ledger = merge_events({}, [_event(severity=Severity.IMPORTANT)])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        escalated = _event(severity=Severity.ACTION_REQUIRED)
        assert event_should_notify(ledger, escalated) is True

    def test_escalation_notifies_after_remerge_refreshes_severity(self):
        """merge may overwrite severity; announced_severity must still gate."""
        ledger = merge_events({}, [_event(severity=Severity.IMPORTANT)])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        ledger = merge_events(ledger, [_event(severity=Severity.ACTION_REQUIRED)])
        assert (
            ledger["ci:owner/repo/run/1:failure"]["announced_severity"]
            == Severity.IMPORTANT.value
        )
        assert event_should_notify(ledger, _event(severity=Severity.ACTION_REQUIRED))

    def test_deescalation_suppressed(self):
        ledger = merge_events({}, [_event(severity=Severity.ACTION_REQUIRED)])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        lower = _event(severity=Severity.IMPORTANT)
        assert event_should_notify(ledger, lower) is False

    def test_resolution_notifies_once_after_condition(self):
        ledger = merge_events({}, [_event()])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:failure"])
        recovery = _event(
            "ci:owner/repo/run/1:recovery",
            severity=Severity.RESOLVED,
            event_type="recovery",
        )
        assert event_should_notify(ledger, recovery) is True

        ledger = merge_events(ledger, [recovery])
        ledger = mark_delivered(ledger, ["ci:owner/repo/run/1:recovery"])
        assert event_should_notify(ledger, recovery) is False

    def test_resolved_status_suppresses_renotify(self):
        ledger = merge_events({}, [_event(severity=Severity.RESOLVED, event_type="recovery")])
        ledger = mark_delivered(ledger, [_event(severity=Severity.RESOLVED, event_type="recovery").identity])
        assert event_should_notify(
            ledger, _event(severity=Severity.RESOLVED, event_type="recovery")
        ) is False

    def test_filter_notifiable_partitions(self):
        delivered = _event("ci:a/run/1:failure")
        fresh = _event("ci:b/run/2:failure", title="other")
        ledger = merge_events({}, [delivered])
        ledger = mark_delivered(ledger, ["ci:a/run/1:failure"])

        keep = filter_notifiable(ledger, [delivered, fresh])
        assert [e.identity for e in keep] == ["ci:b/run/2:failure"]


class TestBriefsAndRetire:
    def test_events_for_brief_excludes_already_briefed(self):
        ledger = merge_events({}, [_event(), _event("ci:b/run/2:failure")])
        ledger = mark_briefed(ledger, ["ci:owner/repo/run/1:failure"], "daily")
        pending = events_for_brief(
            ledger,
            kind="daily",
            window=timedelta(hours=24),
            end=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        )
        assert [e.identity for e in pending] == ["ci:b/run/2:failure"]

    def test_prune_drops_old_entries_as_retired(self):
        old = _event(timestamp="2020-01-01T00:00:00+00:00")
        recent = _event("ci:b/run/2:failure", timestamp="2026-09-24T12:00:00+00:00")
        ledger = merge_events({}, [old, recent])
        pruned = prune_ledger(
            ledger, max_age_days=30, now=datetime(2026, 9, 24, tzinfo=timezone.utc)
        )
        assert "ci:owner/repo/run/1:failure" not in pruned
        assert "ci:b/run/2:failure" in pruned

    def test_status_retired_constant_exists(self):
        assert STATUS_RETIRED == "retired"
