"""Minimal ``.env`` file loading (standard library only).

gh-ops keeps secrets in environment variables and never in source or config.
For local development it is convenient to keep them in a gitignored ``.env``
file; this module copies those values into the process environment.

Guarantees:

- An existing environment variable always wins. ``.env`` never overrides a value
  that is already set, so a CI-provided secret cannot be shadowed by a stale
  local file.
- Values are never logged, echoed, printed, or returned. Callers get variable
  *names* back, which is enough to report what was configured.
- A missing file is a no-op, not an error.

This deliberately adds no dependency: a small parser is preferable to pulling a
third-party package into the runtime for local convenience only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

#: Default filename, looked up at the project root.
DEFAULT_ENV_FILENAME = ".env"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``.env`` content into a mapping.

    Handles blank lines, ``#`` comment lines, an optional ``export`` prefix,
    single- or double-quoted values, and `` #`` inline comments on unquoted
    values. Malformed lines are skipped rather than raising, so one bad line
    cannot break a run.

    Args:
        text: Raw file content.

    Returns:
        Mapping of variable name to value.
    """
    result: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        if not _ENV_KEY_RE.match(key):
            continue

        result[key] = _parse_value(value.strip())

    return result


def _parse_value(value: str) -> str:
    """Strip surrounding quotes, or a trailing inline comment, from a value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]

    # Unquoted values may carry a " # comment" suffix. Tokens contain no
    # spaces, so this is safe for credentials.
    marker = value.find(" #")
    if marker != -1:
        value = value[:marker]

    return value.rstrip()


def find_project_root(start: Path | None = None) -> Path:
    """Find the project root by walking up to the directory with pyproject.toml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def load_env_file(
    path: str | Path | None = None,
    *,
    override: bool = False,
) -> list[str]:
    """Load a ``.env`` file into ``os.environ``.

    Args:
        path: Path to the file. Defaults to ``.env`` at the project root.
        override: When False (the default), variables already present in the
            environment are left untouched.

    Returns:
        Sorted names of the variables that were set. Never contains values.
    """
    if path is None:
        path = find_project_root() / DEFAULT_ENV_FILENAME
    path = Path(path)

    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # An unreadable .env must not break a run; the real environment still wins.
        return []

    loaded: list[str] = []
    for key, value in parse_env_file(text).items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)

    return sorted(loaded)


def env_var_status(names: list[str]) -> dict[str, bool]:
    """Report whether each named variable is set, without reading values.

    Args:
        names: Environment variable names to check.

    Returns:
        Mapping of name to whether it is set and non-blank.
    """
    return {name: bool(os.environ.get(name, "").strip()) for name in names}
