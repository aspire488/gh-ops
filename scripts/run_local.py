#!/usr/bin/env python3
"""Local development runner for gh-ops.

Loads the gitignored ``.env`` file and then delegates to the same dispatcher
entry point the GitHub Actions workflows use, so a local run exercises exactly
the code path CI does.

Usage::

    python scripts/run_local.py <job_name>

Job names: daily, monitoring, weekly-report, oss-hunt, security, status

Exit codes match ``python -m src.jobs``: 0 on success, 1 if the job is unknown,
missing, or failed.
"""
import os
import sys

# Add src to path for local development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.dispatcher import get_dispatcher, main  # noqa: E402
from src.jobs.jobs import ALL_JOB_NAMES, register_all_jobs  # noqa: E402
from src.utils.env import load_env_file  # noqa: E402


def run(argv: list[str]) -> int:
    """Load local secrets, then dispatch a job.

    Args:
        argv: Command-line arguments, excluding the program name.

    Returns:
        Process exit code.
    """
    # Load local secrets from the gitignored .env file. Real environment
    # variables always win, so a CI-provided secret is never shadowed.
    # Only names are reported; values are never printed.
    loaded = load_env_file()
    if loaded:
        print(f"Loaded environment variables from .env: {', '.join(loaded)}")

    if not argv:
        print("Usage: python scripts/run_local.py <job_name>")
        print(f"Jobs: {', '.join(ALL_JOB_NAMES)}")
        return 1

    register_all_jobs(get_dispatcher())
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
