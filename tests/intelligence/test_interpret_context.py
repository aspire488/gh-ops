"""Tests for interpret_context (triage -> LLM -> grounding over a context)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.intelligence.context import build_context
from src.intelligence.interpret import interpret_context
from src.intelligence.llm import LlmConfig, Provider
from src.reporting.model import ReportEvent, Severity

_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

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


def _event(
    identity: str,
    *,
    subsystem: str,
    severity: Severity = Severity.ACTION_REQUIRED,
    repository: str = "octo/example",
) -> ReportEvent:
    return ReportEvent(
        subsystem=subsystem,
        event_type="event",
        severity=severity,
        title="CI failed on main",
        repository=repository,
        identity=identity,
        timestamp="2026-09-24T11:00:00+00:00",
        source=subsystem,
    )


def _security_context():
    return build_context(
        [_event("security:octo/example/a:1", subsystem="security")],
        ledger={},
        purpose="daily",
        now=_NOW,
    )


class TestGates:
    @pytest.fixture(autouse=True)
    def _laya_on(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")

    def test_not_meaningful_context_short_circuits(self, monkeypatch):
        context = build_context(
            [_event("ci:octo/example/run/1:failure", subsystem="ci", severity=Severity.INFORMATION)],
            ledger={},
            purpose="daily",
            now=_NOW,
        )
        assert context.meaningful is False
        session = _llm_session("should never be used")
        assert interpret_context(context, config=_CFG, session=session) is None
        session.post.assert_not_called()

    def test_empty_pack_short_circuits(self):
        context = build_context([], ledger={}, purpose="daily", now=_NOW)
        session = _llm_session("should never be used")
        assert interpret_context(context, config=_CFG, session=session) is None
        session.post.assert_not_called()

    def test_laya_quiet_suppresses_a_non_urgent_context(self):
        # Four informational items are meaningful but not security/attention,
        # so a Laya "quiet" vote silences interpretation entirely.
        context = build_context(
            [
                _event(
                    f"ci:octo/example/run/{index}:failure",
                    subsystem="ci",
                    severity=Severity.INFORMATION,
                )
                for index in range(1, 5)
            ],
            ledger={},
            purpose="daily",
            now=_NOW,
        )
        assert context.meaningful is True
        assert context.focus == "operations"
        session = _llm_session("should never be used")
        result = interpret_context(
            context, config=_CFG, session=session, laya_loader=_laya("quiet")
        )
        assert result is None
        session.post.assert_not_called()


class TestHeadline:
    @pytest.fixture(autouse=True)
    def _laya_on(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")

    def test_grounded_sentence_returned(self):
        session = _llm_session("octo/example needs a security review today.")
        result = interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result == "octo/example needs a security review today."

    def test_deterministic_security_focus_beats_laya_label(self):
        session = _llm_session("octo/example needs a security review today.")
        result = interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("oss"),
        )
        assert result is not None
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert "security posture" in prompt

    def test_prompt_carries_purpose_and_digest(self):
        session = _llm_session("octo/example needs attention.")
        interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        prompt = session.post.call_args.kwargs["json"]["messages"][1]["content"]
        assert "daily brief" in prompt
        assert "E1 [security|action_required] octo/example" in prompt

    def test_llm_failure_returns_none(self):
        session = MagicMock()
        session.post.side_effect = ConnectionError("no net")
        result = interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result is None

    def test_invalid_llm_output_returns_none(self):
        session = _llm_session('{"internal": "json"}')
        result = interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result is None

    def test_ungrounded_claim_returns_none(self):
        session = _llm_session("stranger/repo#99 is the real problem.")
        result = interpret_context(
            _security_context(),
            config=_CFG,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result is None

    def test_disabled_llm_returns_none(self):
        cfg = LlmConfig(enabled=False, timeout_s=5, providers=(_PROVIDER,))
        session = _llm_session("octo/example needs attention.")
        result = interpret_context(
            _security_context(),
            config=cfg,
            session=session,
            laya_loader=_laya("security"),
        )
        assert result is None
        session.post.assert_not_called()
