"""Tests for src.core.config"""
import os
import pytest
import tempfile
from pathlib import Path

from src.core.config import Config, Settings, RepositoryConfig, _load_yaml, _deep_merge
from src.core.errors import ConfigError


class TestLoadYaml:
    def test_load_valid_yaml(self, tmp_path):
        config_file = tmp_path / "test.yml"
        config_file.write_text("key: value\nnested:\n  a: 1\n")
        data = _load_yaml(config_file)
        assert data["key"] == "value"
        assert data["nested"]["a"] == 1

    def test_missing_file_raises(self, tmp_path):
        config_file = tmp_path / "missing.yml"
        with pytest.raises(ConfigError, match="not found"):
            _load_yaml(config_file)

    def test_invalid_yaml_raises(self, tmp_path):
        config_file = tmp_path / "bad.yml"
        config_file.write_text(":\n  invalid:\n    yaml: [")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            _load_yaml(config_file)

    def test_empty_file_returns_dict(self, tmp_path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text("")
        data = _load_yaml(config_file)
        assert data == {}


class TestDeepMerge:
    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_override_non_dict(self):
        base = {"a": "string"}
        override = {"a": 42}
        result = _deep_merge(base, override)
        assert result["a"] == 42


class TestConfig:
    def test_load_default_config(self):
        """Test loading config from the project's config/ directory."""
        config = Config.load()
        assert isinstance(config.settings, Settings)
        assert config.settings.timezone == "UTC"
        assert config.settings.requests_per_hour == 5000

    def test_settings_defaults(self):
        settings = Settings()
        assert settings.timezone == "UTC"
        assert settings.log_level == "INFO"
        assert settings.requests_per_hour == 5000
        assert settings.search_per_minute == 30

    def test_missing_config_dir(self):
        with pytest.raises(ConfigError, match="not found"):
            Config.load(config_dir="/nonexistent/path")

    def test_get_nested(self):
        config = Config(
            settings=Settings(),
            repositories=[],
            raw={"oss_hunter": {"scoring": {"weights": {"repo_activity": 0.25}}}},
        )
        assert config.get("oss_hunter", "scoring", "weights", "repo_activity") == 0.25
        assert config.get("oss_hunter", "missing", default="fallback") == "fallback"

    def test_empty_repositories(self):
        config = Config(
            settings=Settings(),
            repositories=[],
        )
        assert len(config.repositories) == 0


class TestRepositoryConfig:
    def test_defaults(self):
        repo = RepositoryConfig(owner="test", repo="repo")
        assert repo.owner == "test"
        assert repo.repo == "repo"
        assert repo.monitors["releases"] is True
        assert repo.monitors["ci"] is True
