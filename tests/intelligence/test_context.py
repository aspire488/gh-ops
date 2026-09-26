"""Tests for src.intelligence.context (bounded intelligence contexts)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.intelligence.context import MAX_DIGEST_CHARS, build_context
from src.reporting.model import ReportEvent, Severity

_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _event(
    identity: str,
    *,
    subsystem: str,
    severity: Severity = Severity.IMPORTANT,
    repository: str = "owner/repo",
    title: str = "something happened",
    url: str = "",
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="event",
        severity=severity,
        title=title,
        repository=repository,
        url=url,
        identity=identity,
        timestamp="2026-09-24T11:00:00+00:00",
        source=subsystem,
    )


class TestBuildContext:
    def test_empty_input_is_quiet_and_not_meaningful(self):
        context = build_context([], ledger={}, purpose="daily", now=_NOW)
        assert context.focus == "quiet"
        assert context.meaningful is False
        assert context.pack.items == ()

    def test_security_action_focuses_security_and_is_meaningful(self):
        context = build_context(
            [
                _event(
                    "security:owner/repo/a:1",
                    subsystem="security",
                    severity=Severity.ACTION_REQUIRED,
                )
            ],
            ledger={},
            purpose="daily",
            now=_NOW,
        )
        assert context.focus == "security"
        assert context.meaningful is True

    def test_grounding_sets_delegate_to_pack(self):
        context = build_context(
            [
                _event(
                    "developer:owner/repo#7:issue",
                    subsystem="developer",
                    url="https://github.com/owner/repo/issues/7",
                )
            ],
            ledger={},
            purpose="weekly",
            now=_NOW,
        )
        assert context.evidence_ids == context.pack.ids
        assert context.urls == frozenset({"https://github.com/owner/repo/issues/7"})
        assert 7 in context.item_numbers

    def test_slugs_include_trend_repositories_not_in_pack(self):
        ledger = {
            f"ci:other/repo/run/{index}:failure": {
                "subsystem": "ci",
                "repository": "other/repo",
                "event_type": "failure",
                "condition": f"ci:other/repo/run/{index}",
                "severity": "important",
                "status": "notified",
                "observations": 1,
                "timestamp": "2026-09-24T11:00:00+00:00",
                "first_observed": "2026-09-24T11:00:00+00:00",
                "last_observed": "2026-09-24T11:00:00+00:00",
            }
            for index in range(1, 4)
        }
        context = build_context(
            [_event("ci:owner/repo/run/1:failure", subsystem="ci")],
            ledger=ledger,
            purpose="daily",
            now=_NOW,
        )
        assert "other/repo" in context.slugs
        assert "owner/repo" in context.slugs


class TestDigest:
    def test_header_and_evidence_block_present(self):
        context = build_context(
            [_event("ci:owner/repo/run/1:failure", subsystem="ci")],
            ledger={},
            purpose="daily",
            now=_NOW,
        )
        digest = context.digest()
        assert digest.startswith("Purpose: daily\nFocus: operations")
        assert "E1 [ci|important] owner/repo · something happened" in digest

    def test_correlations_and_trends_appear_in_tail(self):
        ledger = {
            f"ci:owner/repo/run/{index}:failure": {
                "subsystem": "ci",
                "repository": "owner/repo",
                "event_type": "failure",
                "condition": f"ci:owner/repo/run/{index}",
                "severity": "important",
                "status": "notified",
                "observations": 1,
                "timestamp": "2026-09-24T11:00:00+00:00",
                "first_observed": "2026-09-24T11:00:00+00:00",
                "last_observed": "2026-09-24T11:00:00+00:00",
            }
            for index in range(1, 4)
        }
        context = build_context(
            [
                _event("ci:owner/repo/run/1:failure", subsystem="ci"),
                _event("endpoint:owner/repo/check:1", subsystem="endpoint"),
            ],
            ledger=ledger,
            purpose="daily",
            now=_NOW,
        )
        digest = context.digest()
        assert "Correlations:" in digest
        assert "Trends:" in digest
        assert "recurring across 3 conditions" in digest

    def test_digest_is_bounded_by_default(self):
        events = [
            _event(
                f"ci:owner/repo/run/{index}:failure",
                subsystem="ci",
                title=f"a rather long failure title number {index} " + "x" * 40,
                url=f"https://github.com/owner/repo/actions/runs/{index}",
            )
            for index in range(40)
        ]
        context = build_context(events, ledger={}, purpose="daily", now=_NOW)
        assert len(context.pack.items) == 40
        assert len(context.digest()) <= MAX_DIGEST_CHARS

    def test_small_budget_hard_truncates(self):
        events = [
            _event(f"ci:owner/repo/run/{index}:failure", subsystem="ci", title="y" * 80)
            for index in range(10)
        ]
        context = build_context(events, ledger={}, purpose="daily", now=_NOW)
        digest = context.digest(max_chars=300)
        assert len(digest) <= 300

    def test_digest_is_deterministic(self):
        events = [_event("ci:owner/repo/run/1:failure", subsystem="ci")]
        first = build_context(events, ledger={}, purpose="daily", now=_NOW).digest()
        second = build_context(events, ledger={}, purpose="daily", now=_NOW).digest()
        assert first == second
