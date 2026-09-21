"""Tests for the Telegram transport (Phase 7).

All HTTP is mocked via FakeSession — no live Telegram contact.
"""
from __future__ import annotations

import pytest
import requests

from src.core.errors import ErrorCode, ErrorSeverity
from src.notifications.telegram import (
    TELEGRAM_API_BASE,
    TelegramError,
    TelegramTransport,
)
from tests.notifications._fakes import (
    TEST_CHAT_ID,
    TEST_TOKEN,
    FakeResponse,
    FakeSession,
    RecordingSleep,
    assert_no_token,
    error_response,
    ok_response,
)


def _transport(session: FakeSession, sleep: RecordingSleep | None = None, **kwargs) -> TelegramTransport:
    params = {"timeout": 5.0, "max_retries": 3, "retry_delay": 1.0}
    params.update(kwargs)
    return TelegramTransport(
        TEST_TOKEN,
        session=session,
        sleep=sleep or RecordingSleep(),
        **params,
    )


# ── Successful delivery ──────────────────────────────────────────


class TestSuccessfulSend:
    def test_posts_to_fixed_https_telegram_base(self):
        session = FakeSession()
        _transport(session).send_message(TEST_CHAT_ID, "hello")

        assert session.calls[0]["method"] == "POST"
        assert session.last_url.startswith(f"{TELEGRAM_API_BASE}/bot")
        assert session.last_url.endswith("/sendMessage")
        assert session.last_url.startswith("https://")

    def test_returns_parsed_body(self):
        session = FakeSession([ok_response(message_id=99)])
        body = _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert body["ok"] is True
        assert body["result"]["message_id"] == 99

    def test_sends_chat_id_text_and_parse_mode(self):
        session = FakeSession()
        _transport(session).send_message(TEST_CHAT_ID, "hello")
        payload = session.calls[0]["kwargs"]["json"]
        assert payload["chat_id"] == TEST_CHAT_ID
        assert payload["text"] == "hello"
        assert payload["parse_mode"] == "MarkdownV2"

    def test_uses_a_finite_timeout(self):
        session = FakeSession()
        _transport(session, timeout=2.5).send_message(TEST_CHAT_ID, "hello")
        assert session.calls[0]["kwargs"]["timeout"] == 2.5

    def test_parse_mode_override(self):
        session = FakeSession()
        _transport(session).send_message(TEST_CHAT_ID, "hello", parse_mode="HTML")
        assert session.calls[0]["kwargs"]["json"]["parse_mode"] == "HTML"

    def test_empty_parse_mode_omits_the_field(self):
        session = FakeSession()
        _transport(session).send_message(TEST_CHAT_ID, "hello", parse_mode="")
        assert "parse_mode" not in session.calls[0]["kwargs"]["json"]

    def test_empty_text_is_refused_without_http(self):
        session = FakeSession()
        with pytest.raises(TelegramError):
            _transport(session).send_message(TEST_CHAT_ID, "")
        assert session.call_count == 0


class TestTransportConstruction:
    def test_missing_token_is_rejected(self):
        with pytest.raises(TelegramError):
            TelegramTransport("")

    def test_whitespace_token_is_rejected(self):
        with pytest.raises(TelegramError):
            TelegramTransport("   ")

    def test_non_positive_timeout_is_rejected(self):
        with pytest.raises(ValueError):
            TelegramTransport(TEST_TOKEN, timeout=0)

    def test_negative_retries_are_rejected(self):
        with pytest.raises(ValueError):
            TelegramTransport(TEST_TOKEN, max_retries=-1)

    def test_repr_never_exposes_the_token(self):
        transport = TelegramTransport(TEST_TOKEN)
        assert_no_token(repr(transport))
        assert_no_token(str(transport))


# ── Error mapping ────────────────────────────────────────────────


class TestErrorMapping:
    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_http_error_status_is_preserved(self, status: int):
        session = FakeSession([error_response(status)])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert excinfo.value.status_code == status
        assert excinfo.value.code is ErrorCode.TELEGRAM_DELIVERY_FAILED

    def test_error_description_is_used(self):
        session = FakeSession([error_response(400, "Bad Request: chat not found")])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert "chat not found" in excinfo.value.message

    def test_auth_failures_are_high_severity(self):
        for status in (401, 403):
            session = FakeSession([error_response(status)])
            with pytest.raises(TelegramError) as excinfo:
                _transport(session).send_message(TEST_CHAT_ID, "hello")
            assert excinfo.value.severity is ErrorSeverity.HIGH

    def test_non_json_error_body_gets_a_generic_message(self):
        session = FakeSession([FakeResponse(status_code=400, json_error=True)])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert "HTTP 400" in excinfo.value.message

    def test_ok_false_with_http_200_is_an_error(self):
        session = FakeSession([
            FakeResponse(status_code=200, body={"ok": False, "description": "nope"}),
        ])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert "nope" in excinfo.value.message

    def test_empty_body_is_an_error(self):
        session = FakeSession([FakeResponse(status_code=200, json_error=True)])
        with pytest.raises(TelegramError):
            _transport(session).send_message(TEST_CHAT_ID, "hello")

    def test_long_error_descriptions_are_truncated(self):
        session = FakeSession([error_response(400, "x" * 5000)])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert len(excinfo.value.message) <= 300

    def test_client_errors_are_not_retried(self):
        session = FakeSession([error_response(400)])
        with pytest.raises(TelegramError):
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        assert session.call_count == 1

    def test_error_carries_no_url_or_token(self):
        session = FakeSession([error_response(401, "Unauthorized")])
        with pytest.raises(TelegramError) as excinfo:
            _transport(session).send_message(TEST_CHAT_ID, "hello")
        error = excinfo.value
        assert_no_token(error.message)
        assert_no_token(str(error.to_dict()))
        assert_no_token(str(error.context))
        assert "api.telegram.org" not in error.message
        assert "api.telegram.org" not in str(error.to_dict())


# ── Retries ──────────────────────────────────────────────────────


class TestRetries:
    def test_server_error_is_retried_then_succeeds(self):
        session = FakeSession([error_response(500), ok_response()])
        sleep = RecordingSleep()
        body = _transport(session, sleep).send_message(TEST_CHAT_ID, "hello")
        assert body["ok"] is True
        assert session.call_count == 2
        assert len(sleep.delays) == 1

    def test_server_error_exhausts_retries(self):
        session = FakeSession(default=error_response(503))
        sleep = RecordingSleep()
        with pytest.raises(TelegramError) as excinfo:
            _transport(session, sleep, max_retries=3).send_message(TEST_CHAT_ID, "hello")
        assert session.call_count == 4  # initial attempt + 3 retries
        assert len(sleep.delays) == 3
        assert excinfo.value.status_code == 503

    def test_zero_retries_means_a_single_attempt(self):
        session = FakeSession(default=error_response(500))
        sleep = RecordingSleep()
        with pytest.raises(TelegramError):
            _transport(session, sleep, max_retries=0).send_message(TEST_CHAT_ID, "hello")
        assert session.call_count == 1
        assert sleep.delays == []

    def test_timeout_is_retried_then_succeeds(self):
        session = FakeSession([requests.exceptions.Timeout(), ok_response()])
        sleep = RecordingSleep()
        body = _transport(session, sleep).send_message(TEST_CHAT_ID, "hello")
        assert body["ok"] is True
        assert session.call_count == 2
        assert len(sleep.delays) == 1

    def test_timeout_exhausts_retries(self):
        session = FakeSession(default=requests.exceptions.Timeout())
        sleep = RecordingSleep()
        with pytest.raises(TelegramError) as excinfo:
            _transport(session, sleep, max_retries=2).send_message(TEST_CHAT_ID, "hello")
        assert session.call_count == 3
        assert len(sleep.delays) == 2
        assert "timed out" in excinfo.value.message
        assert excinfo.value.status_code == 0

    def test_network_error_is_retried_then_fails(self):
        session = FakeSession(default=requests.exceptions.ConnectionError("boom"))
        sleep = RecordingSleep()
        with pytest.raises(TelegramError) as excinfo:
            _transport(session, sleep, max_retries=1).send_message(TEST_CHAT_ID, "hello")
        assert session.call_count == 2
        assert "ConnectionError" in excinfo.value.message

    def test_backoff_delays_are_positive_and_bounded(self):
        session = FakeSession(default=error_response(500))
        sleep = RecordingSleep()
        with pytest.raises(TelegramError):
            _transport(session, sleep, max_retries=3, retry_delay=1.0).send_message(
                TEST_CHAT_ID, "hello"
            )
        assert all(0 < d <= 30.0 for d in sleep.delays)


# ── Rate limiting ────────────────────────────────────────────────


class TestRateLimiting:
    def test_retry_after_header_is_respected(self):
        session = FakeSession([
            error_response(429, "Too Many Requests", headers={"Retry-After": "7"}),
            ok_response(),
        ])
        sleep = RecordingSleep()
        body = _transport(session, sleep).send_message(TEST_CHAT_ID, "hello")
        assert body["ok"] is True
        assert sleep.delays == [7.0]

    def test_retry_after_from_body_parameters(self):
        session = FakeSession([
            error_response(429, "Too Many Requests", parameters={"retry_after": 4}),
            ok_response(),
        ])
        sleep = RecordingSleep()
        _transport(session, sleep).send_message(TEST_CHAT_ID, "hello")
        assert sleep.delays == [4.0]

    def test_retry_after_header_wins_over_body(self):
        session = FakeSession([
            error_response(
                429,
                "Too Many Requests",
                headers={"Retry-After": "9"},
                parameters={"retry_after": 2},
            ),
            ok_response(),
        ])
        sleep = RecordingSleep()
        _transport(session, sleep).send_message(TEST_CHAT_ID, "hello")
        assert sleep.delays == [9.0]

    def test_retry_after_is_clamped_to_max_delay(self):
        session = FakeSession([
            error_response(429, headers={"Retry-After": "9999"}),
            ok_response(),
        ])
        sleep = RecordingSleep()
        _transport(session, sleep, max_retry_delay=30.0).send_message(TEST_CHAT_ID, "hello")
        assert sleep.delays == [30.0]

    def test_rate_limit_exhausted_uses_dedicated_error_code(self):
        session = FakeSession(default=error_response(429, headers={"Retry-After": "1"}))
        sleep = RecordingSleep()
        with pytest.raises(TelegramError) as excinfo:
            _transport(session, sleep, max_retries=2).send_message(TEST_CHAT_ID, "hello")
        assert excinfo.value.code is ErrorCode.TELEGRAM_RATE_LIMITED
        assert excinfo.value.status_code == 429
        assert excinfo.value.retry_after == 1.0
        assert len(sleep.delays) == 2

    def test_invalid_retry_after_falls_back_to_backoff(self):
        session = FakeSession([
            error_response(429, headers={"Retry-After": "not-a-number"}),
            ok_response(),
        ])
        sleep = RecordingSleep()
        _transport(session, sleep, retry_delay=1.0).send_message(TEST_CHAT_ID, "hello")
        assert len(sleep.delays) == 1
        assert sleep.delays[0] > 0


# ── Token safety ─────────────────────────────────────────────────


class TestTokenSafety:
    def test_token_is_only_used_in_the_request_url(self):
        session = FakeSession()
        _transport(session).send_message(TEST_CHAT_ID, "hello")
        # Functional proof the token is used, so the leak tests are meaningful.
        assert TEST_TOKEN in session.last_url

    def test_no_error_path_leaks_the_token(self):
        cases = [
            [error_response(400)],
            [error_response(401)],
            [error_response(429)],
            [error_response(500)],
            [requests.exceptions.Timeout()],
        ]
        for outcomes in cases:
            session = FakeSession(default=outcomes[0])
            with pytest.raises(TelegramError) as excinfo:
                _transport(session).send_message(TEST_CHAT_ID, "hello")
            assert_no_token(excinfo.value.message)
            assert_no_token(str(excinfo.value.to_dict()))
            assert_no_token(repr(excinfo.value))
            assert_no_token(str(excinfo.value.__cause__))

    def test_context_session_is_closed(self):
        session = FakeSession()
        with _transport(session) as transport:
            transport.send_message(TEST_CHAT_ID, "hello")
        assert session.closed is True
