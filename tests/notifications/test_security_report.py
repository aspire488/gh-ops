"""Tests for security Telegram formatting and delivery."""
from __future__ import annotations

from src.core.models import CurrentState
from src.intelligence.security import SecurityFinding, analyze_security
from src.notifications.telegram import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TOPIC_SECURITY,
    ChatTarget,
    TelegramConfig,
    TelegramError,
    TelegramNotifier,
    format_security_alerts,
    format_security_summary,
    notify_security_alerts,
    notify_security_summary,
)
from src.utils.text import escape_markdown_v2 as esc
from tests.notifications._fakes import FakeTransport


def _finding(**overrides) -> SecurityFinding:
    fields = {
        "identity": "octo/repo/dependabot/1",
        "repository": "octo/repo",
        "source": "dependabot",
        "package_name": "lodash",
        "severity": "critical",
        "summary": "Prototype pollution in lodash",
        "state": "open",
        "html_url": "https://github.com/octo/repo/security/dependabot/1",
    }
    fields.update(overrides)
    return SecurityFinding(**fields)


class TestFormatSecurityAlerts:
    def test_empty_returns_no_messages(self):
        assert format_security_alerts([]) == []

    def test_formats_counts_and_content(self):
        message = format_security_alerts([_finding()])[0]
        assert "CRITICAL" in message
        assert "octo/repo" in message
        assert "dependabot" in message
        assert esc("Prototype pollution in lodash") in message
        assert "lodash" in message
        # Index prefix is MarkdownV2-escaped as "1\."
        assert "1\\." in message

    def test_untrusted_text_is_escaped(self):
        finding = _finding(summary="evil *bold* [link](http://x)")
        message = format_security_alerts([finding])[0]
        assert "evil *bold*" not in message
        assert esc("evil *bold* [link](http://x)") in message

    def test_deterministic(self):
        findings = [_finding(), _finding(identity="octo/repo/dependabot/2")]
        assert format_security_alerts(findings) == format_security_alerts(findings)

    def test_report_actionable_attribute_used(self):
        state = CurrentState(resources={"security": {
            "octo/repo/dependabot/1": {
                "severity": "critical",
                "package_name": "a",
                "summary": "s",
                "state": "open",
            },
            "octo/repo/dependabot/2": {
                "severity": "low",
                "package_name": "b",
                "summary": "t",
                "state": "open",
            },
        }})
        report = analyze_security(state)
        messages = format_security_alerts(report)
        assert len(messages) == 1
        assert "dependabot/1" in messages[0] or "critical" in messages[0].lower()
        assert "low" not in messages[0].lower() or "LOW" not in messages[0]

    def test_respects_max_length(self):
        findings = [
            _finding(identity=f"octo/repo/dependabot/{i}", summary="x" * 200)
            for i in range(50)
        ]
        messages = format_security_alerts(findings, max_length=500)
        assert messages
        assert all(len(m) <= 500 for m in messages)


class TestFormatSecuritySummary:
    def test_empty_report_notes_no_alerts(self):
        from src.intelligence.security import SecurityReport

        empty = SecurityReport(
            findings=(),
            by_severity={},
            open_count=0,
            actionable=(),
            alert_severities=("critical", "high"),
        )
        message = format_security_summary(empty)[0]
        assert "Findings: 0" in message
        assert esc("No security alerts collected.") in message

    def test_reports_severity_breakdown(self):
        state = CurrentState(resources={"security": {
            "octo/repo/dependabot/1": {"severity": "critical", "state": "open"},
            "octo/repo/dependabot/2": {"severity": "low", "state": "open"},
        }})
        report = analyze_security(state)
        message = format_security_summary(report)[0]
        assert "Findings: 2" in message
        assert "critical: 1" in message
        assert "low: 1" in message
        assert "Actionable: 1" in message

    def test_deterministic(self):
        report = analyze_security(CurrentState())
        assert format_security_summary(report) == format_security_summary(report)

    def test_well_formed_under_telegram_limit(self):
        report = analyze_security(CurrentState())
        for message in format_security_summary(report):
            assert 0 < len(message) <= TELEGRAM_MAX_MESSAGE_LENGTH


class TestNotifySecurity:
    def test_alerts_toggle_is_not_used_for_batch_delivery(self):
        # Alert batches are always attempted (at-least-once); empty lists skip.
        result = notify_security_alerts([], config={"telegram": {"enabled": True}})
        assert result.topic == TOPIC_SECURITY

    def test_summary_disabled_skips(self):
        result = notify_security_summary(
            analyze_security(CurrentState()),
            config={"telegram": {"enabled": True}},
        )
        assert result.skipped
        assert result.skip_reason == "security summaries disabled"

    def test_summary_enabled_allows_delivery(self):
        result = notify_security_summary(
            analyze_security(CurrentState()),
            config={"telegram": {"enabled": True, "security_summary": True}},
        )
        assert result.topic == TOPIC_SECURITY

    def test_routes_to_security_topic(self):
        transport = FakeTransport()
        notifier = TelegramNotifier(
            TelegramConfig(
                enabled=True,
                security_summary=True,
                targets=(ChatTarget("9", (TOPIC_SECURITY,)),),
            ),
            transport=transport,
        )
        result = notifier.send(
            format_security_summary(analyze_security(CurrentState())),
            TOPIC_SECURITY,
        )
        assert result.ok
        assert transport.sent[0][0] == "9"

    def test_delivery_failure_is_isolated(self):
        transport = FakeTransport(fail_on={"1"}, error=TelegramError("unavailable"))
        notifier = TelegramNotifier(
            TelegramConfig(
                enabled=True,
                security_summary=True,
                targets=(ChatTarget("1", (TOPIC_SECURITY,)),),
            ),
            transport=transport,
        )
        result = notifier.send(format_security_alerts([_finding()]), TOPIC_SECURITY)
        assert not result.ok
