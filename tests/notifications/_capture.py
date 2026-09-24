"""Transport-boundary capture: real notifier + real transport + FakeSession.

Captures the exact outbound HTTP payload (chat_id, text, parse_mode) without
any network I/O, plus the NotificationResult.topic of the send that produced
each payload. No test in this layer contacts api.telegram.org.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from src.notifications.telegram import (
    ChatTarget,
    NotificationResult,
    TelegramConfig,
    TelegramNotifier,
    TelegramTransport,
)
from tests.notifications._fakes import TEST_TOKEN, FakeSession

DEFAULT_CAPTURE_CHAT_ID = "999000111"


@dataclass(frozen=True)
class CapturedPayload:
    """One outbound sendMessage payload as Telegram would receive it."""

    chat_id: str
    text: str
    parse_mode: str | None
    topic: str


class CaptureBoundary:
    """Full outbound capture at the HTTP payload boundary.

    Uses the real ``TelegramNotifier`` and ``TelegramTransport`` so ``chat_id``,
    ``text``, and ``parse_mode`` come from the actual request body; ``topic``
    is taken from the ``NotificationResult`` of the send that produced it.
    """

    def __init__(
        self,
        *,
        topics: Sequence[str] = (),
        chat_id: str = DEFAULT_CAPTURE_CHAT_ID,
        parse_mode: str = "MarkdownV2",
        max_length: int = 4096,
    ) -> None:
        self.session = FakeSession()
        self.config = TelegramConfig(
            enabled=True,
            targets=(ChatTarget(chat_id, tuple(topics)),),
            max_length=max_length,
            parse_mode=parse_mode,
            retry_attempts=0,
            retry_delay_seconds=0,
            timeout_seconds=1,
        )
        self.transport = TelegramTransport(
            TEST_TOKEN,
            session=self.session,
            sleep=lambda _seconds: None,
            parse_mode=parse_mode,
            max_retries=0,
        )
        self.notifier = TelegramNotifier(self.config, transport=self.transport)
        self._captured: list[CapturedPayload] = []
        self._cursor = 0

    def send(self, messages: Sequence[str], topic: str) -> NotificationResult:
        """Deliver messages through the real notifier/transport and capture HTTP."""
        result = self.notifier.send(messages, topic)
        self._drain(result.topic)
        return result

    def capture_notify(
        self,
        notify_fn: Callable[..., NotificationResult],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[NotificationResult, list[CapturedPayload]]:
        """Invoke a ``notify_*`` helper with this boundary's config and transport."""
        kwargs["config"] = self.config
        kwargs["transport"] = self.transport
        result = notify_fn(*args, **kwargs)
        self._drain(result.topic)
        return result, list(self._captured)

    @property
    def payloads(self) -> list[CapturedPayload]:
        return list(self._captured)

    @property
    def texts(self) -> list[str]:
        return [payload.text for payload in self._captured]

    def clear(self) -> None:
        self._captured.clear()

    def _drain(self, topic: str) -> None:
        for call in self.session.calls[self._cursor :]:
            payload = call["kwargs"].get("json") or {}
            self._captured.append(
                CapturedPayload(
                    chat_id=str(payload.get("chat_id", "")),
                    text=str(payload.get("text", "")),
                    parse_mode=payload.get("parse_mode"),
                    topic=topic,
                )
            )
        self._cursor = len(self.session.calls)
