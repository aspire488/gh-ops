"""CLI entry point: ``python -m src.jobs <job_name>``.

This is the composition root for the GitHub Actions execution layer. It
registers the six gh-ops jobs and routes to the dispatcher.

The job name must be passed as a **process argument or environment variable**,
never interpolated into a shell command (see ARCHITECTURE.md, "Preventing
Workflow Injection").

Usage::

    python -m src.jobs daily
    GHOPS_JOB=weekly-report python -m src.jobs

Exit codes:
    0  the job executed and reported success
    1  no job given, the job is unknown, or the job failed
"""
from __future__ import annotations

from src.core.dispatcher import get_dispatcher, main
from src.jobs.jobs import register_all_jobs

if __name__ == "__main__":
    # Register before dispatch: without this the dispatcher has no jobs and
    # every invocation would fail with "Unknown job". Registration is explicit
    # rather than an import side effect so job names stay greppable.
    register_all_jobs(get_dispatcher())
    raise SystemExit(main())
