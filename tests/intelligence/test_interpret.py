"""Tests for src.intelligence.interpret (Laya gate -> LLM headline)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.intelligence.interpret import FOCUS_LABELS, interpret_brief
from src.intelligence.llm import LlmConfig, Provider

_PROVIDER = Provider(name="groq", base_url="https://groq.test/v1", api_key="k", model="m")
_CFG = LlmConfig(enabled=True, timeout_s=5, providers=(_PROVIDER,))


def _llm_session(content: str) -> MagicMock:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    session.post.return_value = response
    return session


def _laya(choice: str):
    agent = type("Agent", (), {})
    agent.predict = lambda state, spec: {
        "answers": {"decision": {"choice": choice, "confidence": 0.8}}
    }
    return lambda: agent


def _laya_unavailable():
    def boom():
        raise RuntimeError("no checkpoint")

    return boom


class TestLabels:
    def test_allowed_set_is_fixed(self):
        assert FOCUS_LABELS == ("none", "operations", "security", "oss")


class TestInterpretBrief:
    @pytest.fixture(autouse=True)
    def _laya_on(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")

    def test_empty_digest_returns_none(self):
        assert interpret_brief("   ") is None

    def test_laya_none_skips_llm(self):
        session = _llm_session("should never be used")
        result = interpret_brief(
            "brief text", config=_CFG, session=session, laya_loader=_laya("none")
        )
        assert result is None
        session.post.assert_not_called()

    def test_laya_focus_frames_prompt(self):
        session = _llm_session("Security posture hardened today.")
        result = interpret_brief(
            "brief text",
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result == "Security posture hardened today."
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert "security posture" in prompt
        assert "brief text" in prompt

    def test_laya_unavailable_still_interprets(self):
        session = _llm_session("A neutral one-line overview.")
        result = interpret_brief(
            "brief text", config=_CFG, session=session, laya_loader=_laya_unavailable()
        )
        assert result == "A neutral one-line overview."
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert "repository health" in prompt

    def test_llm_failure_returns_none(self):
        session = MagicMock()
        session.post.side_effect = ConnectionError("no net")
        result = interpret_brief(
            "brief text", config=_CFG, session=session, laya_loader=_laya("operations")
        )
        assert result is None

    def test_invalid_llm_output_returns_none(self):
        session = _llm_session('{"internal": "json"}')
        result = interpret_brief(
            "brief text", config=_CFG, session=session, laya_loader=_laya("operations")
        )
        assert result is None

    def test_llm_unconfigured_returns_none(self):
        cfg = LlmConfig(enabled=False, timeout_s=5, providers=(_PROVIDER,))
        session = _llm_session("line")
        result = interpret_brief("brief text", config=cfg, session=session, laya_loader=_laya("operations"))
        assert result is None
        session.post.assert_not_called()

    def test_digest_is_bounded(self):
        session = _llm_session("short line")
        interpret_brief("x " * 10000, config=_CFG, session=session, laya_loader=_laya("operations"))
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert len(prompt) < 5000
