"""Structured error types for gh-ops.

Every module raises typed errors. Collectors never crash the pipeline;
errors are accumulated and reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorSeverity(Enum):
    """Error severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCode(Enum):
    """Machine-readable error codes."""

    # Config errors
    CONFIG_MISSING = "config_missing"
    CONFIG_INVALID = "config_invalid"
    CONFIG_FIELD_MISSING = "config_field_missing"

    # GitHub API errors
    API_AUTH_FAILED = "api_auth_failed"
    API_RATE_LIMITED = "api_rate_limited"
    API_NOT_FOUND = "api_not_found"
    API_FORBIDDEN = "api_forbidden"
    API_TIMEOUT = "api_timeout"
    API_MALFORMED_RESPONSE = "api_malformed_response"
    API_UNEXPECTED_STATUS = "api_unexpected_status"

    # Write errors (V1 enforcement)
    WRITE_DENIED = "write_denied"

    # State errors
    STATE_LOAD_FAILED = "state_load_failed"
    STATE_SAVE_FAILED = "state_save_failed"
    STATE_CORRUPTED = "state_corrupted"
    STATE_UNSUPPORTED_VERSION = "state_unsupported_version"
    STATE_VALIDATION_FAILED = "state_validation_failed"

    # Collector errors
    COLLECTOR_FAILED = "collector_failed"
    COLLECTOR_TIMEOUT = "collector_timeout"

    # Intelligence errors
    SEARCH_FAILED = "search_failed"
    SCORING_FAILED = "scoring_failed"

    # Notification errors
    TELEGRAM_DELIVERY_FAILED = "telegram_delivery_failed"
    TELEGRAM_RATE_LIMITED = "telegram_rate_limited"

    # General
    PARTIAL_FAILURE = "partial_failure"
    INTERNAL_ERROR = "internal_error"


class GhOpsError(Exception):
    """A structured error from any gh-ops module.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable description.
        module: Which module produced this error (e.g. "github.client").
        severity: How bad is this.
        context: Additional structured data about the error.
        cause: The original exception, if any.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        module: str = "",
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.module = module
        self.severity = severity
        self.context = context or {}
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Serialize error for logging/state."""
        d: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.module:
            d["module"] = self.module
        if self.context:
            d["context"] = self.context
        return d


@dataclass
class ErrorCollector:
    """Accumulates errors from a pipeline run without crashing.

    Usage:
        errors = ErrorCollector()
        try:
            do_work()
        except Exception as e:
            errors.add(GhOpsError(
                code=ErrorCode.COLLECTOR_FAILED,
                message=str(e),
                module="github.collectors.repos",
                cause=e,
            ))
        if errors.has_errors:
            report(errors)
    """

    _errors: list[GhOpsError] = field(default_factory=list)

    def add(self, error: GhOpsError) -> None:
        """Record an error."""
        self._errors.append(error)

    @property
    def has_errors(self) -> bool:
        return len(self._errors) > 0

    @property
    def count(self) -> int:
        return len(self._errors)

    @property
    def has_critical(self) -> bool:
        return any(e.severity == ErrorSeverity.CRITICAL for e in self._errors)

    def by_severity(self, severity: ErrorSeverity) -> list[GhOpsError]:
        return [e for e in self._errors if e.severity == severity]

    def by_module(self, module: str) -> list[GhOpsError]:
        return [e for e in self._errors if e.module == module]

    def to_list(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._errors]

    def summary(self) -> str:
        if not self.has_errors:
            return "No errors."
        lines = [f"{self.count} error(s):"]
        for e in self._errors:
            prefix = f"  [{e.severity.value.upper()}]"
            module = f" ({e.module})" if e.module else ""
            lines.append(f"{prefix} {e.code.value}: {e.message}{module}")
        return "\n".join(lines)


class WriteDeniedError(GhOpsError):
    """Raised when V1 read-only enforcement blocks a write attempt."""

    def __init__(
        self,
        method: str,
        url: str,
        module: str = "github.client",
    ) -> None:
        super().__init__(
            code=ErrorCode.WRITE_DENIED,
            message=f"V1 read-only: {method} {url} is not permitted",
            module=module,
            severity=ErrorSeverity.CRITICAL,
            context={"method": method, "url": url},
        )


class ConfigError(GhOpsError):
    """Configuration loading or validation error."""

    def __init__(self, message: str, field_name: str = "", path: str = "") -> None:
        ctx: dict[str, Any] = {}
        if field_name:
            ctx["field"] = field_name
        if path:
            ctx["path"] = path
        super().__init__(
            code=ErrorCode.CONFIG_INVALID,
            message=message,
            module="core.config",
            severity=ErrorSeverity.CRITICAL,
            context=ctx,
        )


class StateError(GhOpsError):
    """State persistence error."""

    def __init__(
        self,
        message: str,
        filename: str = "",
        code: ErrorCode = ErrorCode.STATE_LOAD_FAILED,
        cause: Exception | None = None,
    ) -> None:
        ctx: dict[str, Any] = {}
        if filename:
            ctx["filename"] = filename
        super().__init__(
            code=code,
            message=message,
            module="core.state",
            severity=ErrorSeverity.HIGH,
            context=ctx,
            cause=cause,
        )
