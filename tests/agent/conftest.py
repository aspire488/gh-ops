from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.intelligence import llm


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every agent test gets its own state dir and clean provider health."""
    monkeypatch.setattr("src.core.state._state_dir", lambda: tmp_path)
    llm._provider_health.clear()
    llm._token_usage.clear()
    return tmp_path


class FakeClient:
    """GET-only GitHub stand-in: routes path -> response (or callable)."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rate_limit_state = SimpleNamespace(remaining=4999)

    def get(self, path: str, params: dict[str, Any] | None = None, headers: Any = None) -> Any:
        self.calls.append((path, dict(params or {})))
        if path not in self.routes:
            err = Exception(f"404 {path}")
            err.status_code = 404  # type: ignore[attr-defined]
            raise err
        value = self.routes[path]
        return value(params or {}) if callable(value) else value


class FakeTransport:
    """Records Bot API calls; scripted getUpdates batches."""

    def __init__(self, updates: list[list[dict[str, Any]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates = list(updates or [])
        self.fail_parse_once = False
        self._mid = 100

    def call(self, method: str, payload: dict[str, Any], *, chat_id: str = "") -> dict[str, Any]:
        self.calls.append((method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": self.updates.pop(0) if self.updates else []}
        if method == "sendMessage" and self.fail_parse_once and payload.get("parse_mode"):
            self.fail_parse_once = False
            from src.notifications.telegram import TelegramError

            raise TelegramError("Bad Request: can't parse entities", status_code=400, operation=method)
        self._mid += 1
        return {"ok": True, "result": {"message_id": self._mid}}

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]


def scripted_chat(*replies: llm.ChatResult):
    """chat_fn returning queued results and recording the messages it saw."""
    queue = list(replies)
    seen: list[list[dict[str, Any]]] = []

    def chat(messages, tools=None, **_):
        seen.append([dict(m) for m in messages])
        return queue.pop(0)

    chat.seen = seen  # type: ignore[attr-defined]
    return chat


def answer(text: str) -> llm.ChatResult:
    return llm.ChatResult("fake", "m", text, ())


def calls(*pairs: tuple[str, str]) -> llm.ChatResult:
    return llm.ChatResult("fake", "m", "", tuple({"id": f"c{i}", "name": n, "arguments": a}
                                                  for i, (n, a) in enumerate(pairs)))
