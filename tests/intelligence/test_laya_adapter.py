"""Tests for src.intelligence.laya_adapter (gate, validation, fail-soft)."""
from __future__ import annotations

import pytest

from src.intelligence import laya_adapter


def _fake_loader(choice: str, confidence: float = 0.7):
    agent = type("Agent", (), {})
    agent.predict = lambda state, spec: {
        "answers": {"decision": {"choice": choice, "confidence": confidence}}
    }
    return lambda: agent


class TestEnabledFlag:
    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("LAYA_ENABLED", raising=False)
        assert laya_adapter.laya_enabled() is True

    def test_explicit_disable(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "false")
        assert laya_adapter.laya_enabled() is False

    def test_explicit_enable(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        assert laya_adapter.laya_enabled() is True


class TestDecide:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "false")
        loader = _fake_loader("operations")
        assert (
            laya_adapter.decide("instructions", "state", ["operations", "none"], loader=loader)
            is None
        )

    def test_empty_labels_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        assert laya_adapter.decide("instructions", "state", [], loader=_fake_loader("x")) is None

    def test_empty_instructions_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        assert (
            laya_adapter.decide("  ", "state", ["operations"], loader=_fake_loader("operations"))
            is None
        )

    def test_returns_label_and_score(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        result = laya_adapter.decide(
            "instructions", "state", ["none", "security"], loader=_fake_loader("security", 0.9)
        )
        assert result == ("security", 0.9)

    def test_label_outside_allowed_set_rejected(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        assert (
            laya_adapter.decide(
                "instructions", "state", ["none", "security"], loader=_fake_loader("hacked")
            )
            is None
        )

    def test_loader_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")

        def boom():
            raise RuntimeError("model unavailable")

        assert laya_adapter.decide("instructions", "state", ["none"], loader=boom) is None

    def test_malformed_result_returns_none(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")

        class Broken:
            def predict(self, state, spec):
                return {"answers": {}}

        assert (
            laya_adapter.decide("instructions", "state", ["none"], loader=lambda: Broken()) is None
        )

    def test_default_loader_never_runs_when_disabled(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "false")

        def must_not_load():
            raise AssertionError("checkpoint must not load when disabled")

        assert (
            laya_adapter.decide("instructions", "state", ["none"], loader=must_not_load) is None
        )


class TestCache:
    @pytest.fixture(autouse=True)
    def _clean(self):
        laya_adapter.reset_cache()
        yield
        laya_adapter.reset_cache()

    def test_reset_cache_clears_agent(self, monkeypatch):
        monkeypatch.setenv("LAYA_ENABLED", "true")
        laya_adapter.reset_cache()
        assert laya_adapter._AGENT is None
