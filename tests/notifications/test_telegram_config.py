"""Tests for Telegram configuration and token resolution (Phase 7)."""
from __future__ import annotations

import pytest

from src.core.config import Config as CoreConfig
from src.core.errors import ConfigError
from src.notifications.telegram import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TELEGRAM_TOKEN_ENV_VAR,
    ChatTarget,
    TelegramConfig,
    TelegramError,
    has_telegram_token,
    load_telegram_config,
    resolve_telegram_token,
)
from tests.notifications._fakes import TEST_TOKEN, assert_no_token

# ── Token resolution ─────────────────────────────────────────────


class TestTokenResolution:
    def test_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, TEST_TOKEN)
        assert resolve_telegram_token() == TEST_TOKEN

    def test_trims_surrounding_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, f"  {TEST_TOKEN}  ")
        assert resolve_telegram_token() == TEST_TOKEN

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        with pytest.raises(TelegramError) as excinfo:
            resolve_telegram_token()
        assert excinfo.value.code.value == "telegram_delivery_failed"

    def test_blank_token_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, "   ")
        with pytest.raises(TelegramError):
            resolve_telegram_token()

    def test_error_names_the_env_var_but_contains_no_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        with pytest.raises(TelegramError) as excinfo:
            resolve_telegram_token()
        assert TELEGRAM_TOKEN_ENV_VAR in excinfo.value.message
        assert_no_token(str(excinfo.value.to_dict()))

    def test_has_telegram_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        assert has_telegram_token() is False
        monkeypatch.setenv(TELEGRAM_TOKEN_ENV_VAR, TEST_TOKEN)
        assert has_telegram_token() is True

    def test_other_env_vars_are_not_consulted(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(TELEGRAM_TOKEN_ENV_VAR, raising=False)
        monkeypatch.setenv("TELEGRAM_TOKEN", TEST_TOKEN)
        monkeypatch.setenv("BOT_TOKEN", TEST_TOKEN)
        with pytest.raises(TelegramError):
            resolve_telegram_token()


# ── Config parsing ───────────────────────────────────────────────


class TestConfigDefaults:
    def test_none_returns_defaults(self):
        config = TelegramConfig.from_dict(None)
        assert config.enabled is False
        assert config.targets == ()
        assert config.max_length == TELEGRAM_MAX_MESSAGE_LENGTH

    def test_disabled_by_default(self):
        assert TelegramConfig().enabled is False

    def test_run_summary_disabled_by_default(self):
        assert TelegramConfig.from_dict({"enabled": True}).run_summary is False

    def test_oss_summary_disabled_by_default(self):
        assert TelegramConfig.from_dict({"enabled": True}).oss_summary is False

    def test_run_and_oss_summary_flags_parse(self):
        config = TelegramConfig.from_dict({
            "telegram": {"enabled": True, "run_summary": True, "oss_summary": True},
        })
        assert config.run_summary is True
        assert config.oss_summary is True

    def test_non_mapping_raises(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict(["not", "a", "mapping"])

    def test_unwraps_nested_telegram_section(self):
        config = TelegramConfig.from_dict({"telegram": {"enabled": True}})
        assert config.enabled is True

    def test_accepts_already_unwrapped_section(self):
        config = TelegramConfig.from_dict({"enabled": True})
        assert config.enabled is True

    def test_message_section_must_be_mapping(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"message": "nope"}})


class TestConfigChatIds:
    def test_bare_string_ids(self):
        config = TelegramConfig.from_dict({"telegram": {"chat_ids": ["1", "2"]}})
        assert config.targets == (ChatTarget("1"), ChatTarget("2"))

    def test_bare_int_ids_are_stringified(self):
        config = TelegramConfig.from_dict({"telegram": {"chat_ids": [123]}})
        assert config.targets == (ChatTarget("123"),)

    def test_mapping_ids_with_topics(self):
        config = TelegramConfig.from_dict({
            "telegram": {"chat_ids": [{"chat_id": 1, "topics": ["alerts"]}]},
        })
        assert config.targets == (ChatTarget("1", ("alerts",)),)

    def test_mapping_id_without_topics(self):
        config = TelegramConfig.from_dict({"telegram": {"chat_ids": [{"chat_id": 7}]}})
        assert config.targets == (ChatTarget("7", ()),)

    def test_empty_chat_id_raises(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"chat_ids": [{"chat_id": "  "}]}})

    def test_unknown_entry_type_raises(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"chat_ids": [["nested"]]}})

    def test_topics_must_be_a_list(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({
                "telegram": {"chat_ids": [{"chat_id": 1, "topics": "alerts"}]},
            })

    def test_chat_ids_must_be_a_list(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"chat_ids": "1,2"}})


class TestConfigValidation:
    def test_token_ref_must_be_telegram_bot_token(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({
                "telegram": {"bot_token_ref": "SOME_OTHER_VAR"},
            })

    def test_default_token_ref_accepted(self):
        config = TelegramConfig.from_dict({
            "telegram": {"bot_token_ref": TELEGRAM_TOKEN_ENV_VAR},
        })
        assert config.enabled is False

    def test_max_length_clamped_to_telegram_limit(self):
        config = TelegramConfig.from_dict({
            "telegram": {"message": {"max_length": 99999}},
        })
        assert config.max_length == TELEGRAM_MAX_MESSAGE_LENGTH

    def test_max_length_must_be_positive(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"message": {"max_length": 0}}})

    def test_negative_retry_attempts_raises(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"message": {"retry_attempts": -1}}})

    def test_negative_retry_delay_raises(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"message": {"retry_delay_seconds": -1}}})

    def test_timeout_must_be_positive(self):
        with pytest.raises(ConfigError):
            TelegramConfig.from_dict({"telegram": {"message": {"timeout_seconds": 0}}})

    def test_message_options_are_parsed(self):
        config = TelegramConfig.from_dict({
            "telegram": {
                "message": {
                    "max_length": 1000,
                    "parse_mode": "HTML",
                    "split_separator": "\n",
                    "retry_attempts": 1,
                    "retry_delay_seconds": 2,
                    "timeout_seconds": 3,
                },
            },
        })
        assert config.max_length == 1000
        assert config.parse_mode == "HTML"
        assert config.split_separator == "\n"
        assert config.retry_attempts == 1
        assert config.retry_delay_seconds == 2.0
        assert config.timeout_seconds == 3.0


class TestChatTargetRouting:
    def test_empty_topics_accepts_every_topic(self):
        assert ChatTarget("1").accepts("anything") is True

    def test_matching_topic(self):
        assert ChatTarget("1", ("alerts",)).accepts("alerts") is True

    def test_non_matching_topic(self):
        assert ChatTarget("1", ("alerts",)).accepts("oss_opportunities") is False

    def test_targets_for_filters_by_topic(self):
        config = TelegramConfig(
            enabled=True,
            targets=(ChatTarget("1", ("alerts",)), ChatTarget("2", ("developer_report",))),
        )
        assert config.targets_for("alerts") == (ChatTarget("1", ("alerts",)),)

    def test_targets_for_preserves_configured_order(self):
        config = TelegramConfig(
            enabled=True,
            targets=(ChatTarget("b"), ChatTarget("a")),
        )
        assert [t.chat_id for t in config.targets_for("alerts")] == ["b", "a"]

    def test_to_dict_includes_topics(self):
        assert ChatTarget("1", ("alerts",)).to_dict() == {"chat_id": "1", "topics": ["alerts"]}


class TestConfigSerialization:
    def test_to_dict_contains_no_token_field(self):
        payload = TelegramConfig().to_dict()
        assert "token" not in payload
        assert "bot_token" not in payload
        assert "bot_token_ref" not in payload

    def test_to_dict_is_deterministic(self):
        config = TelegramConfig(enabled=True, targets=(ChatTarget("1", ("alerts",)),))
        assert config.to_dict() == config.to_dict()


# ── Config loading ───────────────────────────────────────────────


class TestLoadTelegramConfig:
    def test_accepts_telegram_config_instance(self):
        config = TelegramConfig(enabled=True)
        assert load_telegram_config(config) is config

    def test_accepts_raw_dict(self):
        assert load_telegram_config({"telegram": {"enabled": True}}).enabled is True

    def test_accepts_core_config_instance(self):
        loaded = load_telegram_config(CoreConfig.load())
        assert isinstance(loaded, TelegramConfig)

    def test_loads_from_disk_by_default(self):
        config = load_telegram_config()
        # config/telegram.yml is deployment configuration: it may be enabled or
        # disabled and may list chats. Assert only invariants that must always
        # hold, so changing the shipped config cannot break this test.
        assert isinstance(config, TelegramConfig)
        assert 0 < config.max_length <= TELEGRAM_MAX_MESSAGE_LENGTH
        assert config.timeout_seconds > 0
        assert config.retry_attempts >= 0
        assert all(target.chat_id for target in config.targets)

    def test_shipped_config_parses_to_a_valid_config(self):
        config = load_telegram_config()
        # Whatever the deployment says, the token is never part of it.
        assert "token" not in str(config.to_dict()).lower().replace("bot_token_ref", "")

    def test_missing_section_uses_defaults(self):
        config = load_telegram_config({"telegram": {}})
        assert config.enabled is False
