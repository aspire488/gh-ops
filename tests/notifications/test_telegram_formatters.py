"""Tests for Telegram message formatting (Phase 7).

Covers escaping of untrusted GitHub text, message splitting, long messages,
and deterministic output.

Expectations are written with ``esc(...)`` (the real escaping utility) because
every piece of text placed in a message is escaped, including the title.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.monitor import MonitorStatus
from src.developer import ActivityRecord, ActivityType, generate_report
from src.notifications.telegram import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    format_developer_report,
    format_monitor_alerts,
    format_oss_opportunities,
)
from src.utils.text import escape_markdown_v2 as esc
from tests.notifications._fakes import (
    make_developer_report,
    make_monitor_result,
    make_opportunity,
)

# ── Helpers ──────────────────────────────────────────────────────


def _strip_title(message: str) -> str:
    """Remove the bold title from a message if it has one."""
    if message.startswith("*"):
        end = message.index("*", 1)
        return message[end + 1:].lstrip("\n")
    return message


def _unescaped_asterisks(text: str) -> int:
    """Count asterisks that are not part of an escape sequence."""
    return sum(
        1
        for index, char in enumerate(text)
        if char == "*" and (index == 0 or text[index - 1] != "\\")
    )


def _assert_well_formed(messages: list[str], max_length: int) -> None:
    """Assert every message fits and contains no broken MarkdownV2 entity."""
    assert messages, "expected at least one message"

    for message in messages:
        assert 0 < len(message) <= max_length
        # A message must never end in the middle of an escape sequence.
        trailing = len(message) - len(message.rstrip("\\"))
        assert trailing % 2 == 0

    # The bold title is the only unescaped markup, and only in the first message.
    assert _unescaped_asterisks(messages[0]) == 2
    for message in messages[1:]:
        assert _unescaped_asterisks(message) == 0


# ── Escaping ─────────────────────────────────────────────────────


class TestEscaping:
    def test_monitor_summary_is_escaped(self):
        result = make_monitor_result(MonitorStatus.ALERT, summary="v1.0.0 failed!")
        message = format_monitor_alerts([result])[0]
        assert esc("v1.0.0 failed!") in message
        assert "v1.0.0 failed!" not in message

    def test_oss_title_is_escaped(self):
        opp = make_opportunity(title="Fix [bug] in v2.0 (urgent)")
        message = format_oss_opportunities([opp])[0]
        assert esc("Fix [bug] in v2.0 (urgent)") in message

    def test_oss_repo_and_url_are_escaped(self):
        opp = make_opportunity(html_url="https://github.com/octo/example/issues/42")
        message = format_oss_opportunities([opp])[0]
        assert esc("https://github.com/octo/example/issues/42") in message

    def test_developer_report_content_is_escaped(self):
        report = make_developer_report()
        message = format_developer_report(report)[0]
        # format_summary renders the username; escaping must survive it.
        assert "@someone" in message

    def test_underscores_and_backticks_are_escaped(self):
        opp = make_opportunity(title="a_b `code` ~x~")
        message = format_oss_opportunities([opp])[0]
        assert "a\\_b" in message
        assert "\\`code\\`" in message
        assert "\\~x\\~" in message

    def test_newlines_are_preserved_unescaped(self):
        result = make_monitor_result(MonitorStatus.ALERT, summary="line one")
        message = format_monitor_alerts([result])[0]
        assert "\n" in message
        assert "\\n" not in message

    def test_hostile_title_cannot_break_the_entity_structure(self):
        opp = make_opportunity(title="*bold* [link](http://evil) _italic_")
        _assert_well_formed(format_oss_opportunities([opp]), TELEGRAM_MAX_MESSAGE_LENGTH)

    def test_trailing_backslash_is_escaped_not_dropped(self):
        result = make_monitor_result(MonitorStatus.ALERT, summary="trailing\\")
        message = format_monitor_alerts([result])[0]
        # One backslash -> escaped pair; context lines may follow the title.
        assert esc("trailing\\") in message
        assert "trailing\\" not in message.replace(esc("trailing\\"), "")
        _assert_well_formed([message], TELEGRAM_MAX_MESSAGE_LENGTH)


# ── Monitor alerts ───────────────────────────────────────────────


class TestMonitorAlerts:
    def test_only_alerts_and_errors_are_notified(self):
        results = [
            make_monitor_result(MonitorStatus.OK, resource="ok-resource"),
            make_monitor_result(MonitorStatus.CHANGED, resource="changed-resource"),
            make_monitor_result(MonitorStatus.SKIPPED, resource="skipped-resource"),
            make_monitor_result(MonitorStatus.ALERT, resource="alert-resource"),
            make_monitor_result(MonitorStatus.ERROR, resource="error-resource"),
        ]
        message = format_monitor_alerts(results)[0]
        assert esc("alert-resource") in message
        assert esc("error-resource") in message
        assert esc("ok-resource") not in message
        assert esc("changed-resource") not in message
        assert esc("skipped-resource") not in message

    def test_nothing_to_notify_returns_no_messages(self):
        assert format_monitor_alerts([make_monitor_result(MonitorStatus.OK)]) == []

    def test_empty_input_returns_no_messages(self):
        assert format_monitor_alerts([]) == []

    def test_title_uses_severity_and_report_name(self):
        results = [
            make_monitor_result(MonitorStatus.ALERT),
            make_monitor_result(MonitorStatus.ERROR),
        ]
        assert f"*{esc('🟠 GH-OPS · MONITORING')}*" in format_monitor_alerts(results)[0]

    def test_includes_resource_and_summary(self):
        result = make_monitor_result(
            MonitorStatus.ALERT, monitor="release", resource="octo/example", summary="new tag"
        )
        message = format_monitor_alerts([result])[0]
        assert "octo/example" in message
        assert "new tag" in message
        assert f"*{esc('🟠 GH-OPS · MONITORING')}*" in message

    def test_input_order_is_preserved(self):
        results = [
            make_monitor_result(MonitorStatus.ALERT, resource="first"),
            make_monitor_result(MonitorStatus.ALERT, resource="second"),
        ]
        message = format_monitor_alerts(results)[0]
        assert message.index("first") < message.index("second")


# ── OSS opportunities ────────────────────────────────────────────


class TestOssOpportunities:
    def test_formats_a_list_of_opportunities(self):
        message = format_oss_opportunities([make_opportunity()])[0]
        assert esc("octo/example#42") in message
        assert esc("Fix the thing") in message

    def test_accepts_a_hunter_result_like_object(self):
        class FakeHunterResult:
            opportunities = [
                make_opportunity(),
                make_opportunity(issue_number=43, issue_id=10043),
            ]

        message = format_oss_opportunities(FakeHunterResult())[0]
        assert esc("octo/example#42") in message
        assert esc("octo/example#43") in message

    def test_limit_is_applied(self):
        items = [make_opportunity(issue_number=n, issue_id=10000 + n) for n in range(1, 6)]
        message = format_oss_opportunities(items, limit=2)[0]
        assert esc("octo/example#1") in message
        assert esc("octo/example#2") in message
        assert esc("octo/example#3") not in message

    def test_limit_is_reported_in_the_title(self):
        items = [make_opportunity(issue_number=n, issue_id=10000 + n) for n in range(1, 6)]
        message = format_oss_opportunities(items, limit=2)[0]
        assert f"*{esc('gh-ops OSS opportunities (2)')}*" in message

    def test_negative_limit_means_no_limit(self):
        items = [make_opportunity(issue_number=n, issue_id=10000 + n) for n in range(1, 13)]
        message = format_oss_opportunities(items, limit=-1)[0]
        assert f"*{esc('gh-ops OSS opportunities (12)')}*" in message

    def test_empty_input_returns_no_messages(self):
        assert format_oss_opportunities([]) == []

    def test_objects_without_identity_are_skipped(self):
        class NotAnOpportunity:
            pass

        assert format_oss_opportunities([NotAnOpportunity()]) == []

    def test_optional_fields_are_tolerated(self):
        opp = make_opportunity(repo_language=None, html_url="", score=0.0, repo_stars=0)
        message = format_oss_opportunities([opp])[0]
        assert esc("score 0.0") in message
        assert esc("stars 0") in message

    def test_non_numeric_score_is_tolerated(self):
        opp = make_opportunity(score="n/a")
        assert "score n/a" in format_oss_opportunities([opp])[0]

    def test_score_has_fixed_precision(self):
        opp = make_opportunity(score=12.3456789)
        assert esc("score 12.3") in format_oss_opportunities([opp])[0]

    def test_language_and_url_are_included_when_present(self):
        opp = make_opportunity(html_url="https://github.com/octo/example/issues/42")
        message = format_oss_opportunities([opp])[0]
        assert "Python" in message
        assert "issues/42" in message


# ── Developer report ─────────────────────────────────────────────


class TestDeveloperReportFormatting:
    def test_reuses_the_phase6_summary(self):
        message = format_developer_report(make_developer_report())[0]
        assert f"*{esc('gh-ops developer report')}*" in message
        assert "Total activities: 0" in message
        assert "Active days" in message

    def test_period_is_included(self):
        message = format_developer_report(make_developer_report())[0]
        assert "2025\\-01\\-01" in message
        assert "2025\\-01\\-08" in message

    def test_real_activity_is_counted(self):
        records = [
            ActivityRecord(
                activity_type=ActivityType.PR_MERGED,
                timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
                repository="octo/example",
                item_id="octo/example#1",
            ),
        ]
        report = generate_report(
            records,
            [],
            period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2025, 1, 8, tzinfo=timezone.utc),
        )
        message = format_developer_report(report)[0]
        assert "Total activities: 1" in message
        assert "PRs: 0 opened, 1 merged, 0 closed" in message


# ── Splitting and long messages ──────────────────────────────────


class TestMessageSplitting:
    def test_short_message_is_not_split(self):
        assert len(format_oss_opportunities([make_opportunity()])) == 1

    def test_long_message_is_split_into_bounded_chunks(self):
        items = [
            make_opportunity(issue_number=n, issue_id=10000 + n, title="t" * 60)
            for n in range(1, 40)
        ]
        messages = format_oss_opportunities(items, max_length=500)
        assert len(messages) > 1
        _assert_well_formed(messages, 500)

    def test_title_only_appears_in_the_first_message(self):
        items = [
            make_opportunity(issue_number=n, issue_id=10000 + n, title="t" * 60)
            for n in range(1, 40)
        ]
        messages = format_oss_opportunities(items, limit=-1, max_length=500)
        assert len(messages) > 1
        assert messages[0].startswith(f"*{esc('gh-ops OSS opportunities (39)')}*")
        assert all(esc("gh-ops OSS opportunities") not in m for m in messages[1:])

    def test_packaging_is_dense(self):
        """Blocks should be packed, not emitted one message per block."""
        items = [make_opportunity(issue_number=n, issue_id=10000 + n) for n in range(1, 5)]
        assert len(format_oss_opportunities(items, max_length=4096)) == 1

    def test_pathological_escaping_stays_within_the_limit(self):
        # Every character needs escaping, so the escaped form is twice as long.
        result = make_monitor_result(MonitorStatus.ALERT, summary="!" * 400)
        messages = format_monitor_alerts([result], max_length=200)
        assert len(messages) > 1
        _assert_well_formed(messages, 200)

    def test_no_content_is_lost_when_escaping_expands(self):
        summary = "!" * 300
        result = make_monitor_result(MonitorStatus.ALERT, summary=summary)
        messages = format_monitor_alerts([result], max_length=200)
        joined = "".join(_strip_title(m) for m in messages)
        assert joined.count("\\!") == len(summary)

    def test_dangling_escape_is_repaired_at_split_boundaries(self):
        # Sweep limits so that a cut lands immediately after an escaping backslash.
        for max_length in range(60, 140):
            result = make_monitor_result(MonitorStatus.ALERT, summary="." * 200)
            _assert_well_formed(
                format_monitor_alerts([result], max_length=max_length), max_length
            )

    def test_every_character_survives_a_sweep_of_limits(self):
        summary = "." * 120
        for max_length in range(60, 140):
            result = make_monitor_result(MonitorStatus.ALERT, summary=summary)
            messages = format_monitor_alerts([result], max_length=max_length)
            joined = "".join(_strip_title(m) for m in messages)
            assert joined.count("\\.") == len(summary)

    def test_tiny_max_length_is_rejected(self):
        with pytest.raises(ValueError):
            format_monitor_alerts([make_monitor_result(MonitorStatus.ALERT)], max_length=5)

    def test_zero_max_length_is_rejected(self):
        with pytest.raises(ValueError):
            format_monitor_alerts([make_monitor_result(MonitorStatus.ALERT)], max_length=0)

    def test_negative_max_length_is_rejected(self):
        with pytest.raises(ValueError):
            format_monitor_alerts([make_monitor_result(MonitorStatus.ALERT)], max_length=-1)

    def test_custom_separator_is_used(self):
        results = [
            make_monitor_result(MonitorStatus.ALERT, resource="first"),
            make_monitor_result(MonitorStatus.ALERT, resource="second"),
        ]
        message = format_monitor_alerts(results, separator="\n")[0]
        assert "\n\n" not in _strip_title(message)

    def test_developer_report_is_split_when_long(self):
        messages = format_developer_report(make_developer_report(), max_length=120)
        _assert_well_formed(messages, 120)


# ── Determinism ──────────────────────────────────────────────────


class TestDeterministicFormatting:
    def test_monitor_alerts_are_deterministic(self):
        results = [make_monitor_result(MonitorStatus.ALERT, summary="v1.0 failed!")]
        assert format_monitor_alerts(results) == format_monitor_alerts(results)

    def test_oss_opportunities_are_deterministic(self):
        items = [make_opportunity(), make_opportunity(issue_number=2, issue_id=10002)]
        assert format_oss_opportunities(items) == format_oss_opportunities(items)

    def test_developer_report_is_deterministic(self):
        report = make_developer_report()
        assert format_developer_report(report) == format_developer_report(report)

    def test_split_output_is_deterministic(self):
        items = [
            make_opportunity(issue_number=n, issue_id=10000 + n, title="t" * 60)
            for n in range(1, 40)
        ]
        assert (
            format_oss_opportunities(items, max_length=400)
            == format_oss_opportunities(items, max_length=400)
        )

    def test_escaping_utility_is_reused_verbatim(self):
        assert esc("a.b!") == "a\\.b\\!"

    def test_no_message_exceeds_the_telegram_limit_by_default(self):
        items = [
            make_opportunity(issue_number=n, issue_id=10000 + n, title="t" * 200)
            for n in range(1, 60)
        ]
        messages = format_oss_opportunities(items)
        assert all(len(m) <= TELEGRAM_MAX_MESSAGE_LENGTH for m in messages)
