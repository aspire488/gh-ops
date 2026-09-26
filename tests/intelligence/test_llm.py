"""Tests for src.intelligence.llm (validation, config, provider failover)."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.intelligence.llm import (
    MAX_OUTPUT_CHARS,
    LlmConfig,
    Provider,
    validate_output,
)

_GROQ = Provider(name="groq", base_url="https://groq.test/v1", api_key="k1", model="m1")
_GEMINI = Provider(name="gemini", base_url="https://gemini.test/v1", api_key="k2", model="m2")
_CFG = LlmConfig(enabled=True, timeout_s=5, providers=(_GROQ, _GEMINI))


def _session(response: object) -> MagicMock:
    session = MagicMock()
    if isinstance(response, Exception):
        session.post.side_effect = response
    else:
        session.post.return_value = response
    return session


def _response(content: object, status: int = 200) -> MagicMock:
    response = MagicMock()
    if status >= 400:
        response.raise_for_status.side_effect = RuntimeError("http error")
        return response
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


class TestValidateOutput:
    def test_accepts_plain_sentence(self):
        assert validate_output("CI is down on two repos.") == "CI is down on two repos."

    def test_collapses_whitespace(self):
        assert validate_output("a\n  b\tc") == "a b c"

    def test_rejects_non_string(self):
        assert validate_output(None) is None
        assert validate_output(5) is None

    def test_rejects_empty(self):
        assert validate_output("   ") is None

    def test_rejects_over_long(self):
        assert validate_output("x" * (MAX_OUTPUT_CHARS + 1)) is None

    def test_rejects_raw_json(self):
        assert validate_output('{"a": 1}') is None
        assert validate_output("[1, 2]") is None

    def test_rejects_control_characters(self):
        assert validate_output("ok\x00nope") is None

    def test_rejects_internal_strings(self):
        assert validate_output("schema_version bumped") is None
        assert validate_output("Monitoring Complete: alerts 0") is None
        assert validate_output("state: persisted") is None


class TestFromEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("GH_OPS_LLM_ENABLED", raising=False)
        monkeypatch.delenv("GH_OPS_LLM_PROVIDERS", raising=False)
        cfg = LlmConfig.from_env()
        assert cfg.enabled is True
        assert [p.name for p in cfg.providers] == ["groq", "gemini"]
        assert cfg.providers[0].base_url == "https://api.groq.com/openai/v1"
        assert cfg.usable is False

    def test_reads_keys_and_models(self, monkeypatch):
        monkeypatch.setenv("GH_OPS_LLM_ENABLED", "true")
        monkeypatch.setenv("GH_OPS_LLM_PROVIDERS", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "secret-key")
        monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
        monkeypatch.delenv("GROQ_BASE_URL", raising=False)
        cfg = LlmConfig.from_env()
        assert cfg.usable is True
        assert cfg.providers[0].api_key == "secret-key"
        assert cfg.providers[0].model == "openai/gpt-oss-120b"

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("GH_OPS_LLM_ENABLED", "false")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        monkeypatch.setenv("GROQ_MODEL", "m")
        cfg = LlmConfig.from_env()
        assert cfg.enabled is False
        assert cfg.usable is False


class TestSummarize:
    def test_returns_none_when_disabled(self):
        from src.intelligence.llm import summarize

        cfg = LlmConfig(enabled=False, timeout_s=5, providers=(_GROQ,))
        assert summarize("prompt", config=cfg, session=_session(_response("ok"))) is None

    def test_returns_none_without_usable_provider(self):
        from src.intelligence.llm import summarize

        dead = Provider(name="groq", base_url="", api_key="", model="")
        cfg = LlmConfig(enabled=True, timeout_s=5, providers=(dead,))
        assert summarize("prompt", config=cfg) is None

    def test_happy_path(self):
        from src.intelligence.llm import summarize

        session = _session(_response("Two repos need attention."))
        result = summarize("prompt", config=_CFG, session=session)
        assert result == "Two repos need attention."
        assert session.post.call_count == 1

    def test_fails_over_to_second_provider(self):
        from src.intelligence.llm import summarize

        session = MagicMock()
        session.post.side_effect = [_response("", status=500), _response("fallback line")]
        result = summarize("prompt", config=_CFG, session=session)
        assert result == "fallback line"
        assert session.post.call_count == 2

    def test_invalid_content_falls_through(self):
        from src.intelligence.llm import summarize

        session = MagicMock()
        session.post.side_effect = [_response('{"json": true}'), _response("good line")]
        result = summarize("prompt", config=_CFG, session=session)
        assert result == "good line"

    def test_network_failure_returns_none(self):
        from src.intelligence.llm import summarize

        cfg = LlmConfig(enabled=True, timeout_s=5, providers=(_GROQ,))
        assert summarize("prompt", config=cfg, session=_session(ConnectionError("no net"))) is None

    def test_never_logs_key(self):
        import io
        import logging

        from src.intelligence.llm import summarize

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("src.intelligence.llm")
        logger.addHandler(handler)
        try:
            summarize("prompt", config=_CFG, session=_session(_response("ok")))
        finally:
            logger.removeHandler(handler)
        assert "k1" not in stream.getvalue()
        assert "k2" not in stream.getvalue()
