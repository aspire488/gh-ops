"""Tests for daily/weekly brief builders (repo-first, empty-omit, data quality)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.reporting.builders import (
    build_daily_brief,
    build_report,
    build_weekly_brief,
    data_quality_block,
)
from src.reporting.model import ReportEvent, Severity

_END = datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc)
_START = datetime(2026, 9, 23, 15, 30, tzinfo=timezone.utc)


def _event(
    identity: str = "ci:owner/repo/run/1:failure",
    *,
    severity: Severity = Severity.IMPORTANT,
    repository: str = "owner/repo",
    title: str = "CI failed",
) -> ReportEvent:
    return ReportEvent(
        subsystem="ci",
        event_type="failure",
        severity=severity,
        title=title,
        repository=repository,
        identity=identity,
        timestamp="2026-09-24T12:00:00+00:00",
        source="ci",
    )


class TestDataQualityBlock:
    def test_none_when_absent(self):
        assert data_quality_block(None) is None

    def test_none_when_healthy(self):
        assert data_quality_block({"collectors": 5, "incomplete": 0}) is None

    def test_none_when_empty(self):
        assert data_quality_block({}) is None

    def test_line_when_incomplete(self):
        line = data_quality_block(
            {"collectors": 5, "incomplete": 2, "completeness_pct": 60.0}
        )
        assert line is not None
        assert "2/5" in line
        assert "60" in line


class TestDailyBrief:
    def test_empty_events_returns_none(self):
        assert build_daily_brief([], repository_count=6) is None

    def test_empty_with_healthy_quality_returns_none(self):
        built = build_daily_brief(
            [],
            repository_count=6,
            data_quality={"collectors": 4, "incomplete": 0},
        )
        assert built is None

    def test_repo_first_grouping(self):
        a = _event("ci:a/run/1:failure", repository="octo/a")
        b = _event("ci:b/run/2:failure", repository="octo/b")
        built = build_daily_brief(
            [b, a],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=2,
        )
        assert built is not None
        _, blocks = built
        # Priority order: both P1, sorted by timestamp then identity —
        # repo headers appear before their events.
        joined = "\n".join(blocks)
        assert "▸ octo/a" in joined
        assert "▸ octo/b" in joined
        assert joined.index("▸ octo/a") < joined.index("• [P1] octo/a · CI")

    def test_includes_coverage_and_repository_count(self):
        built = build_daily_brief(
            [_event()],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
        )
        assert built is not None
        _, blocks = built
        assert any("Coverage:" in line for line in blocks)
        assert any("6 repositories monitored" in line for line in blocks)

    def test_data_quality_appended_only_when_failed(self):
        built = build_daily_brief(
            [_event()],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
            data_quality={"collectors": 4, "incomplete": 1, "completeness_pct": 75.0},
        )
        assert built is not None
        _, blocks = built
        assert any("Data quality" in line for line in blocks)

    def test_no_needs_attention_section_when_only_information(self):
        info = _event(severity=Severity.INFORMATION, title="FYI")
        built = build_daily_brief([info], repository_count=1)
        assert built is not None
        _, blocks = built
        assert not any(line.strip() == "Needs attention:" for line in blocks)


class TestWeeklyBrief:
    def test_empty_without_developer_events_returns_none(self):
        assert build_weekly_brief([], repository_count=0) is None

    def test_developer_events_alone_produce_brief(self):
        built = build_weekly_brief(
            [],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
            developer_events=12,
        )
        assert built is not None
        _, blocks = built
        assert any("12 developer activities" in line for line in blocks)

    def test_repo_first_grouping(self):
        built = build_weekly_brief(
            [_event(repository="octo/a")],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=1,
        )
        assert built is not None
        _, blocks = built
        assert "▸ octo/a" in blocks


class TestBuildReportStillWorks:
    def test_report_unchanged_shape(self):
        built = build_report([_event()], report_name="MONITORING")
        assert built is not None
        title, blocks = built
        assert "GH-OPS" in title
        assert any("• [P1]" in b for b in blocks)
