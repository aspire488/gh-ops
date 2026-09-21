"""Allow running the dispatcher as a module: ``python -m src.core <job_name>``.

This is a CLI composition root, not part of the ``src.core`` library import
graph: the job registration import happens only when the module is executed as
a script, so importing ``src.core`` never pulls in job orchestration.

Both of these are equivalent::

    python -m src.core <job_name>
    python -m src.jobs <job_name>
"""
from src.core.dispatcher import get_dispatcher, main

if __name__ == "__main__":
    # Lazy import keeps the dependency direction intact: src.core itself does
    # not depend on src.jobs, only this executable entry point does.
    from src.jobs.jobs import register_all_jobs

    register_all_jobs(get_dispatcher())

    # SystemExit is required: without it the process exits 0 even when main()
    # returns 1, turning an unknown or failed job into a green check.
    raise SystemExit(main())
