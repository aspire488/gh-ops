"""Tests for src.utils.env (.env loading)."""
from __future__ import annotations

import logging

import pytest

from src.utils.env import (
    env_var_status,
    find_project_root,
    load_env_file,
    parse_env_file,
)

FAKE_SECRET = "8900000000:AAFakeSecretValue_ForTestsOnly_1234567890"


# ── Parsing ──────────────────────────────────────────────────────


class TestParseEnvFile:
    def test_basic_pair(self):
        assert parse_env_file("KEY=value") == {"KEY": "value"}

    def test_multiple_pairs(self):
        assert parse_env_file("A=1\nB=2") == {"A": "1", "B": "2"}

    def test_blank_lines_and_comments_are_skipped(self):
        text = "\n# a comment\n   \nKEY=value\n# trailing comment\n"
        assert parse_env_file(text) == {"KEY": "value"}

    def test_export_prefix(self):
        assert parse_env_file("export KEY=value") == {"KEY": "value"}

    def test_double_quoted_value_keeps_spaces(self):
        assert parse_env_file('KEY="a value with spaces"') == {"KEY": "a value with spaces"}

    def test_single_quoted_value(self):
        assert parse_env_file("KEY='  padded  '") == {"KEY": "  padded  "}

    def test_inline_comment_on_unquoted_value(self):
        assert parse_env_file("KEY=value  # why") == {"KEY": "value"}

    def test_inline_comment_kept_inside_quotes(self):
        assert parse_env_file('KEY="value # not a comment"') == {"KEY": "value # not a comment"}

    def test_hash_without_leading_space_is_part_of_the_value(self):
        assert parse_env_file("KEY=va#lue") == {"KEY": "va#lue"}

    def test_value_may_contain_equals_signs(self):
        assert parse_env_file("KEY=a=b=c") == {"KEY": "a=b=c"}

    def test_empty_value(self):
        assert parse_env_file("KEY=") == {"KEY": ""}

    def test_surrounding_whitespace_is_trimmed(self):
        assert parse_env_file("  KEY = value  ") == {"KEY": "value"}

    def test_line_without_equals_is_skipped(self):
        assert parse_env_file("nonsense") == {}

    def test_invalid_key_is_skipped(self):
        assert parse_env_file("1BAD=value\n-ALSO-BAD=value") == {}

    def test_valid_key_after_invalid_is_still_parsed(self):
        assert parse_env_file("1BAD=x\nGOOD=y") == {"GOOD": "y"}

    def test_windows_line_endings(self):
        assert parse_env_file("A=1\r\nB=2\r\n") == {"A": "1", "B": "2"}

    def test_token_shaped_value_is_parsed_verbatim(self):
        assert parse_env_file(f"TELEGRAM_BOT_TOKEN={FAKE_SECRET}") == {
            "TELEGRAM_BOT_TOKEN": FAKE_SECRET
        }


# ── Loading ──────────────────────────────────────────────────────


class TestLoadEnvFile:
    def test_sets_variables_and_returns_names(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GHOPS_TEST_A", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("GHOPS_TEST_A=hello\n", encoding="utf-8")

        assert load_env_file(env_file) == ["GHOPS_TEST_A"]
        assert __import__("os").environ["GHOPS_TEST_A"] == "hello"

    def test_missing_file_is_a_noop(self, tmp_path):
        assert load_env_file(tmp_path / "absent.env") == []

    def test_directory_path_is_a_noop(self, tmp_path):
        assert load_env_file(tmp_path) == []

    def test_does_not_override_existing_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHOPS_TEST_B", "from-process")
        env_file = tmp_path / ".env"
        env_file.write_text("GHOPS_TEST_B=from-file\n", encoding="utf-8")

        assert load_env_file(env_file) == []
        assert __import__("os").environ["GHOPS_TEST_B"] == "from-process"

    def test_override_flag_replaces_existing_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHOPS_TEST_C", "from-process")
        env_file = tmp_path / ".env"
        env_file.write_text("GHOPS_TEST_C=from-file\n", encoding="utf-8")

        assert load_env_file(env_file, override=True) == ["GHOPS_TEST_C"]
        assert __import__("os").environ["GHOPS_TEST_C"] == "from-file"

    def test_names_are_sorted_and_value_free(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GHOPS_TEST_D", raising=False)
        monkeypatch.delenv("GHOPS_TEST_C2", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("GHOPS_TEST_D=x\nGHOPS_TEST_C2=y\n", encoding="utf-8")

        loaded = load_env_file(env_file)
        assert loaded == ["GHOPS_TEST_C2", "GHOPS_TEST_D"]

    def test_project_root_is_discovered(self):
        root = find_project_root()
        assert (root / "pyproject.toml").is_file()

    def test_default_path_targets_the_project_root(self, monkeypatch, tmp_path):
        # A missing default .env must not raise.
        monkeypatch.chdir(tmp_path)
        assert load_env_file() == []

    def test_real_project_env_file_is_gitignored(self):
        """The repository must never track .env."""
        import subprocess

        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            cwd=find_project_root(),
            capture_output=True,
        )
        # Skips cleanly if git is unavailable or this is not a checkout.
        if result.returncode not in (0, 1):
            pytest.skip("git not available")
        assert result.returncode == 0, ".env is NOT gitignored"


# ── Secret safety ────────────────────────────────────────────────


class TestSecretSafety:
    def test_values_never_appear_in_logs(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(f"TELEGRAM_BOT_TOKEN={FAKE_SECRET}\n", encoding="utf-8")

        with caplog.at_level(logging.DEBUG):
            load_env_file(env_file)

        assert FAKE_SECRET not in caplog.text

    def test_loaded_names_contain_no_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(f"TELEGRAM_BOT_TOKEN={FAKE_SECRET}\n", encoding="utf-8")

        loaded = load_env_file(env_file)
        assert FAKE_SECRET not in str(loaded)


# ── Status reporting ─────────────────────────────────────────────


class TestEnvVarStatus:
    def test_reports_set_and_unset(self, monkeypatch):
        monkeypatch.setenv("GHOPS_TEST_SET", "value")
        monkeypatch.delenv("GHOPS_TEST_UNSET", raising=False)
        assert env_var_status(["GHOPS_TEST_SET", "GHOPS_TEST_UNSET"]) == {
            "GHOPS_TEST_SET": True,
            "GHOPS_TEST_UNSET": False,
        }

    def test_blank_value_counts_as_unset(self, monkeypatch):
        monkeypatch.setenv("GHOPS_TEST_BLANK", "   ")
        assert env_var_status(["GHOPS_TEST_BLANK"]) == {"GHOPS_TEST_BLANK": False}

    def test_status_is_boolean_only(self, monkeypatch):
        monkeypatch.setenv("GHOPS_TEST_SECRET", FAKE_SECRET)
        status = env_var_status(["GHOPS_TEST_SECRET"])
        assert status == {"GHOPS_TEST_SECRET": True}
        assert FAKE_SECRET not in str(status)
