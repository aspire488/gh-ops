"""YAML configuration loading and validation for gh-ops.

Reads config/*.yml files, validates required fields, provides
typed access to configuration values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.errors import ConfigError, ErrorCode


# Default config directory (relative to project root)
_DEFAULT_CONFIG_DIR = "config"


def _project_root() -> Path:
    """Find the project root by looking for pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and parse a YAML file.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML as dictionary.

    Raises:
        ConfigError: If file is missing or malformed.
    """
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path.name}",
            path=str(path),
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(
            f"Invalid YAML in {path.name}: {e}",
            path=str(path),
        ) from e

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path.name} must contain a mapping, got {type(data).__name__}",
            path=str(path),
        )
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Get a nested value from a dictionary.

    Args:
        data: Root dictionary.
        *keys: Keys to traverse.
        default: Default value if path not found.

    Returns:
        The value at the nested path, or default.
    """
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


@dataclass
class Settings:
    """Global settings from config/settings.yml."""

    timezone: str = "UTC"
    log_level: str = "INFO"
    requests_per_hour: int = 5000
    search_per_minute: int = 30
    code_search_per_minute: int = 10
    degraded_threshold: int = 100
    backoff_base_seconds: int = 60
    backoff_max_seconds: int = 600
    state_max_age_days: int = 90
    state_prune_on_save: bool = True
    request_timeout_seconds: int = 30
    workflow_timeout_minutes: int = 25


@dataclass
class RepositoryConfig:
    """Configuration for a monitored repository."""

    owner: str
    repo: str
    monitors: dict[str, bool] = field(default_factory=lambda: {
        "releases": True,
        "issues": False,
        "ci": True,
        "security": True,
    })


@dataclass
class Config:
    """Unified configuration for gh-ops.

    Loaded once at startup, immutable thereafter.
    """

    settings: Settings
    repositories: list[RepositoryConfig]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: str | Path | None = None) -> "Config":
        """Load all configuration files.

        Args:
            config_dir: Path to config directory. If None, uses default.

        Returns:
            Loaded and validated configuration.

        Raises:
            ConfigError: If required config is missing or invalid.
        """
        root = _project_root()
        if config_dir is None:
            config_dir = root / _DEFAULT_CONFIG_DIR
        else:
            config_dir = Path(config_dir)

        if not config_dir.exists():
            raise ConfigError(
                f"Config directory not found: {config_dir}",
                path=str(config_dir),
            )

        # Load settings
        settings_path = config_dir / "settings.yml"
        settings_data = _load_yaml(settings_path) if settings_path.exists() else {}
        settings = cls._parse_settings(settings_data)

        # Load repositories
        repos_path = config_dir / "repositories.yml"
        repos_data = _load_yaml(repos_path) if repos_path.exists() else {}
        repositories = cls._parse_repositories(repos_data)

        # Load raw config for other modules
        raw: dict[str, Any] = {}
        for config_file in config_dir.glob("*.yml"):
            if config_file.name not in ("settings.yml", "repositories.yml"):
                raw[config_file.stem] = _load_yaml(config_file)

        return cls(
            settings=settings,
            repositories=repositories,
            raw=raw,
        )

    @classmethod
    def _parse_settings(cls, data: dict[str, Any]) -> Settings:
        """Parse settings.yml into Settings dataclass."""
        global_data = data.get("global", {})
        rate_data = data.get("rate_limits", {})
        state_data = data.get("state", {})
        timeout_data = data.get("timeouts", {})

        return Settings(
            timezone=global_data.get("timezone", "UTC"),
            log_level=global_data.get("log_level", "INFO"),
            requests_per_hour=rate_data.get("requests_per_hour", 5000),
            search_per_minute=rate_data.get("search_per_minute", 30),
            code_search_per_minute=rate_data.get("code_search_per_minute", 10),
            degraded_threshold=rate_data.get("degraded_threshold", 100),
            backoff_base_seconds=rate_data.get("backoff_base_seconds", 60),
            backoff_max_seconds=rate_data.get("backoff_max_seconds", 600),
            state_max_age_days=state_data.get("max_age_days", 90),
            state_prune_on_save=state_data.get("prune_on_save", True),
            request_timeout_seconds=timeout_data.get("request_seconds", 30),
            workflow_timeout_minutes=timeout_data.get("workflow_minutes", 25),
        )

    @classmethod
    def _parse_repositories(cls, data: dict[str, Any]) -> list[RepositoryConfig]:
        """Parse repositories.yml into list of RepositoryConfig."""
        repos = data.get("repositories", [])
        if not repos:
            return []

        result = []
        for entry in repos:
            if not isinstance(entry, dict):
                continue
            owner = entry.get("owner", "")
            repo = entry.get("repo", "")
            if not owner or not repo:
                continue
            result.append(RepositoryConfig(
                owner=owner,
                repo=repo,
                monitors=entry.get("monitors", {
                    "releases": True,
                    "issues": False,
                    "ci": True,
                    "security": True,
                }),
            ))
        return result

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested value from the raw config.

        Args:
            *keys: Keys to traverse (e.g., "oss_hunter", "scoring", "weights").
            default: Default value if path not found.

        Returns:
            The value at the nested path, or default.
        """
        return _get_nested(self.raw, *keys, default=default)
