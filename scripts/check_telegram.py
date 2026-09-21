#!/usr/bin/env python3
"""Report whether Telegram delivery is configured, without exposing secrets.

Prints only booleans, counts, and lengths — never the bot token, never a chat
ID's contents beyond a count, and never the API URL. Safe to paste into a
terminal or a CI log.

Usage:
    python scripts/check_telegram.py
    python scripts/check_telegram.py --env path/to/.env

Exit code is 0 when delivery is ready, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.notifications.telegram import (  # noqa: E402
    TELEGRAM_TOKEN_ENV_VAR,
    load_telegram_config,
)
from src.utils.env import load_env_file  # noqa: E402

_GITHUB_ENV_VARS = ["GITHUB_TOKEN", "GH_TOKEN"]


def _report_flag(ok: bool) -> str:
    return "[x]" if ok else "[ ]"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Telegram configuration (secret-safe).")
    parser.add_argument(
        "--env",
        default=None,
        help="Path to a .env file. Defaults to .env at the project root.",
    )
    args = parser.parse_args()

    loaded = load_env_file(args.env)

    token = os.environ.get(TELEGRAM_TOKEN_ENV_VAR, "").strip()
    config = load_telegram_config()

    print("gh-ops Telegram configuration check")
    print("=" * 38)
    print()
    print("Environment")
    print(f"  .env variables loaded:  {len(loaded)}")
    for name in loaded:
        print(f"    - {name}")
    print(
        f"  {TELEGRAM_TOKEN_ENV_VAR}:"
        f"  {'set (' + str(len(token)) + ' characters)' if token else 'NOT SET'}"
    )
    for name in _GITHUB_ENV_VARS:
        present = bool(os.environ.get(name, "").strip())
        print(f"  {name}:  {'set' if present else 'not set'}")
    print()

    print("Telegram config (config/telegram.yml)")
    print(f"  delivery enabled:  {config.enabled}")
    print(f"  chat targets:      {len(config.targets)}")
    print(f"  max_length:        {config.max_length}")
    print(f"  parse_mode:        {config.parse_mode}")
    print(f"  retry_attempts:    {config.retry_attempts}")
    print(f"  timeout_seconds:   {config.timeout_seconds}")
    print()

    checks = {
        "bot token configured": bool(token),
        "delivery enabled": config.enabled,
        "at least one chat target": bool(config.targets),
    }
    print("Readiness")
    for label, ok in checks.items():
        print(f"  {_report_flag(ok)} {label}")
    print()

    ready = all(checks.values())
    if ready:
        print("Result: READY")
        return 0

    print("Result: NOT READY")
    if not token:
        print(f"  -> set {TELEGRAM_TOKEN_ENV_VAR} in .env")
    if not config.enabled:
        print("  -> set telegram.enabled: true in config/telegram.yml")
    if not config.targets:
        print("  -> add at least one chat_id under telegram.chat_ids in config/telegram.yml")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
