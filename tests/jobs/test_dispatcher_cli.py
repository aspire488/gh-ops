"""Tests for the dispatcher CLI entry points.

These lock down the bug Phase 8 fixed: ``python -m src.core.dispatcher <job>``
used to import the module and exit 0 without executing anything, producing a
green CI check that collected nothing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.core.dispatcher import JOB_ENV_VAR, Dispatcher, get_dispatcher, main
from src.core import dispatcher as dispatcher_module
from src.jobs.jobs import ALL_JOB_NAMES, register_all_jobs

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def clean_dispatcher(monkeypatch: pytest.MonkeyPatch):
    """Give each test an isolated dispatcher as the module default."""
    fresh = Dispatcher()
    monkeypatch.setattr(dispatcher_module, "_default_dispatcher", fresh)
    return fresh


@pytest.fixture()
def registered(clean_dispatcher, monkeypatch: pytest.MonkeyPatch):
    """A dispatcher with the six jobs registered and no I/O."""
    from src.jobs import jobs as jobs_module
    from src.core.models import CurrentState

    monkeypatch.setattr(jobs_module, "load_runtime_config", lambda config_dir=None: _FakeConfig())
    monkeypatch.setattr(jobs_module, "load_previous_state", CurrentState)
    register_all_jobs(clean_dispatcher)
    return clean_dispatcher


class _FakeConfig:
    def get(self, *keys, default=None):
        return default

    repositories: list = []


class TestMainExitCodes:
    def test_no_job_returns_nonzero(self, registered):
        assert main([]) == 1

    def test_unknown_job_returns_nonzero(self, registered, capsys):
        assert main(["definitely-not-a-job"]) == 1
        assert "Unknown job" in capsys.readouterr().err

    def test_known_job_returns_zero(self, registered):
        assert main(["status"]) == 0

    def test_failed_job_returns_nonzero(self, clean_dispatcher, monkeypatch, capsys):
        from src.core.dispatcher import JobResult
        from src.core.errors import ErrorCode, GhOpsError, ErrorCollector
        from src.core.errors import ErrorSeverity

        def failing(**kwargs) -> JobResult:
            errors = ErrorCollector()
            errors.add(GhOpsError(
                code=ErrorCode.INTERNAL_ERROR,
                message="job failed",
                severity=ErrorSeverity.CRITICAL,
            ))
            return JobResult(job="failing", success=False, errors=errors)

        clean_dispatcher.register("failing", failing)
        assert main(["failing"]) == 1
        assert capsys.readouterr().err.strip() != ""

    def test_never_returns_zero_without_running_a_job(self, clean_dispatcher):
        """An empty registry must not look like success."""
        assert clean_dispatcher.list_jobs() == []
        assert main([]) == 1
        assert main(["anything"]) == 1


class TestJobSelection:
    def test_env_var_selects_job(self, registered, monkeypatch):
        monkeypatch.setenv(JOB_ENV_VAR, "status")
        assert main([]) == 0

    def test_argument_takes_precedence_over_env_var(self, registered, monkeypatch, capsys):
        monkeypatch.setenv(JOB_ENV_VAR, "status")
        assert main(["nope"]) == 1
        assert "Unknown job" in capsys.readouterr().err

    def test_blank_env_var_is_not_a_job(self, registered, monkeypatch):
        monkeypatch.setenv(JOB_ENV_VAR, "   ")
        assert main([]) == 1

    def test_env_var_is_stripped(self, registered, monkeypatch):
        monkeypatch.setenv(JOB_ENV_VAR, "  status  ")
        assert main([]) == 0


class TestSubprocessInvocation:
    """End-to-end checks that the documented commands really execute.

    These are the exact invocations the workflows use, run in a subprocess so
    the exit code is the real process exit code.
    """

    def _run(self, *args: str, env_extra: dict | None = None):
        import os

        env = dict(os.environ)
        env.pop(JOB_ENV_VAR, None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

    def test_module_invocation_with_no_job_fails(self):
        result = self._run("src.core.dispatcher")
        assert result.returncode == 1
        assert "Usage" in result.stdout

    def test_module_invocation_with_unknown_job_fails(self):
        result = self._run("src.core.dispatcher", "nope")
        assert result.returncode == 1
        assert "Unknown job" in result.stderr

    def test_module_invocation_does_not_silently_succeed(self):
        """Regression guard for the silent no-op.

        Previously this exited 0 having done nothing. It must now either do the
        work or fail non-zero.
        """
        result = self._run("src.core.dispatcher", "nope")
        assert result.returncode != 0

    def test_jobs_module_lists_all_jobs_on_no_argument(self):
        result = self._run("src.jobs")
        assert result.returncode == 1
        for name in ALL_JOB_NAMES:
            assert name in result.stdout

    def test_jobs_module_runs_status_job(self):
        result = self._run("src.jobs", "status")
        assert result.returncode == 0, result.stderr

    def test_jobs_module_reads_env_var(self):
        result = self._run("src.jobs", env_extra={JOB_ENV_VAR: "status"})
        assert result.returncode == 0, result.stderr

    def test_core_module_entry_point_fails_without_a_job(self):
        result = self._run("src.core")
        assert result.returncode == 1


class TestLocalRunnerScript:
    """scripts/run_local.py is documented in the README, so it must work."""

    def _run(self, *args: str):
        import os

        env = dict(os.environ)
        env.pop(JOB_ENV_VAR, None)
        return subprocess.run(
            [sys.executable, "scripts/run_local.py", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

    def test_no_argument_exits_nonzero(self):
        result = self._run()
        assert result.returncode == 1
        assert "Usage" in result.stdout

    def test_unknown_job_exits_nonzero(self):
        result = self._run("not-a-job")
        assert result.returncode == 1
        assert "Unknown job" in result.stderr

    def test_status_job_succeeds(self):
        result = self._run("status")
        assert result.returncode == 0, result.stderr

    def test_lists_the_real_job_names(self):
        result = self._run()
        for name in ALL_JOB_NAMES:
            assert name in result.stdout

    def test_never_leaks_a_secret_value(self):
        """Loading .env may report variable names, never values."""
        from tests.notifications._fakes import TEST_TOKEN, assert_no_token

        result = self._run("status")
        assert_no_token(result.stdout + result.stderr, TEST_TOKEN)
        assert "=" not in result.stdout.split("Usage")[0].split("Loaded", 1)[-1]


class TestRegistrationContract:
    def test_registered_jobs_match_workflow_names(self, registered):
        assert registered.list_jobs() == sorted(ALL_JOB_NAMES)

    def test_register_all_jobs_is_idempotent(self, registered):
        register_all_jobs(registered)
        assert registered.list_jobs() == sorted(ALL_JOB_NAMES)

    def test_get_dispatcher_returns_shared_instance(self):
        assert get_dispatcher() is get_dispatcher()
