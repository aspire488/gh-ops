"""Lightweight job dispatcher for gh-ops.

Maps job names to Python entry points. GitHub Actions is the scheduler;
this module is just the dispatcher that routes jobs to the right code.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from src.core.errors import ErrorCollector, GhOpsError, ErrorCode, ErrorSeverity
from src.utils.logging import get_logger

logger = get_logger("core.dispatcher")


@dataclass
class JobResult:
    """Result of a dispatched job.

    Attributes:
        job: Job name.
        success: Whether the job completed without errors.
        errors: Any errors that occurred.
        data: Job-specific output data.
    """

    job: str
    success: bool
    errors: ErrorCollector = field(default_factory=ErrorCollector)
    data: dict[str, Any] = field(default_factory=dict)


# Type alias for job functions
JobFunc = Callable[..., JobResult]


class Dispatcher:
    """Maps job names to entry point functions.

    Usage:
        dispatcher = Dispatcher()
        dispatcher.register("daily", daily_job)
        dispatcher.register("oss-hunt", oss_hunt_job)
        result = dispatcher.run("daily")
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobFunc] = {}

    def register(self, name: str, func: JobFunc) -> None:
        """Register a job function.

        Args:
            name: Job name (matches workflow step).
            func: Callable that executes the job.
        """
        if name in self._jobs:
            logger.warning("Overwriting job registration: %s", name)
        self._jobs[name] = func
        logger.debug("Registered job: %s", name)

    def run(self, name: str, **kwargs: Any) -> JobResult:
        """Run a registered job by name.

        Args:
            name: Job name to run.
            **kwargs: Additional arguments passed to the job function.

        Returns:
            JobResult with success status and any errors.

        Raises:
            KeyError: If job name is not registered.
        """
        if name not in self._jobs:
            available = ", ".join(sorted(self._jobs.keys()))
            raise KeyError(f"Unknown job: {name!r}. Available: {available}")

        logger.info("Starting job: %s", name)
        func = self._jobs[name]

        try:
            result = func(**kwargs)
            if result.success:
                logger.info("Job completed: %s", name)
            else:
                logger.warning(
                    "Job completed with errors: %s\n%s",
                    name,
                    result.errors.summary(),
                )
            return result
        except Exception as e:
            logger.error("Job failed: %s: %s", name, e)
            errors = ErrorCollector()
            errors.add(GhOpsError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Job {name} failed: {e}",
                module=f"dispatcher.{name}",
                severity=ErrorSeverity.CRITICAL,
                cause=e,
            ))
            return JobResult(job=name, success=False, errors=errors)

    def list_jobs(self) -> list[str]:
        """List all registered job names."""
        return sorted(self._jobs.keys())


# Default dispatcher instance
_default_dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    """Get the default dispatcher instance."""
    return _default_dispatcher


def register_job(name: str) -> Callable[[JobFunc], JobFunc]:
    """Decorator to register a job function.

    Usage:
        @register_job("daily")
        def daily_job(**kwargs) -> JobResult:
            ...
    """
    def decorator(func: JobFunc) -> JobFunc:
        _default_dispatcher.register(name, func)
        return func
    return decorator


def main() -> None:
    """CLI entry point for running jobs.

    Usage: python -m src.core.dispatcher <job_name>
    """
    if len(sys.argv) < 2:
        jobs = _default_dispatcher.list_jobs()
        print("Usage: python -m src.core.dispatcher <job_name>")
        if jobs:
            print(f"Available jobs: {', '.join(jobs)}")
        else:
            print("No jobs registered.")
        sys.exit(1)

    job_name = sys.argv[1]
    try:
        result = _default_dispatcher.run(job_name)
        if not result.success:
            print(result.errors.summary(), file=sys.stderr)
            sys.exit(1)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
