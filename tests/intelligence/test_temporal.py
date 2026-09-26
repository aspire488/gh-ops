"""Tests for src.intelligence.temporal (ledger temporal classification)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.intelligence.temporal import (
    TemporalState,
    build_profiles,
    build_trends,
    classify_entry,
)

_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _entry(
    condition: str = "ci:owner/repo/run/1",
    *,
    severity: str = "important",
    status: str = "notified",
    observations: int = 1,
    last_observed: str = "2026-09-24T00:00:00+00:00",
    first_observed: str = "2026-09-20T00:00:00+00:00",
    reopened: bool | None = None,
    announced_severity: str = "",
    subsystem: str = "ci",
    repository: str = "owner/repo",
    event_type: str = "failure",
) -> dict:
    entry = {
        "condition": condition,
        "severity": severity,
        "status": status,
        "observations": observations,
        "first_observed": first_observed,
        "last_observed": last_observed,
        "timestamp": first_observed,
        "subsystem": subsystem,
        "repository": repository,
        "event_type": event_type,
    }
    if reopened:
        entry["reopened"] = True
    if announced_severity:
        entry["announced_severity"] = announced_severity
    return entry


class TestClassifyEntry:
    def test_resolved_severity_is_recovered(self):
        profile = classify_entry(_entry(severity="resolved", status="resolved"), now=_NOW)
        assert profile.state is TemporalState.RECOVERED

    def test_resolved_status_is_recovered(self):
        profile = classify_entry(_entry(status="resolved"), now=_NOW)
        assert profile.state is TemporalState.RECOVERED

    def test_escalation_beats_other_states(self):
        profile = classify_entry(
            _entry(
                severity="action_required",
                announced_severity="important",
                observations=5,
                reopened=True,
            ),
            now=_NOW,
        )
        assert profile.state is TemporalState.ESCALATING

    def test_reopened_beats_stale_and_persistent(self):
        profile = classify_entry(
            _entry(
                reopened=True,
                observations=5,
                status="new",
                last_observed="2026-09-01T00:00:00+00:00",
            ),
            now=_NOW,
        )
        assert profile.state is TemporalState.REOPENED

    def test_stale_when_active_and_not_reobserved(self):
        profile = classify_entry(
            _entry(last_observed="2026-09-01T00:00:00+00:00", observations=4),
            now=_NOW,
        )
        assert profile.state is TemporalState.STALE

    def test_fresh_active_condition_persistence_by_observations(self):
        assert (
            classify_entry(_entry(observations=1), now=_NOW).state
            is TemporalState.NEW
        )
        assert (
            classify_entry(_entry(observations=2), now=_NOW).state
            is TemporalState.ONGOING
        )
        assert (
            classify_entry(_entry(observations=3), now=_NOW).state
            is TemporalState.PERSISTENT
        )

    def test_missing_observation_counter_treated_as_new(self):
        entry = _entry()
        del entry["observations"]
        assert classify_entry(entry, now=_NOW).state is TemporalState.NEW


class TestProfileSummary:
    def test_persistent_and_ongoing_summaries_carry_counts(self):
        assert (
            classify_entry(_entry(observations=5), now=_NOW).summary
            == "persistent across 5 observations"
        )
        assert (
            classify_entry(_entry(observations=2), now=_NOW).summary
            == "ongoing across 2 observations"
        )

    def test_other_states_use_state_name(self):
        assert classify_entry(_entry(status="resolved"), now=_NOW).summary == "recovered"


class TestBuildProfiles:
    def test_keyed_by_condition_first_sorted_key_wins(self):
        ledger = {
            "ci:owner/repo/run/2:failure": _entry(
                "ci:owner/repo/run/2", observations=1
            ),
            "ci:owner/repo/run/1:failure": _entry(
                "ci:owner/repo/run/1", observations=5
            ),
        }
        profiles = build_profiles(ledger, now=_NOW)
        assert set(profiles) == {"ci:owner/repo/run/1", "ci:owner/repo/run/2"}
        assert profiles["ci:owner/repo/run/1"].state is TemporalState.PERSISTENT

    def test_failure_and_recovery_share_one_condition_profile(self):
        ledger = {
            "ci:owner/repo/run/1:failure": _entry("ci:owner/repo/run/1"),
            "ci:owner/repo/run/1:recovery": _entry(
                "ci:owner/repo/run/1", severity="resolved", status="resolved"
            ),
        }
        profiles = build_profiles(ledger, now=_NOW)
        # Sorted keys put the failure first; one profile represents the condition.
        assert profiles["ci:owner/repo/run/1"].state is TemporalState.NEW


class TestBuildTrends:
    def test_three_conditions_in_group_is_recurring(self):
        ledger = {
            f"ci:owner/repo/run/{index}:failure": _entry(
                f"ci:owner/repo/run/{index}"
            )
            for index in range(1, 4)
        }
        trends = build_trends(ledger, now=_NOW)
        assert len(trends) == 1
        assert trends[0].state == "recurring"
        assert trends[0].conditions == 3

    def test_two_resolved_conditions_is_quieted(self):
        ledger = {
            f"ci:owner/repo/run/{index}:recovery": _entry(
                f"ci:owner/repo/run/{index}",
                severity="resolved",
                status="resolved",
            )
            for index in (1, 2)
        }
        trends = build_trends(ledger, now=_NOW)
        assert len(trends) == 1
        assert trends[0].state == "quieted"

    def test_recurring_wins_over_fully_resolved(self):
        ledger = {
            f"ci:owner/repo/run/{index}:recovery": _entry(
                f"ci:owner/repo/run/{index}",
                severity="resolved",
                status="resolved",
            )
            for index in (1, 2, 3)
        }
        trends = build_trends(ledger, now=_NOW)
        assert [trend.state for trend in trends] == ["recurring"]

    def test_two_conditions_with_one_still_open_is_not_a_trend(self):
        ledger = {
            "ci:owner/repo/run/1:recovery": _entry(
                "ci:owner/repo/run/1", severity="resolved", status="resolved"
            ),
            "ci:owner/repo/run/2:failure": _entry("ci:owner/repo/run/2"),
        }
        assert build_trends(ledger, now=_NOW) == ()

    def test_single_condition_is_not_a_trend(self):
        ledger = {"ci:owner/repo/run/1:failure": _entry("ci:owner/repo/run/1")}
        assert build_trends(ledger, now=_NOW) == ()

    def test_groups_split_by_subsystem_repository_and_type(self):
        ledger = {
            **{
                f"ci:owner/repo/run/{index}:failure": _entry(
                    f"ci:owner/repo/run/{index}"
                )
                for index in (1, 2, 3)
            },
            **{
                f"ci:owner/other/run/{index}:failure": _entry(
                    f"ci:owner/other/run/{index}", repository="owner/other"
                )
                for index in (1, 2, 3)
            },
        }
        trends = build_trends(ledger, now=_NOW)
        assert [trend.repository for trend in trends] == ["owner/other", "owner/repo"]

    def test_line_is_human_readable(self):
        ledger = {
            f"ci:owner/repo/run/{index}:failure": _entry(
                f"ci:owner/repo/run/{index}"
            )
            for index in range(1, 4)
        }
        line = build_trends(ledger, now=_NOW)[0].line
        assert line == "CI failure in owner/repo recurring across 3 conditions"
