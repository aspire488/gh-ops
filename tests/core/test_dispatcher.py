"""Tests for src.core.dispatcher"""
import pytest
from src.core.dispatcher import (
    Dispatcher,
    JobResult,
    get_dispatcher,
    register_job,
)
from src.core.errors import ErrorCollector


def _make_job(name: str, success: bool = True):
    """Create a simple test job."""
    def job(**kwargs) -> JobResult:
        return JobResult(job=name, success=success)
    return job


class TestDispatcher:
    def test_register_and_run(self):
        dispatcher = Dispatcher()
        dispatcher.register("test", _make_job("test"))
        result = dispatcher.run("test")
        assert result.success
        assert result.job == "test"

    def test_run_unknown_job(self):
        dispatcher = Dispatcher()
        with pytest.raises(KeyError, match="Unknown job"):
            dispatcher.run("nonexistent")

    def test_list_jobs(self):
        dispatcher = Dispatcher()
        dispatcher.register("alpha", _make_job("alpha"))
        dispatcher.register("beta", _make_job("beta"))
        jobs = dispatcher.list_jobs()
        assert jobs == ["alpha", "beta"]

    def test_overwrite_registration(self):
        dispatcher = Dispatcher()
        dispatcher.register("test", _make_job("test", success=True))
        dispatcher.register("test", _make_job("test", success=False))
        result = dispatcher.run("test")
        assert not result.success

    def test_job_exception_handling(self):
        def failing_job(**kwargs) -> JobResult:
            raise RuntimeError("boom")

        dispatcher = Dispatcher()
        dispatcher.register("fail", failing_job)
        result = dispatcher.run("fail")
        assert not result.success
        assert result.errors.count == 1

    def test_job_with_kwargs(self):
        def param_job(message: str = "default", **kwargs) -> JobResult:
            return JobResult(
                job="param",
                success=True,
                data={"message": message},
            )

        dispatcher = Dispatcher()
        dispatcher.register("param", param_job)
        result = dispatcher.run("param", message="hello")
        assert result.data["message"] == "hello"


class TestRegisterDecorator:
    def test_decorator(self):
        @register_job("decorated")
        def my_job(**kwargs) -> JobResult:
            return JobResult(job="decorated", success=True)

        dispatcher = get_dispatcher()
        assert "decorated" in dispatcher.list_jobs()
