"""Tests for src.intelligence.attention (allocation, focus, triage)."""
from __future__ import annotations

import pytest

from src.intelligence.attention import (
    CONTEXT_FOCUS_LABELS,
    ReportMode,
    allocation_for,
    deterministic_focus,
    is_meaningful,
    reconcile,
    triage,
)
from src.intelligence.correlate import Correlation
from src.intelligence.evidence import build_evidence
from src.intelligence.temporal import TrendProfile
from src.reporting.model import ReportEvent, Severity


def _event(
    identity: str,
    *,
    subsystem: str,
    severity: Severity = Severity.INFORMATION,
    repository: str = "owner/repo",
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="event",
        severity=severity,
        title=f"{subsystem} event",
        repository=repository,
        identity=identity,
        timestamp="2026-09-24T12:00:00+00:00",
        source=subsystem,
    )


def _pack(*events: ReportEvent):
    return build_evidence(events)


class TestFocusLabels:
    def test_extended_label_set_is_stable(self):
        assert CONTEXT_FOCUS_LABELS == (
            "attention",
            "security",
            "operations",
            "developer",
            "oss",
            "release",
            "recovery",
            "trend",
            "quiet",
            "mixed",
        )


class TestAllocation:
    @pytest.mark.parametrize(
        "subsystem",
        ["ci", "monitoring", "repository", "endpoint", "system", "security", "release"],
    )
    def test_monitoring_subsystems_reach_all_modes(self, subsystem):
        event = _event(f"{subsystem}:x:1", subsystem=subsystem)
        assert allocation_for(event) == frozenset(
            {ReportMode.IMMEDIATE, ReportMode.DAILY, ReportMode.WEEKLY}
        )

    @pytest.mark.parametrize("subsystem", ["developer", "oss"])
    def test_content_subsystems_skip_the_immediate_path(self, subsystem):
        event = _event(f"{subsystem}:x:1", subsystem=subsystem)
        assert allocation_for(event) == frozenset({ReportMode.DAILY, ReportMode.WEEKLY})

    @pytest.mark.parametrize("subsystem", ["daily", "weekly"])
    def test_brief_subsystems_stay_in_briefs(self, subsystem):
        event = _event(f"{subsystem}:x:1", subsystem=subsystem)
        assert allocation_for(event) == frozenset({ReportMode.DAILY, ReportMode.WEEKLY})


class TestDeterministicFocus:
    def test_empty_pack_is_quiet(self):
        assert deterministic_focus(_pack()) == "quiet"

    def test_open_security_finding_wins(self):
        pack = _pack(
            _event("security:a:1", subsystem="security", severity=Severity.ACTION_REQUIRED),
            _event("ci:b:1", subsystem="ci", severity=Severity.ACTION_REQUIRED),
        )
        assert deterministic_focus(pack) == "security"

    def test_action_required_is_attention(self):
        pack = _pack(
            _event("ci:a:1", subsystem="ci", severity=Severity.ACTION_REQUIRED)
        )
        assert deterministic_focus(pack) == "attention"

    def test_all_resolved_is_recovery(self):
        pack = _pack(
            _event("ci:a:1", subsystem="ci", severity=Severity.RESOLVED),
            _event("release:b:1", subsystem="release", severity=Severity.RESOLVED),
        )
        assert deterministic_focus(pack) == "recovery"

    def test_strict_dominant_subsystem_decides(self):
        pack = _pack(
            _event("ci:a:1", subsystem="ci"),
            _event("ci:b:1", subsystem="ci"),
            _event("oss:c:1", subsystem="oss"),
        )
        assert deterministic_focus(pack) == "operations"

    def test_tied_subsystems_are_mixed(self):
        pack = _pack(
            _event("ci:a:1", subsystem="ci"),
            _event("oss:b:1", subsystem="oss"),
        )
        assert deterministic_focus(pack) == "mixed"

    @pytest.mark.parametrize(
        ("subsystem", "label"),
        [
            ("developer", "developer"),
            ("oss", "oss"),
            ("release", "release"),
        ],
    )
    def test_dominant_labels_for_content_subsystems(self, subsystem, label):
        pack = _pack(
            _event(f"{subsystem}:a:1", subsystem=subsystem),
            _event(f"{subsystem}:b:1", subsystem=subsystem),
            _event("repository:c:1", subsystem="repository"),
        )
        assert deterministic_focus(pack) == label


class TestMeaningfulness:
    def test_empty_pack_is_not_meaningful(self):
        assert is_meaningful(_pack(), (), ()) is False

    def test_action_required_is_meaningful(self):
        pack = _pack(_event("ci:a:1", subsystem="ci", severity=Severity.ACTION_REQUIRED))
        assert is_meaningful(pack, (), ()) is True

    def test_important_is_meaningful(self):
        pack = _pack(_event("ci:a:1", subsystem="ci", severity=Severity.IMPORTANT))
        assert is_meaningful(pack, (), ()) is True

    def test_correlations_are_meaningful(self):
        pack = _pack(
            _event("ci:a:1", subsystem="ci"),
            _event("endpoint:b:1", subsystem="endpoint"),
        )
        correlation = Correlation(
            kind="multi_subsystem",
            repository="owner/repo",
            evidence_ids=("E1", "E2"),
            statement="Activity spans 2 subsystems in owner/repo.",
        )
        assert is_meaningful(pack, (correlation,), ()) is True

    def test_recurring_trend_is_meaningful(self):
        pack = _pack(_event("ci:a:1", subsystem="ci"))
        trend = TrendProfile(
            key="ci|owner/repo|event",
            subsystem="ci",
            repository="owner/repo",
            event_type="event",
            conditions=3,
            state="recurring",
        )
        assert is_meaningful(pack, (), (trend,)) is True

    def test_four_informational_items_are_meaningful(self):
        pack = _pack(
            *[_event(f"ci:a:{index}", subsystem="ci") for index in range(4)]
        )
        assert is_meaningful(pack, (), ()) is True

    def test_three_quiet_items_are_not_meaningful(self):
        pack = _pack(
            *[_event(f"ci:a:{index}", subsystem="ci") for index in range(3)]
        )
        assert is_meaningful(pack, (), ()) is False


class TestReconcile:
    def test_invalid_laya_label_falls_back(self):
        assert reconcile("operations", "banana") == "operations"

    def test_deterministic_security_wins(self):
        assert reconcile("security", "oss") == "security"

    def test_deterministic_attention_wins(self):
        assert reconcile("attention", "quiet") == "attention"

    def test_laya_can_quiet_a_non_urgent_focus(self):
        assert reconcile("operations", "quiet") == "quiet"

    def test_laya_refines_within_allowed_labels(self):
        assert reconcile("operations", "developer") == "developer"


class TestTriage:
    def test_disabled_laya_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "false")
        assert triage("digest") is None

    def test_laya_decision_uses_context_labels(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        seen: dict = {}

        def loader():
            agent = type("Agent", (), {})

            def predict(state, spec):
                seen["labels"] = spec["decision"]["criteria"]
                return {"answers": {"decision": {"choice": "security", "confidence": 0.9}}}

            agent.predict = predict
            return agent

        assert triage("digest", loader=loader) == ("security", 0.9)
        assert tuple(seen["labels"]) == CONTEXT_FOCUS_LABELS
