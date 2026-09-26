"""Tests for src.reporting.narratives (brief/radar render helpers)."""
from __future__ import annotations

from src.reporting.model import ReportEvent, Severity
from src.reporting.narratives import (
    focus_blocks,
    oss_update_blocks,
    repository_signal_blocks,
    trend_blocks,
)
from tests.notifications._fakes import make_opportunity


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


class TestRepositorySignalBlocks:
    def test_two_subsystems_on_one_repo_render_a_header_and_line(self):
        blocks = repository_signal_blocks(
            [
                _event("ci:owner/repo/run/1", subsystem="ci"),
                _event("endpoint:owner/repo/check/1", subsystem="endpoint"),
            ]
        )
        assert blocks[0] == "📦 Repository Signals"
        assert blocks[1] == "owner/repo: CI×1 · ENDPOINT×1"
        assert len(blocks) == 2

    def test_single_subsystem_repo_is_omitted(self):
        assert repository_signal_blocks(
            [
                _event("ci:owner/repo/run/1", subsystem="ci"),
                _event("ci:owner/repo/run/2", subsystem="ci"),
            ]
        ) == []

    def test_event_without_repository_is_skipped(self):
        assert repository_signal_blocks(
            [
                _event("ci::run/1", subsystem="ci", repository=""),
                _event("endpoint::check/1", subsystem="endpoint", repository=""),
            ]
        ) == []

    def test_repos_sorted_and_counts_aggregated(self):
        blocks = repository_signal_blocks(
            [
                _event("ci:zeta/repo/run/1", subsystem="ci", repository="zeta/repo"),
                _event(
                    "endpoint:zeta/repo/check/1",
                    subsystem="endpoint",
                    repository="zeta/repo",
                ),
                _event("ci:alpha/repo/run/1", subsystem="ci", repository="alpha/repo"),
                _event("ci:alpha/repo/run/2", subsystem="ci", repository="alpha/repo"),
                _event(
                    "endpoint:alpha/repo/check/1",
                    subsystem="endpoint",
                    repository="alpha/repo",
                ),
            ]
        )
        assert blocks[1] == "alpha/repo: CI×2 · ENDPOINT×1"
        assert blocks[2] == "zeta/repo: CI×1 · ENDPOINT×1"


class TestTrendBlocks:
    def test_empty_lines_render_nothing(self):
        assert trend_blocks([]) == []

    def test_header_and_bullets(self):
        assert trend_blocks(["CI failure in owner/repo recurring across 3 conditions"]) == [
            "📈 Trends",
            "• CI failure in owner/repo recurring across 3 conditions",
        ]


class TestFocusBlocks:
    def test_quiet_when_nothing_needs_attention(self):
        assert focus_blocks([_event("ci:a", subsystem="ci", severity=Severity.INFORMATION)]) == []

    def test_counts_action_and_important_items(self):
        blocks = focus_blocks(
            [
                _event("ci:a", subsystem="ci", severity=Severity.ACTION_REQUIRED),
                _event("endpoint:b", subsystem="endpoint", severity=Severity.IMPORTANT),
                _event("endpoint:c", subsystem="endpoint", severity=Severity.RESOLVED),
            ]
        )
        assert blocks == ["🎯 Focus", "🔴 2 items need attention"]

    def test_empty_event_list_renders_nothing(self):
        assert focus_blocks([]) == []


class TestOssUpdateBlocks:
    def test_empty_input_renders_nothing(self):
        assert oss_update_blocks([]) == []

    def test_changed_opportunity_block(self):
        opportunity = make_opportunity(
            repo_full_name="octo/example",
            issue_number=42,
            title="Improve docs",
            comments=3,
            repo_stars=1500,
            updated_at="2026-09-24T10:00:00+00:00",
            html_url="https://github.com/octo/example/issues/42",
        )
        blocks = oss_update_blocks([opportunity])
        assert blocks[0] == "Changed opportunities"
        assert blocks[1].startswith("🔄 octo/example#42")
        assert "  Improve docs" in blocks[1]
        assert "3 comments" in blocks[1]
        assert "★1500" in blocks[1]
        assert "updated 2026-09-24" in blocks[1]
        assert "open" in blocks[1]
        assert "Open → https://github.com/octo/example/issues/42" in blocks[1]

    def test_header_never_reuses_the_pinned_oss_title(self):
        blocks = oss_update_blocks([make_opportunity(issue_number=1)])
        assert "0 opportunities" not in " ".join(blocks)
        assert "No new qualifying opportunities found" not in " ".join(blocks)
