"""Shared test doubles for the Telegram adapter tests.

ALL Telegram HTTP is mocked here. No test in this directory contacts
api.telegram.org, and no live token is ever required.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# A syntactically realistic bot token. Never leaves the test process.
TEST_TOKEN = "123456789:AAFakeTokenValueForTestsOnly_abcdefghij"
TEST_CHAT_ID = "424242"


class FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("Response body is not JSON")
        return self._body


def ok_response(message_id: int = 1) -> FakeResponse:
    """A successful Telegram response."""
    return FakeResponse(status_code=200, body={"ok": True, "result": {"message_id": message_id}})


def error_response(
    status_code: int,
    description: str = "Bad Request",
    headers: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> FakeResponse:
    """A Telegram error response."""
    body: dict[str, Any] = {"ok": False, "error_code": status_code, "description": description}
    if parameters is not None:
        body["parameters"] = parameters
    return FakeResponse(status_code=status_code, body=body, headers=headers)


class FakeSession:
    """Minimal requests.Session stand-in that records calls.

    Args:
        outcomes: Consumed one per call. Each item is either a FakeResponse or an
            exception instance to raise.
        default: Returned (or raised, if an exception) once outcomes run out.
            Defaults to a successful response.
    """

    def __init__(self, outcomes: list[Any] | None = None, default: Any = None) -> None:
        self.outcomes = list(outcomes or [])
        self.default = default
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_url(self) -> str:
        return self.calls[-1]["url"] if self.calls else ""

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})

        if self.outcomes:
            outcome = self.outcomes.pop(0)
        else:
            outcome = self.default if self.default is not None else ok_response()

        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self) -> None:
        self.closed = True


class RecordingSleep:
    """Sleep stand-in that records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.delays)


class FakeTransport:
    """Transport stand-in for notifier tests.

    Args:
        fail_on: Chat IDs that should raise.
        error: Exception to raise for failing chats.
    """

    def __init__(
        self,
        fail_on: set[str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        from src.notifications.telegram import TelegramError

        self.fail_on = set(fail_on or set())
        self.error = error or TelegramError("delivery failed", operation="sendMessage")
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        if chat_id in self.fail_on:
            raise self.error
        self.sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def close(self) -> None:  # pragma: no cover - parity with the real transport
        pass


@contextmanager
def captured_logs(logger_name: str = "notifications.telegram") -> Iterator[list[str]]:
    """Capture formatted log messages from a logger.

    Yields a list of message strings, which makes it easy to assert that a
    secret never reaches the logs.
    """
    messages: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger(logger_name)
    handler = _Handler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def assert_no_token(text: str, token: str = TEST_TOKEN) -> None:
    """Assert that neither the full token nor its secret half leaked."""
    assert token not in text, f"token leaked into: {text!r}"
    secret = token.split(":", 1)[1]
    assert secret not in text, f"token secret leaked into: {text!r}"


def make_opportunity(**overrides: Any) -> Any:
    """Build a real Opportunity with deterministic defaults."""
    from src.intelligence.oss.models import Opportunity

    fields: dict[str, Any] = {
        "repo_full_name": "octo/example",
        "issue_number": 42,
        "issue_id": 10042,
        "title": "Fix the thing",
        "html_url": "https://github.com/octo/example/issues/42",
        "state": "open",
        "user": "someone",
        "labels": ("good first issue",),
        "score": 12.345,
        "repo_stars": 1500,
        "repo_language": "Python",
    }
    fields.update(overrides)
    return Opportunity(**fields)


def make_monitor_result(
    status: Any,
    monitor: str = "ci",
    resource: str = "octo/example",
    summary: str = "workflow failed",
    category: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Build a real MonitorResult."""
    from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus

    return MonitorResult(
        monitor=monitor,
        resource=resource,
        status=status if isinstance(status, MonitorStatus) else MonitorStatus(status),
        summary=summary,
        category=category if category is not None else MonitorCategory.CI,
        metadata=dict(metadata or {}),
        evaluated_at="2025-01-15T00:00:00+00:00",
    )


def make_developer_report(**overrides: Any) -> Any:
    """Build a deterministic DeveloperReport."""
    from datetime import datetime, timezone

    from src.developer import generate_report

    params: dict[str, Any] = {
        "username": "someone",
        "period_start": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "period_end": datetime(2025, 1, 8, tzinfo=timezone.utc),
    }
    params.update(overrides)
    return generate_report([], [], **params)
