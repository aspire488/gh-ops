#!/usr/bin/env python3
"""Local development runner for gh-ops."""
import os
import sys

# Add src to path for local development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.env import load_env_file  # noqa: E402


def main() -> None:
    """Run a gh-ops operation locally."""
    # Load local secrets from the gitignored .env file. Real environment
    # variables always win, so a CI-provided secret is never shadowed.
    # Only names are reported; values are never printed.
    loaded = load_env_file()
    if loaded:
        print(f"Loaded environment variables from .env: {', '.join(loaded)}")

    if len(sys.argv) < 2:
        print("Usage: python run_local.py <operation> [args...]")
        print("Operations: daily, oss-hunt, monitor, weekly")
        sys.exit(1)

    operation = sys.argv[1]
    print(f"Running operation: {operation}")
    # TODO: Import and call dispatcher
    print("Not yet implemented. Build Phase 1 first.")


if __name__ == "__main__":
    main()
