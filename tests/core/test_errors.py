"""Tests for src.core.errors"""
import pytest
from src.core.errors import (
    GhOpsError,
    ErrorCode,
    ErrorSeverity,
    ErrorCollector,
    WriteDeniedError,
    ConfigError,
    StateError,
)


class TestGhOpsError:
    def test_basic_creation(self):
        error = GhOpsError(
            code=ErrorCode.API_NOT_FOUND,
            message="Repository not found",
        )
        assert error.code == ErrorCode.API_NOT_FOUND
        assert error.message == "Repository not found"
        assert error.severity == ErrorSeverity.MEDIUM

    def test_to_dict(self):
        error = GhOpsError(
            code=ErrorCode.API_RATE_LIMITED,
            message="Rate limited",
            module="github.client",
            context={"retry_after": 60},
        )
        d = error.to_dict()
        assert d["code"] == "api_rate_limited"
        assert d["message"] == "Rate limited"
        assert d["severity"] == "medium"
        assert d["module"] == "github.client"
        assert d["context"]["retry_after"] == 60

    def test_to_dict_minimal(self):
        error = GhOpsError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Something broke",
        )
        d = error.to_dict()
        assert "module" not in d
        assert "context" not in d


class TestErrorCollector:
    def test_empty_collector(self):
        collector = ErrorCollector()
        assert not collector.has_errors
        assert collector.count == 0
        assert collector.summary() == "No errors."

    def test_add_errors(self):
        collector = ErrorCollector()
        collector.add(GhOpsError(
            code=ErrorCode.API_NOT_FOUND,
            message="Not found",
            module="github.client",
        ))
        collector.add(GhOpsError(
            code=ErrorCode.API_RATE_LIMITED,
            message="Rate limited",
            module="github.client",
            severity=ErrorSeverity.HIGH,
        ))
        assert collector.has_errors
        assert collector.count == 2

    def test_filter_by_severity(self):
        collector = ErrorCollector()
        collector.add(GhOpsError(
            code=ErrorCode.API_NOT_FOUND,
            message="Not found",
            severity=ErrorSeverity.LOW,
        ))
        collector.add(GhOpsError(
            code=ErrorCode.API_RATE_LIMITED,
            message="Rate limited",
            severity=ErrorSeverity.HIGH,
        ))
        high = collector.by_severity(ErrorSeverity.HIGH)
        assert len(high) == 1
        assert high[0].code == ErrorCode.API_RATE_LIMITED

    def test_filter_by_module(self):
        collector = ErrorCollector()
        collector.add(GhOpsError(
            code=ErrorCode.API_NOT_FOUND,
            message="Not found",
            module="github.client",
        ))
        collector.add(GhOpsError(
            code=ErrorCode.TELEGRAM_DELIVERY_FAILED,
            message="Delivery failed",
            module="notifications.telegram",
        ))
        github_errors = collector.by_module("github.client")
        assert len(github_errors) == 1

    def test_has_critical(self):
        collector = ErrorCollector()
        assert not collector.has_critical
        collector.add(GhOpsError(
            code=ErrorCode.WRITE_DENIED,
            message="Write denied",
            severity=ErrorSeverity.CRITICAL,
        ))
        assert collector.has_critical

    def test_summary(self):
        collector = ErrorCollector()
        collector.add(GhOpsError(
            code=ErrorCode.API_NOT_FOUND,
            message="Not found",
            module="github.client",
        ))
        summary = collector.summary()
        assert "1 error(s)" in summary
        assert "api_not_found" in summary


class TestWriteDeniedError:
    def test_creation(self):
        error = WriteDeniedError("POST", "https://api.github.com/repos/test/issues")
        assert error.code == ErrorCode.WRITE_DENIED
        assert error.severity == ErrorSeverity.CRITICAL
        assert "POST" in error.message
        assert error.context["method"] == "POST"


class TestConfigError:
    def test_creation(self):
        error = ConfigError("Missing field", field_name="token", path="config/settings.yml")
        assert error.code == ErrorCode.CONFIG_INVALID
        assert error.context["field"] == "token"
        assert error.context["path"] == "config/settings.yml"


class TestStateError:
    def test_creation(self):
        error = StateError("File corrupted", filename="repos.json")
        assert error.code == ErrorCode.STATE_LOAD_FAILED
        assert error.severity == ErrorSeverity.HIGH
        assert error.context["filename"] == "repos.json"
