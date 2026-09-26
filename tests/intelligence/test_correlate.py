"""Tests for src.intelligence.correlate (deterministic cross-signal checks)."""
from __future__ import annotations

from src.intelligence.correlate import correlate
from src.intelligence.evidence import build_evidence
from src.reporting.model import ReportEvent, Severity


def _event(
    identity: str,
    *,
    subsystem: str,
    repository: str = "owner/repo",
    severity: Severity = Severity.IMPORTANT,
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


def _correlations(*events: ReportEvent):
    return correlate(build_evidence(events))


class TestCorrelationKinds:
    def test_dev_ci_when_developer_item_meets_ci(self):
        result = _correlations(
            _event("developer:owner/repo#12:issue", subsystem="developer"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        assert len(result) == 1
        assert result[0].kind == "dev_ci"
        assert "#12" in result[0].statement

    def test_security_release_wins_over_other_pairs(self):
        result = _correlations(
            _event("security:owner/repo/a:1", subsystem="security"),
            _event("release:owner/repo/v1:tag", subsystem="release"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        assert [correlation.kind for correlation in result] == ["security_release"]

    def test_security_ci(self):
        result = _correlations(
            _event("security:owner/repo/a:1", subsystem="security"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        assert [correlation.kind for correlation in result] == ["security_ci"]

    def test_release_ci(self):
        result = _correlations(
            _event("release:owner/repo/v1:tag", subsystem="release"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        assert [correlation.kind for correlation in result] == ["release_ci"]

    def test_multi_subsystem_fallback(self):
        result = _correlations(
            _event("repository:owner/repo:health", subsystem="repository"),
            _event("endpoint:owner/repo:check", subsystem="endpoint"),
        )
        assert [correlation.kind for correlation in result] == ["multi_subsystem"]

    def test_single_subsystem_produces_nothing(self):
        result = _correlations(
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
            _event("ci:owner/repo/run/2:failure", subsystem="ci"),
        )
        assert result == ()

    def test_events_without_repository_are_never_correlated(self):
        result = _correlations(
            _event("ci::run/1:failure", subsystem="ci", repository=""),
            _event("endpoint::check", subsystem="endpoint", repository=""),
        )
        assert result == ()


class TestCorrelationShape:
    def test_one_correlation_per_repository(self):
        result = _correlations(
            _event("security:owner/repo/a:1", subsystem="security"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
            _event("security:other/repo/a:1", subsystem="security", repository="other/repo"),
            _event("ci:other/repo/run/1:failure", subsystem="ci", repository="other/repo"),
        )
        assert [correlation.repository for correlation in result] == [
            "other/repo",
            "owner/repo",
        ]

    def test_evidence_ids_cover_the_whole_repository_group(self):
        result = _correlations(
            _event("security:owner/repo/a:1", subsystem="security"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        assert result[0].evidence_ids == ("E1", "E2")

    def test_statements_are_non_causal(self):
        result = _correlations(
            _event("security:owner/repo/a:1", subsystem="security"),
            _event("ci:owner/repo/run/1:failure", subsystem="ci"),
        )
        statement = result[0].statement.lower()
        for banned in ("caused", "because", "led to", "therefore"):
            assert banned not in statement
