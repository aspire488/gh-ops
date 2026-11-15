"""Conversation memory, isolated per Telegram (chat, user) pair.

Bounded three ways: message count, total characters, and idle expiry. Trims
happen only at user-turn boundaries so a tool result is never orphaned from
the assistant call that requested it (providers reject that).
"""
from __future__ import annotations

import time
from typing import Any

from src.core import state as state_store

STATE_FILE = "agent_conversations.json"


def conversation_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}:{user_id}"


def _size(msg: dict[str, Any]) -> int:
    return len(str(msg.get("content") or "")) + sum(len(c.get("function", {}).get("arguments", ""))
                                                    for c in msg.get("tool_calls") or [])


class ConversationStore:
    def __init__(self, max_messages: int = 40, max_chars: int = 30_000, ttl_hours: float = 12.0,
                 filename: str = STATE_FILE) -> None:
        self.max_messages, self.max_chars, self.ttl_s = max_messages, max_chars, ttl_hours * 3600
        self.filename = filename
        self._data: dict[str, dict[str, Any]] = state_store.load(filename).get("conversations", {})

    def history(self, key: str) -> list[dict[str, Any]]:
        convo = self._data.get(key)
        if not convo:
            return []
        if time.time() - convo.get("updated", 0) > self.ttl_s:
            self._data.pop(key, None)
            return []
        return list(convo.get("messages", []))

    def append(self, key: str, messages: list[dict[str, Any]]) -> None:
        msgs = self.history(key) + messages
        while msgs and (len(msgs) > self.max_messages or sum(map(_size, msgs)) > self.max_chars):
            # drop the oldest whole turn: everything up to the next user message
            nxt = next((i for i, m in enumerate(msgs[1:], 1) if m.get("role") == "user"), len(msgs))
            if nxt >= len(msgs):
                break  # never drop the current turn
            msgs = msgs[nxt:]
        self._data[key] = {"updated": time.time(), "messages": msgs}
        self.save()

    def reset(self, key: str) -> None:
        self._data.pop(key, None)
        self.save()

    def save(self) -> None:
        now = time.time()
        self._data = {k: v for k, v in self._data.items() if now - v.get("updated", 0) <= self.ttl_s}
        state_store.save_atomic(self.filename, {"conversations": self._data})
