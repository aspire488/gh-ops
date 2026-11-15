"""Provider chain: failover, retries, validation, cooldown — no network."""
import pytest
import requests

from src.intelligence import llm


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Session:
    def __init__(self, script):
        self.script, self.urls = list(script), []

    def post(self, url, json, headers, timeout):
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(content="hi", calls=None):
    msg = {"content": content}
    if calls:
        msg["tool_calls"] = calls
    return Resp(200, {"choices": [{"message": msg}], "usage": {"total_tokens": 42}})


CFG = llm.LlmConfig(enabled=True, timeout_s=5, providers=(
    llm.Provider("groq", "https://g", "k1", "m1"), llm.Provider("gemini", "https://m", "k2", "m2")))


def run(script, **kw):
    s = Session(script)
    return llm.chat([{"role": "user", "content": "x"}], config=CFG, session=s, sleep=lambda _: None, **kw), s


def test_primary_answers():
    result, s = run([ok("hello")])
    assert result.provider == "groq" and result.content == "hello" and len(s.urls) == 1
    assert llm.provider_status()["groq"]["tokens_used"] == 42


def test_tool_calls_parsed_and_dict_arguments_normalised():
    result, _ = run([ok("", [{"id": "a", "function": {"name": "get_repo", "arguments": {"repo": "a/b"}}}])])
    assert result.tool_calls == ({"id": "a", "name": "get_repo", "arguments": '{"repo": "a/b"}'},)


def test_auth_failure_fails_over_and_cools_down():
    result, _ = run([Resp(401, {}), ok("from gemini")])
    assert result.provider == "gemini"
    assert llm.provider_status()["groq"]["healthy"] is False
    # next call skips the cooling provider entirely
    result2, s2 = run([ok("again")])
    assert result2.provider == "gemini" and s2.urls == ["https://m/chat/completions"]


def test_429_waits_then_succeeds_on_same_provider():
    waits = []
    s = Session([Resp(429, {}, {"retry-after": "3"}), ok("after wait")])
    result = llm.chat([{"role": "user", "content": "x"}], config=CFG, session=s, sleep=waits.append)
    assert result.provider == "groq" and waits and waits[0] >= 3


def test_network_errors_retry_then_fail_over():
    result, _ = run([requests.exceptions.ConnectionError("boom https://g?key=secret"),
                     requests.exceptions.ConnectionError("x"), requests.exceptions.Timeout("y"), ok("gem")], retries=2)
    assert result.provider == "gemini"


@pytest.mark.parametrize("bad", [Resp(200, None), Resp(200, {"choices": []}), Resp(200, {"choices": [{"message": {"content": ""}}]}),
                                 Resp(200, {"choices": [{"message": {"content": 5}}]})])
def test_malformed_responses_are_rejected(bad):
    result, _ = run([bad, ok("valid")])
    assert result.provider == "gemini"


def test_all_failing_raises_with_safe_reasons():
    with pytest.raises(llm.LlmUnavailableError) as err:
        run([Resp(400, {"error": {"code": "bad"}}), Resp(403, {})])
    text = str(err.value)
    assert "groq" in text and "gemini" in text and "k1" not in text and "k2" not in text


def test_exception_text_with_url_is_not_leaked():
    with pytest.raises(llm.LlmUnavailableError) as err:
        run([requests.exceptions.ConnectionError("https://g?api_key=SECRET")] * 3 + [Resp(401, {})], retries=2)
    assert "SECRET" not in str(err.value)


def test_unconfigured_raises():
    with pytest.raises(llm.LlmUnavailableError):
        llm.chat([], config=llm.LlmConfig(enabled=True, timeout_s=1, providers=()))


def test_multi_model_env_expands_to_failover_steps(monkeypatch):
    monkeypatch.setenv("GH_OPS_LLM_PROVIDERS", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "openai/a, openai/b")
    names = [(p.name, p.model) for p in llm.LlmConfig.from_env().providers]
    assert names == [("groq", "openai/a"), ("groq:b", "openai/b")]


def test_total_time_budget_skips_long_waits_and_fails_over():
    waits = []
    s = Session([Resp(429, {}, {"retry-after": "50"}), ok("gem")])
    result = llm.chat([{"role": "user", "content": "x"}], config=CFG, session=s, sleep=waits.append, budget_s=10)
    assert result.provider == "gemini" and waits == []
