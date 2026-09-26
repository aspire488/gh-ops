"""Transport-boundary UX contract tests for every Telegram path jobs use.

Captures the exact outbound HTTP payload (chat_id, text, parse_mode) plus the
NotificationResult.topic, without any network I/O. Covers the 18 golden
examples, silence, at-least-once reporting, MarkdownV2 final-payload escaping,
topic routing, product language, and job fatal-failure isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.core.models import CurrentState
from src.core.monitor import MonitorStatus
from src.intelligence.security import SecurityFinding, analyze_security
from src.jobs import jobs as jobs_module
from src.notifications.telegram import (
    TOPIC_ALERTS,
    TOPIC_DEVELOPER_REPORT,
    TOPIC_OSS_OPPORTUNITIES,
    TOPIC_RUN_SUMMARY,
    TOPIC_SECURITY,
    format_monitor_alerts,
    format_oss_opportunities,
    format_oss_run_summary,
    format_run_summary,
    format_security_alerts,
    format_security_summary,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
    notify_report,
    notify_security_alerts,
)
from src.reporting.builders import (
    build_daily_brief,
    build_report,
    build_weekly_brief,
)
from src.reporting.model import ReportEvent, Severity
from src.utils.text import escape_markdown_v2 as esc
from tests.notifications._capture import CaptureBoundary
from tests.notifications._fakes import make_developer_report, make_monitor_result, make_opportunity

# ── Product language (forbidden Telegram strings) ────────────────

FORBIDDEN_TELEGRAM_STRINGS: tuple[str, ...] = (
    "Run Summary",
    "Monitoring Complete",
    "OSS Hunter Complete",
    "Security Scan Complete",
    "Alerts: 0",
    "State persisted",
    "State: persisted",
    "Cache restored",
    "resource key",
    "schema_version",
    "reporting_events",
    "security_notified",
    "oss_opportunities",
    "0 opportunities",
    "No new qualifying opportunities found",
)

_END = datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc)
_START = datetime(2026, 9, 23, 15, 30, tzinfo=timezone.utc)


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


def _report_event(
    identity: str = "ci:owner/repo/run/1:failure",
    *,
    severity: Severity = Severity.IMPORTANT,
    subsystem: str = "ci",
    repository: str = "owner/repo",
    title: str = "CI failed",
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="failure",
        severity=severity,
        title=title,
        repository=repository,
        identity=identity,
        timestamp="2026-09-24T12:00:00+00:00",
        source=subsystem,
    )


def _assert_no_forbidden(text: str) -> None:
    for needle in FORBIDDEN_TELEGRAM_STRINGS:
        assert needle not in text, f"forbidden string {needle!r} reached Telegram: {text!r}"


# ── 18 golden examples at the transport boundary ─────────────────


class TestGoldenExamples:
    def test_01_ci_p0_action_required(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.ALERT,
                    monitor="ci",
                    resource="aspire488/gh-ops",
                    summary="build failed on main",
                    metadata={"branch": "main", "event_type": "failure"},
                )
            ],
        )
        assert result.ok
        assert payloads and payloads[0].topic == TOPIC_ALERTS
        assert payloads[0].parse_mode == "MarkdownV2"
        assert payloads[0].chat_id
        assert payloads[0].text.startswith(f"*{esc('🔴 GH-OPS · CI')}*")
        _assert_no_forbidden(payloads[0].text)

    def test_02_ci_important_feature_branch(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.ALERT,
                    monitor="ci",
                    resource="aspire488/gh-ops",
                    summary="build failed",
                    metadata={"branch": "feature/x", "event_type": "failure"},
                )
            ],
        )
        assert result.ok
        assert payloads[0].text.startswith(f"*{esc('🟠 GH-OPS · CI')}*")

    def test_03_ci_recovery_resolved(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.OK,
                    monitor="ci",
                    resource="aspire488/gh-ops",
                    summary="build recovered",
                    metadata={"branch": "main", "event_type": "recovery"},
                )
            ],
        )
        assert result.ok
        assert payloads[0].text.startswith(f"*{esc('🟢 GH-OPS · CI')}*")

    def test_04_security_actionable_batch(self):
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(
            notify_security_alerts, [_finding()]
        )
        assert result.ok
        assert payloads[0].topic == TOPIC_SECURITY
        assert payloads[0].text.startswith(f"*{esc('🔴 GH-OPS · SECURITY (1)')}*")
        assert esc("🔴 [CRITICAL]") in payloads[0].text
        assert "lodash" in payloads[0].text
        _assert_no_forbidden(payloads[0].text)

    def test_05_security_recovery_report(self):
        recovery = [
            _report_event(
                identity="security:octo/repo/dependabot/1:alert_removed",
                severity=Severity.RESOLVED,
                subsystem="security",
                title="alert removed",
            )
        ]
        title, blocks = build_report(recovery, report_name="SECURITY")
        assert title == "🟢 GH-OPS · SECURITY"
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(
            notify_report, title, blocks, topic=TOPIC_SECURITY
        )
        assert result.ok
        assert payloads[0].text.startswith(f"*{esc('🟢 GH-OPS · SECURITY')}*")

    def test_06_oss_single_opportunity(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_oss_opportunities, [make_opportunity()]
        )
        assert result.ok
        assert payloads[0].topic == TOPIC_OSS_OPPORTUNITIES
        assert payloads[0].text.startswith(
            f"*{esc('🔵 GH-OPS · OSS OPPORTUNITIES (1)')}*"
        )
        _assert_no_forbidden(payloads[0].text)

    def test_07_oss_multiple_opportunities(self):
        items = [
            make_opportunity(),
            make_opportunity(issue_number=43, issue_id=10043),
        ]
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(notify_oss_opportunities, items)
        assert result.ok
        assert payloads[0].text.startswith(
            f"*{esc('🔵 GH-OPS · OSS OPPORTUNITIES (2)')}*"
        )
        assert esc("octo/example#42") in payloads[0].text
        assert esc("octo/example#43") in payloads[0].text

    def test_08_oss_duplicate_only_is_silence(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(notify_oss_opportunities, [])
        assert result.skipped
        assert result.skip_reason == "nothing to send"
        assert payloads == []
        assert capture.session.call_count == 0

    def test_09_developer_report(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_developer_report, make_developer_report()
        )
        assert result.ok
        assert payloads[0].topic == TOPIC_DEVELOPER_REPORT
        assert payloads[0].text.startswith(f"*{esc('🔵 GH-OPS · DEVELOPER')}*")

    def test_10_daily_brief(self):
        built = build_daily_brief(
            [_report_event(repository="aspire488/gh-ops")],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
        )
        assert built is not None
        title, blocks = built
        assert title.startswith("🔵 GH-OPS · ")
        assert "DAILY BRIEF" in title
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_report, title, blocks, topic=TOPIC_RUN_SUMMARY
        )
        assert result.ok
        assert payloads[0].topic == TOPIC_RUN_SUMMARY
        assert "DAILY BRIEF" in payloads[0].text
        _assert_no_forbidden(payloads[0].text)

    def test_11_weekly_brief(self):
        built = build_weekly_brief(
            [_report_event(repository="aspire488/gh-ops")],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
            developer_events=3,
        )
        assert built is not None
        title, blocks = built
        assert "WEEKLY BRIEF" in title
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_report, title, blocks, topic=TOPIC_RUN_SUMMARY
        )
        assert result.ok
        assert "WEEKLY BRIEF" in payloads[0].text
        _assert_no_forbidden(payloads[0].text)

    def test_12_data_quality_line_in_brief(self):
        built = build_daily_brief(
            [],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
            data_quality={"collectors": 4, "incomplete": 1, "completeness_pct": 75.0},
        )
        assert built is not None
        title, blocks = built
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_report, title, blocks, topic=TOPIC_RUN_SUMMARY
        )
        assert result.ok
        assert "Data quality" in payloads[0].text
        assert "1/4 collectors incomplete" in payloads[0].text

    def test_13_system_failure_has_no_telegram_path(self, monkeypatch):
        """Fatal job failure must never reach any notify_* helper."""
        notify_calls: list[str] = []

        def _record(name):
            def _fn(*_args, **_kwargs):
                notify_calls.append(name)
                raise AssertionError(f"{name} must not run on fatal failure")

            return _fn

        for name in (
            "notify_monitor_alerts",
            "notify_report",
            "notify_developer_report",
            "notify_oss_opportunities",
            "notify_security_alerts",
        ):
            monkeypatch.setattr(jobs_module, name, _record(name))

        def boom():
            raise RuntimeError("unreadable config")

        monkeypatch.setattr(jobs_module, "load_runtime_config", boom)
        result = jobs_module.job_daily()
        assert result.success is False
        assert notify_calls == []

    def test_14_silent_monitoring_produces_no_http(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts, [make_monitor_result(MonitorStatus.OK)]
        )
        assert result.skipped
        assert result.skip_reason == "nothing to send"
        assert payloads == []
        assert capture.session.call_count == 0

    def test_15_duplicate_security_batch_is_silence(self):
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(notify_security_alerts, [])
        assert result.skipped
        assert payloads == []
        assert capture.session.call_count == 0

    def test_16_system_failure_routes_to_alerts(self):
        """gh-ops's own failing workflow announces as GH-OPS · SYSTEM."""
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.ALERT,
                    monitor="ci",
                    resource="aspire488/gh-ops/run/123",
                    summary="Security workflow failed",
                    metadata={
                        "event_type": "failure",
                        "workflow_name": "Security",
                        "branch": "master",
                    },
                )
            ],
        )
        assert result.ok
        assert payloads and payloads[0].topic == TOPIC_ALERTS
        assert payloads[0].text.startswith(f"*{esc('🔴 GH-OPS · SYSTEM')}*")
        _assert_no_forbidden(payloads[0].text)

    def test_17_system_success_is_silence(self):
        """A successful gh-ops workflow run produces no message at all."""
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.OK,
                    monitor="ci",
                    resource="aspire488/gh-ops/run/124",
                    summary="Daily workflow succeeded",
                    metadata={"event_type": "success", "workflow_name": "Daily"},
                )
            ],
        )
        assert result.skipped
        assert result.skip_reason == "nothing to send"
        assert payloads == []
        assert capture.session.call_count == 0

    def test_18_system_recovery_is_resolved(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.OK,
                    monitor="ci",
                    resource="aspire488/gh-ops/run/125",
                    summary="Monitoring workflow recovered",
                    metadata={"event_type": "recovery", "workflow_name": "Monitoring"},
                )
            ],
        )
        assert result.ok
        assert payloads[0].text.startswith(f"*{esc('🟢 GH-OPS · SYSTEM')}*")


# ── Silence contract ─────────────────────────────────────────────


class TestSilenceContract:
    def test_empty_monitor_batch(self):
        capture = CaptureBoundary()
        result, _ = capture.capture_notify(notify_monitor_alerts, [])
        assert result.skipped
        assert capture.session.call_count == 0

    def test_non_notifiable_statuses_are_silent(self):
        for status in (
            MonitorStatus.OK,
            MonitorStatus.CHANGED,
            MonitorStatus.SKIPPED,
        ):
            capture = CaptureBoundary()
            result, payloads = capture.capture_notify(
                notify_monitor_alerts, [make_monitor_result(status)]
            )
            assert result.skipped, status
            assert payloads == []

    def test_empty_security_actionable_is_silent(self):
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(notify_security_alerts, [])
        assert result.skipped
        assert payloads == []

    def test_empty_oss_batch_is_silent(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(notify_oss_opportunities, [])
        assert result.skipped
        assert payloads == []

    def test_quiet_daily_brief_builder_is_none(self):
        assert build_daily_brief([], repository_count=6) is None

    def test_quiet_weekly_brief_builder_is_none(self):
        assert build_weekly_brief([], repository_count=0) is None


# ── At-least-once delivery reporting ─────────────────────────────


class TestAtLeastOnceReporting:
    def test_oss_delivery_failure_is_not_ok(self):
        from src.notifications.telegram import TelegramError

        capture = CaptureBoundary(topics=(TOPIC_OSS_OPPORTUNITIES,))

        class BoomSession:
            def request(self, **_kwargs):
                raise TelegramError("delivery failed", operation="sendMessage")

            def close(self):
                pass

        capture.transport._session = BoomSession()
        result = capture.notifier.send(
            format_oss_opportunities([make_opportunity()]), TOPIC_OSS_OPPORTUNITIES
        )
        assert result.ok is False
        assert result.failed_chats == 1

    def test_security_delivery_failure_is_not_ok(self):
        from src.notifications.telegram import TelegramError

        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))

        class BoomSession:
            def request(self, **_kwargs):
                raise TelegramError("delivery failed", operation="sendMessage")

            def close(self):
                pass

        capture.transport._session = BoomSession()
        result = capture.notifier.send(
            format_security_alerts([_finding()]), TOPIC_SECURITY
        )
        assert result.ok is False

    def test_successful_delivery_is_ok_for_marking(self):
        capture = CaptureBoundary()
        result = capture.send(
            format_oss_opportunities([make_opportunity()]), TOPIC_OSS_OPPORTUNITIES
        )
        assert result.ok is True
        assert result.sent == 1


# ── MarkdownV2 final-payload escaping ────────────────────────────


class TestMarkdownV2FinalPayload:
    def test_hostile_oss_title_is_escaped_exactly_once(self):
        opp = make_opportunity(title="a_b [x] (y) !*#")
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(notify_oss_opportunities, [opp])
        assert result.ok
        text = payloads[0].text
        assert payloads[0].parse_mode == "MarkdownV2"
        assert "a\\_b" in text
        assert "a\\\\_b" not in text
        assert esc("a_b") in text
        # Title bold markers are the only unescaped asterisks outside escapes.
        assert text.startswith("*")

    def test_hostile_monitor_summary_is_escaped_exactly_once(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts,
            [
                make_monitor_result(
                    MonitorStatus.ALERT,
                    summary="failed! [critical] (now) -_-=.#",
                )
            ],
        )
        assert result.ok
        text = payloads[0].text
        assert esc("failed! [critical] (now) -_-=.#") in text
        assert "failed!" not in text

    def test_url_stays_clickable_after_escaping(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_oss_opportunities,
            [make_opportunity(html_url="https://github.com/octo/example/issues/42")],
        )
        assert result.ok
        # MarkdownV2 escapes '.' and '?' in the URL; the scheme remains intact.
        assert "https://github.com/octo/example/issues/42".replace(".", "\\.") in (
            payloads[0].text
        ) or "https://github.com/octo/example/issues/42" in payloads[0].text

    def test_security_hostile_summary_is_escaped(self):
        finding = _finding(summary="evil *bold* [link](http://x)")
        messages = format_security_alerts([finding])
        assert esc("evil *bold* [link](http://x)") in messages[0]
        assert "evil *bold*" not in messages[0]


# ── Topic routing at the transport boundary ──────────────────────


class TestTopicRouting:
    def test_monitor_alerts_skip_when_not_subscribed(self):
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(
            notify_monitor_alerts, [make_monitor_result(MonitorStatus.ALERT)]
        )
        assert result.skipped
        assert payloads == []

    def test_security_only_subscription_receives_security(self):
        capture = CaptureBoundary(topics=(TOPIC_SECURITY,))
        result, payloads = capture.capture_notify(notify_security_alerts, [_finding()])
        assert result.ok
        assert payloads[0].topic == TOPIC_SECURITY

    def test_empty_topics_means_all_topics(self):
        capture = CaptureBoundary()
        result, payloads = capture.capture_notify(
            notify_monitor_alerts, [make_monitor_result(MonitorStatus.ALERT)]
        )
        assert result.ok
        assert payloads[0].topic == TOPIC_ALERTS

        result, payloads = capture.capture_notify(
            notify_oss_opportunities, [make_opportunity()]
        )
        assert result.ok
        assert payloads[-1].topic == TOPIC_OSS_OPPORTUNITIES


# ── Product language + dormant formatter isolation ───────────────


class TestProductLanguage:
    def test_active_formatter_outputs_have_no_forbidden_strings(self):
        samples: list[str] = []
        samples.extend(
            format_monitor_alerts(
                [
                    make_monitor_result(MonitorStatus.ALERT, summary="failed!"),
                    make_monitor_result(MonitorStatus.ERROR),
                ]
            )
        )
        samples.extend(format_oss_opportunities([make_opportunity()]))
        samples.extend(format_security_alerts([_finding()]))
        samples.extend(format_security_summary(analyze_security(CurrentState())))
        built = build_daily_brief(
            [_report_event()],
            coverage_start=_START,
            coverage_end=_END,
            repository_count=6,
        )
        if built is not None:
            from src.notifications.telegram import format_report

            samples.extend(format_report(*built))
        assert samples, "expected at least one formatted sample"
        for text in samples:
            _assert_no_forbidden(text)

    def test_dormant_summary_formatters_have_no_forbidden_strings(self):
        from src.notifications.telegram import OssRunSummary, RunSummary

        samples = format_run_summary(RunSummary("monitoring", 6, 12, 0, True))
        samples.extend(format_oss_run_summary(OssRunSummary(3, 12, 0, 1)))
        samples.extend(format_run_summary(RunSummary("monitoring", 6, 12, 5, False)))
        samples.extend(format_oss_run_summary(OssRunSummary(3, 12, 0, 1)))
        for text in samples:
            _assert_no_forbidden(text)

    def test_jobs_never_call_dormant_summary_formatters(self):
        source = Path("src/jobs/jobs.py").read_text(encoding="utf-8")
        for name in (
            "format_run_summary",
            "format_oss_run_summary",
            "format_security_summary",
            "notify_run_summary",
            "notify_oss_run_summary",
            "notify_security_summary",
        ):
            assert name not in source, f"jobs must not route through {name}"


# ── Security summary title remains product-shaped ────────────────


class TestSecuritySummaryContract:
    def test_security_summary_title(self):
        messages = format_security_summary(analyze_security(CurrentState()))
        assert messages
        assert esc("GH-OPS") in messages[0]
        _assert_no_forbidden(messages[0])
