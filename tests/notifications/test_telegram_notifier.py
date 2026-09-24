"""Tests for Telegram notification delivery (Phase 7).

Covers routing, failure isolation, safe skipping when unconfigured, and the
guarantee that the bot token never reaches logs or results.
"""
from __future__ import annotations

import pytest

from src.core.errors import ErrorCode, GhOpsError
from src.notifications.telegram import (
    TELEGRAM_TOKEN_ENV_VAR,
    ChatTarget,
    NotificationResult,
    TelegramConfig,
    TelegramError,
    TelegramNotifier,
    TelegramTransport,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
    send_notification,
)
from tests.notifications._fakes import (
    TEST_CHAT_ID,
    TEST_TOKEN,
    FakeSession,
    FakeTransport,
    assert_no_token,
    captured_logs,
    error_response,
    make_developer_report,
    make_monitor_result,
    make_opportunity,
)


def _config(**overrides) -> TelegramConfig:
    params = {
        "enabled": True,
        "targets": (ChatTarget(TEST_CHAT_ID),),
    }
    params.update(overrides)
    return TelegramConfig(**params)


def _multi_chat_config(**overrides) -> TelegramConfig:
    params = {
        "enabled": True,
        "targets": (
            ChatTarget("1", ("alerts",)),
            ChatTarget("2", ("alerts", "developer_report")),
            ChatTarget("3", ("developer_report",)),
            ChatTarget("4"),
        ),
    }
    params.update(overrides)
    return TelegramConfig(**params)


# ── Safe skipping ────────────────────────────────────────────────


class TestSafeSkipping:
    def test_disabled_is_a_noop(self):
        transport = FakeTransport()
        result = TelegramNotifier(TelegramConfig(enabled=False), transport).send(["hi"], "alerts")
        assert result.skipped is True
        assert result.skip_reason == "telegram disabled"
        assert transport.sent == []

    def test_enabled_without_targets_skips(self):
        transport = FakeTransport()
        result = TelegramNotifier(_config(targets=()), transport).send(["hi"], "alerts")
        assert result.skipped is True
        assert "no chat configured" in result.skip_reason
        assert transport.sent == []

    def test_topic_with_no_subscribers_skips(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget("1", ("developer_report",)),))
        result = TelegramNotifier(config, transport).send(["hi"], "alerts")
        assert result.skipped is True
        assert transport.sent == []

    def test_empty_messages_skip(self):
        transport = FakeTransport()
        result = TelegramNotifier(_config(), transport).send([], "alerts")
        assert result.skipped is True
        assert result.skip_reason == "nothing to send"

    def test_missing_token_skips_safely_without_raising(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        result = TelegramNotifier(_config()).send(["hi"], "alerts")
        assert result.skipped is True
        assert result.skip_reason == "telegram not configured"
        assert len(result.errors) == 1
        assert result.sent == 0

    def test_disabled_never_resolves_a_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        # No token, no transport injected: a disabled notifier must not fail.
        result = TelegramNotifier(TelegramConfig(enabled=False)).send(["hi"], "alerts")
        assert result.skipped is True
        assert result.errors == ()


# ── Delivery ─────────────────────────────────────────────────────


class TestDelivery:
    def test_sends_to_a_single_chat(self):
        transport = FakeTransport()
        result = TelegramNotifier(_config(), transport).send(["hello"], "alerts")
        assert result.sent == 1
        assert transport.sent == [(TEST_CHAT_ID, "hello")]
        assert result.ok is True

    def test_sends_every_message_to_every_chat(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget("1"), ChatTarget("2")))
        result = TelegramNotifier(config, transport).send(["a", "b"], "alerts")
        assert result.sent == 4
        assert len(transport.sent) == 4

    def test_topic_routing(self):
        transport = FakeTransport()
        result = TelegramNotifier(_multi_chat_config(), transport).send(["hi"], "alerts")
        # Chats 1, 2 and the catch-all chat 4 subscribe to alerts.
        assert sorted(chat for chat, _ in transport.sent) == ["1", "2", "4"]
        assert result.sent == 3

    def test_topic_routing_for_developer_report(self):
        transport = FakeTransport()
        TelegramNotifier(_multi_chat_config(), transport).send(["hi"], "developer_report")
        assert sorted(chat for chat, _ in transport.sent) == ["2", "3", "4"]

    def test_attempts_are_recorded_per_chat(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget("1"), ChatTarget("2")))
        result = TelegramNotifier(config, transport).send(["a", "b"], "alerts")
        assert [a.to_dict() for a in result.attempts] == [
            {"chat_id": "1", "delivered": 2, "ok": True},
            {"chat_id": "2", "delivered": 2, "ok": True},
        ]

    def test_result_serialization(self):
        transport = FakeTransport()
        payload = TelegramNotifier(_config(), transport).send(["hi"], "alerts").to_dict()
        assert payload["topic"] == "alerts"
        assert payload["sent"] == 1
        assert payload["skipped"] is False
        assert payload["failed_chats"] == 0
        assert payload["ok"] is True
        assert payload["errors"] == []

    def test_empty_message_is_refused_by_the_transport(self):
        session = FakeSession()
        transport = TelegramTransport(TEST_TOKEN, session=session, sleep=lambda s: None)
        result = TelegramNotifier(_config(), transport).send([""], "alerts")
        assert session.call_count == 0  # refused before any HTTP call
        assert result.sent == 0
        assert result.failed_chats == 1

    def test_not_ok_when_skipped(self):
        assert NotificationResult(topic="alerts", skipped=True).ok is False


# ── Failure isolation ────────────────────────────────────────────


class TestFailureIsolation:
    def test_one_failing_chat_does_not_block_others(self):
        transport = FakeTransport(fail_on={"1"})
        config = _config(targets=(ChatTarget("1"), ChatTarget("2")))
        result = TelegramNotifier(config, transport).send(["hi"], "alerts")

        assert result.sent == 1
        assert result.failed_chats == 1
        assert transport.sent == [("2", "hi")]
        assert result.ok is False

    def test_failure_stops_further_messages_for_that_chat_only(self):
        transport = FakeTransport(fail_on={"1"})
        config = _config(targets=(ChatTarget("1"), ChatTarget("2")))
        result = TelegramNotifier(config, transport).send(["a", "b"], "alerts")

        assert [a.delivered for a in result.attempts] == [0, 2]
        assert len(transport.sent) == 2  # chat 2 got both messages

    def test_errors_are_recorded_structurally(self):
        transport = FakeTransport(fail_on={TEST_CHAT_ID})
        result = TelegramNotifier(_config(), transport).send(["hi"], "alerts")
        assert len(result.errors) == 1
        assert result.errors[0]["code"] == "telegram_delivery_failed"
        assert result.errors[0]["module"] == "notifications.telegram"

    def test_all_chats_failing_is_reported(self):
        transport = FakeTransport(fail_on={"1", "2"})
        config = _config(targets=(ChatTarget("1"), ChatTarget("2")))
        result = TelegramNotifier(config, transport).send(["hi"], "alerts")
        assert result.sent == 0
        assert result.failed_chats == 2

    def test_non_telegram_ghops_errors_are_isolated_too(self):
        transport = FakeTransport(
            fail_on={TEST_CHAT_ID},
            error=GhOpsError(code=ErrorCode.INTERNAL_ERROR, message="boom", module="test"),
        )
        result = TelegramNotifier(_config(), transport).send(["hi"], "alerts")
        assert result.failed_chats == 1
        assert len(result.errors) == 1
        assert result.errors[0]["code"] == "internal_error"

    def test_delivery_never_raises_for_transport_failures(self):
        class ExplodingTransport:
            def send_message(self, chat_id: str, text: str, **kwargs):
                raise TelegramError("nope", operation="sendMessage")

        result = TelegramNotifier(_config(), ExplodingTransport()).send(["hi"], "alerts")
        assert result.failed_chats == 1


# ── Secret safety ────────────────────────────────────────────────


class TestSecretSafety:
    def test_token_never_appears_in_logs_or_results_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, TEST_TOKEN)
        transport = TelegramTransport(TEST_TOKEN, session=FakeSession(), sleep=lambda s: None)

        with captured_logs() as logs:
            notifier = TelegramNotifier(_config(), transport)
            notifier.send(["hello"], "alerts")

        assert logs, "expected the notifier to log delivery"
        assert_no_token(" ".join(logs))

    def test_token_never_appears_in_logs_or_errors_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, TEST_TOKEN)
        session = FakeSession(default=error_response(401, "Unauthorized"))
        transport = TelegramTransport(TEST_TOKEN, session=session, sleep=lambda s: None)

        with captured_logs() as logs:
            result = TelegramNotifier(_config(), transport).send(["hello"], "alerts")

        assert result.failed_chats == 1
        assert_no_token(" ".join(logs))
        assert_no_token(str(result.to_dict()))

    def test_token_never_appears_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        with captured_logs() as logs:
            result = TelegramNotifier(_config()).send(["hello"], "alerts")
        assert_no_token(" ".join(logs))
        assert_no_token(str(result.to_dict()))

    def test_config_serialization_never_carries_the_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, TEST_TOKEN)
        assert_no_token(str(_config().to_dict()))

    def test_logging_redactor_catches_telegram_tokens(self):
        """Defence-in-depth: the central log redactor recognises Telegram tokens.

        The captured-log assertions above use an unredacted handler, so they
        prove the token is never logged at all. This test proves the second
        layer works if it ever were.
        """
        import logging

        from src.utils.logging import StructuredFormatter

        record = logging.LogRecord(
            name="notifications.telegram",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=f"delivering with bot{123456789}://{TEST_TOKEN}",
            args=(),
            exc_info=None,
        )
        formatted = StructuredFormatter().format(record)
        assert_no_token(formatted)
        assert "[REDACTED]" in formatted


# ── Convenience entry points ─────────────────────────────────────


class TestConvenienceEntryPoints:
    def test_send_notification_uses_the_given_config(self):
        transport = FakeTransport()
        result = send_notification(
            ["hi"],
            "alerts",
            config={"telegram": {"enabled": True, "chat_ids": [TEST_CHAT_ID]}},
            transport=transport,
        )
        assert result.sent == 1

    def test_send_notification_with_disabled_config(self):
        transport = FakeTransport()
        result = send_notification(["hi"], "alerts", config={"telegram": {}}, transport=transport)
        assert result.skipped is True
        assert transport.sent == []

    def test_notify_monitor_alerts(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget(TEST_CHAT_ID, ("alerts",)),))
        result = notify_monitor_alerts(
            [make_monitor_result("alert")], config=config, transport=transport
        )
        assert result.topic == "alerts"
        assert result.sent == 1
        assert "MONITORING" in transport.sent[0][1]

    def test_notify_monitor_alerts_with_nothing_to_report(self):
        transport = FakeTransport()
        result = notify_monitor_alerts(
            [make_monitor_result("ok")], config=_config(), transport=transport
        )
        assert result.skipped is True
        assert transport.sent == []

    def test_notify_oss_opportunities(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget(TEST_CHAT_ID, ("oss_opportunities",)),))
        result = notify_oss_opportunities(
            [make_opportunity()], config=config, transport=transport
        )
        assert result.topic == "oss_opportunities"
        assert result.sent == 1
        assert "octo/example" in transport.sent[0][1]

    def test_notify_developer_report(self):
        transport = FakeTransport()
        config = _config(targets=(ChatTarget(TEST_CHAT_ID, ("developer_report",)),))
        result = notify_developer_report(
            make_developer_report(), config=config, transport=transport
        )
        assert result.topic == "developer_report"
        assert result.sent == 1
        assert "Total activities" in transport.sent[0][1]

    def test_topic_routing_applies_to_convenience_helpers(self):
        transport = FakeTransport()
        # Only subscribed to developer reports, so alerts are not delivered.
        config = _config(targets=(ChatTarget(TEST_CHAT_ID, ("developer_report",)),))
        result = notify_monitor_alerts(
            [make_monitor_result("alert")], config=config, transport=transport
        )
        assert result.skipped is True

    def test_end_to_end_with_the_real_transport(self):
        """Format → transport → mocked Telegram, with no live network contact."""
        session = FakeSession()
        transport = TelegramTransport(TEST_TOKEN, session=session, sleep=lambda s: None)
        config = _config(
            targets=(ChatTarget(TEST_CHAT_ID, ("developer_report",)),),
            max_length=200,
        )
        result = notify_developer_report(
            make_developer_report(), config=config, transport=transport
        )

        assert result.sent >= 1
        assert session.call_count == result.sent
        assert all(call["method"] == "POST" for call in session.calls)
        assert all(call["url"].endswith("/sendMessage") for call in session.calls)
        assert all(call["kwargs"]["timeout"] == 10.0 for call in session.calls)
