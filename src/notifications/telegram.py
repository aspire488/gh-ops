"""Telegram outbound notification adapter for gh-ops Phase 7.

THIN DELIVERY LAYER. This module takes already-computed, structured results
and delivers them to the Telegram Bot API.

This module does NOT and MUST NOT:
- query GitHub or call any GitHub endpoint
- contain intelligence, scoring, ranking, or filtering logic
- mutate gh-ops state
- perform GitHub writes
- accept commands, webhooks, or any inbound control (outbound only)
- call an LLM, agent, MCP, or UEA runtime

Architecture::

    structured results (MonitorResult / Opportunity / DeveloperReport)
        ↓
    formatters (this module)         — deterministic, escaped MarkdownV2
        ↓
    TelegramTransport (this module)  — bounded retries, 429 / Retry-After
        ↓
    Telegram Bot API (https://api.telegram.org)

Security properties:
- The bot token is read from the ``TELEGRAM_BOT_TOKEN`` env var ONLY. It is
  never read from config files, CLI arguments, logs, or state.
- The API base URL is a module constant, so delivery cannot be redirected to
  an arbitrary host, and HTTPS is enforced by that constant.
- The token is embedded in the request URL. Raw request URLs are therefore
  never logged and never placed in error messages or error context, and raw
  exception text is never interpolated into messages (only the exception type
  name is used, since third-party exception text can contain the URL).
- All untrusted GitHub text is escaped with ``escape_markdown_v2`` before it
  is placed in a message.

``src/notifications/telegram.py`` is the single Telegram HTTP exit point, in
the same spirit as ``src/github/client.py`` being the single GitHub exit point.
It deliberately does NOT reuse the GitHub client: that client is GET-only and
GitHub-specific, while Telegram delivery requires POST.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import requests

from src.core.errors import ConfigError, ErrorCode, ErrorSeverity, GhOpsError
from src.core.monitor import MonitorResult, MonitorStatus
from src.core.rate_limit import calculate_backoff, parse_retry_after
from src.developer.reports import DeveloperReport, format_summary
from src.utils.logging import get_logger
from src.utils.text import escape_markdown_v2, split_message

logger = get_logger("notifications.telegram")


# ── Constants ────────────────────────────────────────────────────

#: Fixed Telegram Bot API base. Not configurable by design — see module docstring.
TELEGRAM_API_BASE = "https://api.telegram.org"

#: The ONLY environment variable consulted for the bot token.
TELEGRAM_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"

#: Telegram's hard limit for a single text message.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

#: Defaults used when config/telegram.yml omits a value.
TELEGRAM_DEFAULT_TIMEOUT_SECONDS = 10.0
TELEGRAM_DEFAULT_RETRY_ATTEMPTS = 3
TELEGRAM_DEFAULT_RETRY_DELAY_SECONDS = 5.0
TELEGRAM_DEFAULT_PARSE_MODE = "MarkdownV2"

#: Notification topics. A chat target with no topics receives every topic.
TOPIC_ALERTS = "alerts"
TOPIC_OSS_OPPORTUNITIES = "oss_opportunities"
TOPIC_DEVELOPER_REPORT = "developer_report"
TOPIC_RUN_SUMMARY = "run_summary"
TOPIC_OSS_SUMMARY = "oss_summary"
TOPIC_SECURITY = "security"

#: Telegram error descriptions are untrusted and unbounded; keep errors small.
_MAX_ERROR_DESCRIPTION = 300


# ── Errors ───────────────────────────────────────────────────────


class TelegramError(GhOpsError):
    """Structured error from Telegram delivery.

    Carries no token, no URL, and no raw exception text.

    Attributes:
        status_code: HTTP status from Telegram (0 for transport failures).
        operation: Telegram Bot API method name (e.g. "sendMessage").
        chat_id: Target chat, for diagnostics.
        retry_after: Seconds Telegram asked us to wait, if provided.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.TELEGRAM_DELIVERY_FAILED,
        status_code: int = 0,
        operation: str = "",
        chat_id: str = "",
        retry_after: float | None = None,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        cause: Exception | None = None,
    ) -> None:
        context: dict[str, Any] = {"status_code": status_code}
        if operation:
            context["operation"] = operation
        if chat_id:
            context["chat_id"] = str(chat_id)
        if retry_after is not None:
            context["retry_after"] = retry_after

        super().__init__(
            code=code,
            message=message,
            module="notifications.telegram",
            severity=severity,
            context=context,
            cause=cause,
        )
        self.status_code = status_code
        self.operation = operation
        self.chat_id = str(chat_id)
        self.retry_after = retry_after


# ── Configuration ────────────────────────────────────────────────


@dataclass(frozen=True)
class ChatTarget:
    """A delivery destination.

    Attributes:
        chat_id: Telegram chat ID (numeric ID or @channelusername).
        topics: Topics this chat subscribes to. Empty means "all topics".
    """

    chat_id: str
    topics: tuple[str, ...] = ()

    def accepts(self, topic: str) -> bool:
        """True if this chat should receive the given topic."""
        return not self.topics or topic in self.topics

    def to_dict(self) -> dict[str, Any]:
        return {"chat_id": self.chat_id, "topics": list(self.topics)}


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram delivery configuration.

    Contains no secrets: the bot token is never part of configuration and is
    resolved separately from the environment.

    Attributes:
        enabled: Master switch. When False, delivery is a safe no-op.
        targets: Chat destinations with optional topic subscriptions.
        max_length: Maximum characters per message (clamped to Telegram's 4096).
        parse_mode: Telegram parse mode (default "MarkdownV2").
        split_separator: Preferred split point when a message is too long.
        retry_attempts: Maximum retries per request (bounded).
        retry_delay_seconds: Base delay for exponential backoff.
        timeout_seconds: Finite per-request timeout.
    """

    enabled: bool = False
    run_summary: bool = False
    oss_summary: bool = False
    security_summary: bool = False
    targets: tuple[ChatTarget, ...] = ()
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH
    parse_mode: str = TELEGRAM_DEFAULT_PARSE_MODE
    split_separator: str = "\n\n"
    retry_attempts: int = TELEGRAM_DEFAULT_RETRY_ATTEMPTS
    retry_delay_seconds: float = TELEGRAM_DEFAULT_RETRY_DELAY_SECONDS
    timeout_seconds: float = TELEGRAM_DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TelegramConfig:
        """Parse configuration from a mapping.

        Accepts either the ``telegram.yml`` file contents (``{"telegram": {...}}``)
        or the already-unwrapped section.

        Args:
            data: Configuration mapping.

        Returns:
            Parsed TelegramConfig.

        Raises:
            ConfigError: If the mapping or any field is malformed.
        """
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError(
                f"Telegram config must be a mapping, got {type(data).__name__}",
                field_name="telegram",
            )

        section = data.get("telegram", data)
        if not isinstance(section, dict):
            raise ConfigError(
                f"Telegram config must be a mapping, got {type(section).__name__}",
                field_name="telegram",
            )

        # The token must come from TELEGRAM_BOT_TOKEN. Fail loudly rather than
        # silently ignoring a configuration that points somewhere else.
        token_ref = section.get("bot_token_ref", TELEGRAM_TOKEN_ENV_VAR)
        if token_ref != TELEGRAM_TOKEN_ENV_VAR:
            raise ConfigError(
                f"Telegram bot token must be read from {TELEGRAM_TOKEN_ENV_VAR} "
                f"(config sets bot_token_ref={token_ref!r})",
                field_name="telegram.bot_token_ref",
            )

        message = section.get("message", {}) or {}
        if not isinstance(message, dict):
            raise ConfigError(
                "telegram.message must be a mapping",
                field_name="telegram.message",
            )

        max_length = int(message.get("max_length", TELEGRAM_MAX_MESSAGE_LENGTH))
        if max_length <= 0:
            raise ConfigError(
                f"telegram.message.max_length must be positive, got {max_length}",
                field_name="telegram.message.max_length",
            )
        if max_length > TELEGRAM_MAX_MESSAGE_LENGTH:
            logger.warning(
                "telegram.message.max_length %d exceeds Telegram's limit; clamping to %d",
                max_length,
                TELEGRAM_MAX_MESSAGE_LENGTH,
            )
            max_length = TELEGRAM_MAX_MESSAGE_LENGTH

        retry_attempts = int(message.get("retry_attempts", TELEGRAM_DEFAULT_RETRY_ATTEMPTS))
        if retry_attempts < 0:
            raise ConfigError(
                f"telegram.message.retry_attempts must not be negative, got {retry_attempts}",
                field_name="telegram.message.retry_attempts",
            )

        retry_delay = float(
            message.get("retry_delay_seconds", TELEGRAM_DEFAULT_RETRY_DELAY_SECONDS)
        )
        if retry_delay < 0:
            raise ConfigError(
                f"telegram.message.retry_delay_seconds must not be negative, got {retry_delay}",
                field_name="telegram.message.retry_delay_seconds",
            )

        timeout = float(message.get("timeout_seconds", TELEGRAM_DEFAULT_TIMEOUT_SECONDS))
        if timeout <= 0:
            raise ConfigError(
                f"telegram.message.timeout_seconds must be positive, got {timeout}",
                field_name="telegram.message.timeout_seconds",
            )

        return cls(
            enabled=bool(section.get("enabled", False)),
            run_summary=bool(section.get("run_summary", False)),
            oss_summary=bool(section.get("oss_summary", False)),
            security_summary=bool(section.get("security_summary", False)),
            targets=_parse_chat_ids(section.get("chat_ids", [])),
            max_length=max_length,
            parse_mode=str(message.get("parse_mode", TELEGRAM_DEFAULT_PARSE_MODE)),
            split_separator=str(message.get("split_separator", "\n\n")),
            retry_attempts=retry_attempts,
            retry_delay_seconds=retry_delay,
            timeout_seconds=timeout,
        )

    def targets_for(self, topic: str) -> tuple[ChatTarget, ...]:
        """Chats subscribed to ``topic`` (in configured order)."""
        return tuple(t for t in self.targets if t.accepts(topic))

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Contains no secrets."""
        return {
            "enabled": self.enabled,
            "run_summary": self.run_summary,
            "oss_summary": self.oss_summary,
            "security_summary": self.security_summary,
            "targets": [t.to_dict() for t in self.targets],
            "max_length": self.max_length,
            "parse_mode": self.parse_mode,
            "retry_attempts": self.retry_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "timeout_seconds": self.timeout_seconds,
        }


def _parse_chat_ids(raw: Any) -> tuple[ChatTarget, ...]:
    """Parse the ``chat_ids`` config list into ChatTargets.

    Accepts bare IDs (int or str) and mappings with ``chat_id`` / ``topics``.

    Raises:
        ConfigError: If an entry is malformed or a chat_id is empty.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(
            f"telegram.chat_ids must be a list, got {type(raw).__name__}",
            field_name="telegram.chat_ids",
        )

    targets: list[ChatTarget] = []
    for entry in raw:
        if isinstance(entry, (int, str)):
            chat_id = str(entry).strip()
            topics: tuple[str, ...] = ()
        elif isinstance(entry, dict):
            chat_id = str(entry.get("chat_id", "")).strip()
            topics_raw = entry.get("topics", []) or []
            if not isinstance(topics_raw, (list, tuple)):
                raise ConfigError(
                    "telegram.chat_ids[].topics must be a list",
                    field_name="telegram.chat_ids",
                )
            topics = tuple(str(t) for t in topics_raw)
        else:
            raise ConfigError(
                f"telegram.chat_ids entries must be an ID or a mapping, "
                f"got {type(entry).__name__}",
                field_name="telegram.chat_ids",
            )

        if not chat_id:
            raise ConfigError(
                "telegram.chat_ids entries must have a non-empty chat_id",
                field_name="telegram.chat_ids",
            )
        targets.append(ChatTarget(chat_id=chat_id, topics=topics))

    return tuple(targets)


def resolve_telegram_token() -> str:
    """Resolve the Telegram bot token from the environment.

    The token is read from ``TELEGRAM_BOT_TOKEN`` ONLY.

    Returns:
        The token.

    Raises:
        TelegramError: If the environment variable is unset or blank.
    """
    token = os.environ.get(TELEGRAM_TOKEN_ENV_VAR, "").strip()
    if not token:
        raise TelegramError(
            f"No Telegram bot token found. Set the {TELEGRAM_TOKEN_ENV_VAR} environment variable.",
            code=ErrorCode.TELEGRAM_DELIVERY_FAILED,
            operation="resolve_token",
            severity=ErrorSeverity.CRITICAL,
        )
    return token


def has_telegram_token() -> bool:
    """True if a usable token is present in the environment."""
    return bool(os.environ.get(TELEGRAM_TOKEN_ENV_VAR, "").strip())


def load_telegram_config(config: Any = None) -> TelegramConfig:
    """Resolve a TelegramConfig from explicit config or ``config/telegram.yml``.

    Args:
        config: A TelegramConfig, a raw mapping, a Config instance, or None to
                load ``config/telegram.yml`` from disk.

    Returns:
        Resolved TelegramConfig.
    """
    if isinstance(config, TelegramConfig):
        return config
    if isinstance(config, dict):
        return TelegramConfig.from_dict(config)
    if config is not None and hasattr(config, "get"):
        return TelegramConfig.from_dict(config.get("telegram", default={}) or {})

    from src.core.config import Config as CoreConfig

    return TelegramConfig.from_dict(CoreConfig.load().get("telegram", default={}) or {})


# ── Formatters ───────────────────────────────────────────────────
#
# Every formatter returns a list of finished, independently-valid MarkdownV2
# messages, each within ``max_length``. Untrusted GitHub text is escaped with
# the existing ``escape_markdown_v2`` utility; the only unescaped MarkdownV2
# markup is the bold title added by ``_compose_messages``.


def _has_dangling_escape(text: str) -> bool:
    """True if ``text`` ends with an odd number of backslashes."""
    trailing = len(text) - len(text.rstrip("\\"))
    return trailing % 2 == 1


def _split_escaped(escaped: str, limit: int, separator: str) -> list[str]:
    """Split already-escaped MarkdownV2 text into chunks that fit ``limit``.

    Escaped body text contains no unescaped MarkdownV2 control characters, so
    cutting it anywhere is safe — except immediately after a backslash. Any
    dangling backslash is moved to the start of the following chunk, where it
    still escapes the same character.

    Args:
        escaped: Escaped text.
        limit: Maximum characters per chunk.
        separator: Preferred split point.

    Returns:
        Chunks, each at most ``limit + 1`` characters.
    """
    chunks = split_message(escaped, max_length=limit, separator=separator)

    repaired: list[str] = []
    carry = ""
    for chunk in chunks:
        chunk = carry + chunk
        carry = ""
        if _has_dangling_escape(chunk):
            carry = chunk[-1]
            chunk = chunk[:-1]
        if chunk:
            repaired.append(chunk)
    if carry:
        repaired.append(carry)

    return repaired or [escaped]


def _compose_messages(
    title: str,
    blocks: Sequence[str],
    *,
    max_length: int,
    separator: str,
) -> list[str]:
    """Compose escaped, split, message-sized MarkdownV2 messages.

    Blocks are self-contained units (for example, one item per block). Each
    block is escaped independently, so packing blocks into a message can never
    break an escape sequence or an entity.

    Args:
        title: Plain-text title, rendered bold.
        blocks: Plain-text blocks.
        max_length: Maximum characters per returned message.
        separator: Joins blocks and is the preferred split point.

    Returns:
        List of messages, each at most ``max_length`` characters.

    Raises:
        ValueError: If ``max_length`` is too small to hold the title.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")

    safe_title = f"*{escape_markdown_v2(title)}*"
    budget = max_length - len(safe_title) - len(separator)
    if budget < 2:
        raise ValueError(f"max_length {max_length} is too small for the message title")

    pieces: list[str] = []
    for block in blocks:
        text = str(block).strip()
        if not text:
            continue
        escaped = escape_markdown_v2(text)
        if len(escaped) <= budget:
            pieces.append(escaped)
        else:
            # Split with one character of headroom: a dangling backslash may be
            # carried onto the front of the next chunk.
            pieces.extend(_split_escaped(escaped, budget - 1, separator))

    if not pieces:
        return []

    messages: list[str] = []
    current = safe_title
    for piece in pieces:
        candidate = f"{current}{separator}{piece}"
        if len(candidate) <= max_length:
            current = candidate
        else:
            messages.append(current)
            current = piece
    if current:
        messages.append(current)

    return messages


def format_monitor_alerts(
    results: Sequence[MonitorResult],
    *,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    separator: str = "\n\n",
) -> list[str]:
    """Format monitor results for delivery.

    Only ALERT and ERROR results are notifiable; OK / CHANGED / SKIPPED results
    are not notifications. Input order is preserved.

    Args:
        results: Monitor results.
        max_length: Maximum characters per message.
        separator: Preferred split point.

    Returns:
        Messages, or an empty list when there is nothing to notify about.
    """
    notifiable = [
        r for r in results
        if r.status in (MonitorStatus.ALERT, MonitorStatus.ERROR)
    ]
    if not notifiable:
        return []

    blocks = [
        f"[{r.status.value.upper()}] {r.monitor}: {r.resource}\n{r.summary}"
        for r in notifiable
    ]
    return _compose_messages(
        f"gh-ops monitor alerts ({len(notifiable)})",
        blocks,
        max_length=max_length,
        separator=separator,
    )


def _format_score(value: Any) -> str:
    """Format a score deterministically, tolerating missing values."""
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "n/a"


def format_oss_opportunities(
    opportunities: Any,
    *,
    limit: int = 10,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    separator: str = "\n\n",
) -> list[str]:
    """Format OSS opportunities for delivery.

    Opportunities are read structurally rather than by type. This module never
    imports ``src.intelligence.oss`` (whose package ``__init__`` pulls in the
    hunter and therefore the GitHub client), keeping the delivery layer free of
    any GitHub dependency.

    Args:
        opportunities: A HunterResult (anything exposing ``.opportunities``) or a
            sequence of Opportunity-like objects.
        limit: Maximum number of opportunities to include. Negative means no limit.
        max_length: Maximum characters per message.
        separator: Preferred split point.

    Returns:
        Messages, or an empty list when there is nothing to send.
    """
    items = list(getattr(opportunities, "opportunities", opportunities))
    if limit >= 0:
        items = items[:limit]
    if not items:
        return []

    blocks: list[str] = []
    for index, opp in enumerate(items, start=1):
        repo = str(getattr(opp, "repo_full_name", "") or "")
        number = getattr(opp, "issue_number", 0) or 0
        if not repo and not number:
            continue

        lines = [f"{index}. {repo}#{number}", str(getattr(opp, "title", "") or "")]
        facts = [
            f"score {_format_score(getattr(opp, 'score', 0.0))}",
            f"stars {getattr(opp, 'repo_stars', 0) or 0}",
        ]
        language = getattr(opp, "repo_language", None)
        if language:
            facts.append(str(language))
        lines.append(" | ".join(facts))

        url = str(getattr(opp, "html_url", "") or "")
        if url:
            lines.append(url)
        blocks.append("\n".join(lines))

    if not blocks:
        return []

    return _compose_messages(
        f"gh-ops OSS opportunities ({len(blocks)})",
        blocks,
        max_length=max_length,
        separator=separator,
    )


def format_developer_report(
    report: DeveloperReport,
    *,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    separator: str = "\n\n",
) -> list[str]:
    """Format a DeveloperReport for delivery.

    Reuses the deterministic ``format_summary`` renderer from Phase 6 and
    escapes its output, so untrusted repository names cannot break the message.

    Args:
        report: The developer report.
        max_length: Maximum characters per message.
        separator: Preferred split point.

    Returns:
        Messages, or an empty list when the report renders to nothing.
    """
    body = format_summary(report)
    if not body.strip():
        return []

    return _compose_messages(
        "gh-ops developer report",
        [body],
        max_length=max_length,
        separator=separator,
    )


# ── Transport ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunSummary:
    run_type: str
    repositories_checked: int
    items_collected: int
    alerts_generated: int
    state_persisted: bool


def format_run_summary(summary: RunSummary, *, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH, separator: str = "\n\n") -> list[str]:
    state = "persisted" if summary.state_persisted else "not persisted"
    body = f"Run type: {summary.run_type}\nRepositories checked: {summary.repositories_checked}\nItems collected: {summary.items_collected}\nAlerts: {summary.alerts_generated}\nState: {state}"
    return _compose_messages("GH-OPS Monitoring Complete", [body], max_length=max_length, separator=separator)


@dataclass(frozen=True)
class OssRunSummary:
    queries_run: int
    total_issues_found: int
    opportunities: int
    duplicates: int


def format_oss_run_summary(summary: OssRunSummary, *, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH, separator: str = "\n\n") -> list[str]:
    body = f"Queries: {summary.queries_run}\nResults: {summary.total_issues_found}\nOpportunities: {summary.opportunities}\nDuplicates: {summary.duplicates}"
    if summary.opportunities == 0:
        body += "\n\nNo new qualifying opportunities found."
    return _compose_messages("GH-OPS · OSS Hunter Complete", [body], max_length=max_length, separator=separator)


def format_security_alerts(
    findings: Any,
    *,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    separator: str = "\n\n",
) -> list[str]:
    """Format new security findings for delivery.

    Findings are read structurally rather than by type so this module never
    imports ``src.intelligence.security`` (keeping the delivery layer free of
    intelligence dependencies).

    Args:
        findings: Sequence of SecurityFinding-like objects (or a report with
            ``.actionable``).
        max_length: Maximum characters per message.
        separator: Preferred split point.

    Returns:
        Messages, or an empty list when there is nothing to send.
    """
    items = list(getattr(findings, "actionable", findings))
    if not items:
        return []

    blocks: list[str] = []
    for index, finding in enumerate(items, start=1):
        severity = str(getattr(finding, "severity", "unknown") or "unknown").upper()
        repository = str(getattr(finding, "repository", "") or "")
        source = str(getattr(finding, "source", "") or "")
        package = str(getattr(finding, "package_name", "") or "")
        summary = str(getattr(finding, "summary", "") or "")
        html_url = str(getattr(finding, "html_url", "") or "")

        header = f"{index}. [{severity}] {repository}"
        if source:
            header = f"{header} ({source})"
        lines = [header]
        if package:
            lines.append(f"Package: {package}")
        if summary:
            lines.append(summary)
        if html_url:
            lines.append(html_url)
        blocks.append("\n".join(lines))

    return _compose_messages(
        f"gh-ops security alerts ({len(items)})",
        blocks,
        max_length=max_length,
        separator=separator,
    )


def format_security_summary(
    report: Any,
    *,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
    separator: str = "\n\n",
) -> list[str]:
    """Format a SecurityReport-like object as a factual completion summary."""
    total = int(getattr(report, "total", 0) or 0)
    open_count = int(getattr(report, "open_count", 0) or 0)
    actionable = int(getattr(report, "actionable_count", 0) or 0)
    by_severity = dict(getattr(report, "by_severity", {}) or {})

    severity_line = ", ".join(f"{k}: {v}" for k, v in sorted(by_severity.items()))
    if not severity_line:
        severity_line = "none"

    body = (
        f"Findings: {total}\n"
        f"Open: {open_count}\n"
        f"Actionable: {actionable}\n"
        f"By severity: {severity_line}"
    )
    if total == 0:
        body += "\n\nNo security alerts collected."
    return _compose_messages(
        "GH-OPS · Security Intelligence",
        [body],
        max_length=max_length,
        separator=separator,
    )


class TelegramTransport:
    """HTTP transport for the Telegram Bot API.

    The only component that performs network I/O. Handles a finite timeout,
    bounded retries, 429 / Retry-After, 5xx backoff, and structured errors.

    Args:
        token: Bot token (from the environment).
        timeout: Per-request timeout in seconds.
        max_retries: Maximum retries per request.
        retry_delay: Base delay for exponential backoff.
        max_retry_delay: Ceiling for a single backoff delay.
        parse_mode: Default Telegram parse mode.
        session: Optional requests.Session-like object (for testing).
        sleep: Optional sleep function (for testing).
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = TELEGRAM_DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = TELEGRAM_DEFAULT_RETRY_ATTEMPTS,
        retry_delay: float = TELEGRAM_DEFAULT_RETRY_DELAY_SECONDS,
        max_retry_delay: float = 30.0,
        parse_mode: str = TELEGRAM_DEFAULT_PARSE_MODE,
        session: Any = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not token or not str(token).strip():
            raise TelegramError(
                "Telegram transport requires a bot token",
                operation="init",
                severity=ErrorSeverity.CRITICAL,
            )
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")

        self._token = str(token).strip()
        self._timeout = float(timeout)
        self._max_retries = int(max_retries)
        self._retry_delay = float(retry_delay)
        self._max_retry_delay = float(max_retry_delay)
        self._parse_mode = parse_mode
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep if sleep is not None else time.sleep

    # -- public API ------------------------------------------------

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send one text message to one chat.

        Args:
            chat_id: Target chat.
            text: Message text (already escaped for the parse mode).
            parse_mode: Override the default parse mode. Pass "" for none.

        Returns:
            The parsed Telegram response body.

        Raises:
            TelegramError: On any delivery failure.
        """
        if not text:
            raise TelegramError(
                "Refusing to send an empty message",
                operation="sendMessage",
                chat_id=chat_id,
            )

        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        mode = self._parse_mode if parse_mode is None else parse_mode
        if mode:
            payload["parse_mode"] = mode

        return self._request("sendMessage", payload, chat_id=chat_id)

    def close(self) -> None:
        """Close the underlying HTTP session, if it supports it."""
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> TelegramTransport:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        """Safe repr that never exposes the token or the token-bearing URL."""
        return (
            f"TelegramTransport(timeout={self._timeout}, "
            f"max_retries={self._max_retries}, parse_mode={self._parse_mode!r})"
        )

    # -- internals -------------------------------------------------

    def _request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        chat_id: str = "",
    ) -> dict[str, Any]:
        """POST to the Telegram Bot API with bounded retries.

        Note: the request URL embeds the token, so it is never logged and never
        attached to an error.
        """
        url = f"{TELEGRAM_API_BASE}/bot{self._token}/{operation}"
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            try:
                response = self._session.request(
                    method="POST",
                    url=url,
                    json=payload,
                    timeout=self._timeout,
                )
            except requests.exceptions.Timeout:
                if attempt + 1 < attempts:
                    self._wait(attempt, operation, "timeout")
                    continue
                raise TelegramError(
                    f"Telegram request timed out after {self._timeout}s",
                    status_code=0,
                    operation=operation,
                    chat_id=chat_id,
                    severity=ErrorSeverity.HIGH,
                ) from None
            except requests.exceptions.RequestException as e:
                # Only the exception type is used: exception text can embed the URL.
                if attempt + 1 < attempts:
                    self._wait(attempt, operation, type(e).__name__)
                    continue
                raise TelegramError(
                    f"Telegram request failed ({type(e).__name__})",
                    status_code=0,
                    operation=operation,
                    chat_id=chat_id,
                    severity=ErrorSeverity.HIGH,
                    cause=e,
                ) from e

            status = response.status_code

            if status == 429:
                retry_after = self._retry_after(response)
                if attempt + 1 < attempts:
                    self._wait(attempt, operation, "rate limited", retry_after)
                    continue
                raise TelegramError(
                    "Telegram rate limit exceeded",
                    code=ErrorCode.TELEGRAM_RATE_LIMITED,
                    status_code=status,
                    operation=operation,
                    chat_id=chat_id,
                    retry_after=retry_after,
                    severity=ErrorSeverity.HIGH,
                )

            if status >= 500:
                if attempt + 1 < attempts:
                    self._wait(attempt, operation, f"server error {status}")
                    continue
                raise TelegramError(
                    f"Telegram server error ({status}) after {self._max_retries} retries",
                    status_code=status,
                    operation=operation,
                    chat_id=chat_id,
                    severity=ErrorSeverity.HIGH,
                )

            if status >= 400:
                raise self._error_from_response(response, operation, chat_id)

            return self._parse_body(response, operation, chat_id)

        raise TelegramError(
            "Telegram request failed after retries",
            operation=operation,
            chat_id=chat_id,
        )

    def _wait(
        self,
        attempt: int,
        operation: str,
        reason: str,
        retry_after: float | None = None,
    ) -> None:
        """Sleep before the next attempt. Never logs the token or URL."""
        if retry_after is not None and retry_after > 0:
            delay = min(float(retry_after), self._max_retry_delay)
        else:
            delay = calculate_backoff(
                attempt,
                base_seconds=self._retry_delay or 1.0,
                max_seconds=self._max_retry_delay,
            )
        logger.warning(
            "Telegram %s retry %d in %.1fs (%s)",
            operation,
            attempt + 1,
            delay,
            reason,
        )
        self._sleep(delay)

    @staticmethod
    def _safe_json(response: Any) -> dict[str, Any]:
        """Parse a response body, returning {} when it is not a JSON mapping."""
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @classmethod
    def _retry_after(cls, response: Any) -> float | None:
        """Read Retry-After from headers, falling back to the JSON body."""
        headers = {k.lower(): v for k, v in getattr(response, "headers", {}).items()}
        header_value = parse_retry_after(headers)
        if header_value is not None:
            return float(header_value)

        parameters = cls._safe_json(response).get("parameters")
        if isinstance(parameters, dict):
            value = parameters.get("retry_after")
            if isinstance(value, (int, float)) and value >= 0:
                return float(value)
        return None

    def _error_from_response(
        self,
        response: Any,
        operation: str,
        chat_id: str,
    ) -> TelegramError:
        """Map a 4xx response to a structured error."""
        status = response.status_code
        description = str(self._safe_json(response).get("description", "") or "")
        description = " ".join(description.split())[:_MAX_ERROR_DESCRIPTION]
        message = description or f"Telegram API error (HTTP {status})"

        return TelegramError(
            message,
            status_code=status,
            operation=operation,
            chat_id=chat_id,
            severity=ErrorSeverity.HIGH if status in (401, 403) else ErrorSeverity.MEDIUM,
        )

    def _parse_body(
        self,
        response: Any,
        operation: str,
        chat_id: str,
    ) -> dict[str, Any]:
        """Validate a 2xx body. Telegram reports rejections with ``ok: false``."""
        body = self._safe_json(response)
        if not body:
            raise TelegramError(
                "Telegram returned a malformed response",
                status_code=response.status_code,
                operation=operation,
                chat_id=chat_id,
            )

        if body.get("ok") is not True:
            description = str(body.get("description", "") or "Telegram rejected the message")
            description = " ".join(description.split())[:_MAX_ERROR_DESCRIPTION]
            raise TelegramError(
                description,
                status_code=response.status_code,
                operation=operation,
                chat_id=chat_id,
            )

        return body


# ── Notifier ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeliveryAttempt:
    """Outcome of delivering a message set to a single chat.

    Attributes:
        chat_id: The target chat.
        delivered: Number of messages successfully sent.
        ok: True when every message for this chat was delivered.
    """

    chat_id: str
    delivered: int
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"chat_id": self.chat_id, "delivered": self.delivered, "ok": self.ok}


@dataclass(frozen=True)
class NotificationResult:
    """Structured outcome of a notification attempt.

    Attributes:
        topic: Notification topic.
        sent: Total messages delivered across all chats.
        skipped: True when nothing was attempted.
        skip_reason: Why nothing was attempted.
        attempts: Per-chat delivery outcomes.
        errors: Structured error dicts (never contain the token).
    """

    topic: str
    sent: int = 0
    skipped: bool = False
    skip_reason: str = ""
    attempts: tuple[DeliveryAttempt, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()

    @property
    def failed_chats(self) -> int:
        """Number of chats that did not receive every message."""
        return sum(1 for a in self.attempts if not a.ok)

    @property
    def ok(self) -> bool:
        """True only when delivery was attempted, produced no errors, and all chats succeeded."""
        return not self.skipped and not self.errors and self.failed_chats == 0

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization."""
        return {
            "topic": self.topic,
            "sent": self.sent,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "failed_chats": self.failed_chats,
            "ok": self.ok,
            "attempts": [a.to_dict() for a in self.attempts],
            "errors": [dict(e) for e in self.errors],
        }


class TelegramNotifier:
    """Delivers formatted messages to configured chats.

    Delivery never raises for configuration or delivery problems: failures are
    isolated per chat and reported through ``NotificationResult`` so a failed
    notification cannot break a collection run.

    Args:
        config: Telegram configuration.
        transport: Optional transport (built lazily when omitted, so a disabled
            or unconfigured notifier never resolves a token).
        token: Optional pre-resolved token (for testing).
    """

    def __init__(
        self,
        config: TelegramConfig,
        transport: TelegramTransport | None = None,
        *,
        token: str | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._token = token

    @property
    def config(self) -> TelegramConfig:
        """The configuration in use."""
        return self._config

    def send(self, messages: Sequence[str], topic: str) -> NotificationResult:
        """Deliver ``messages`` to every chat subscribed to ``topic``.

        Args:
            messages: Finished message texts (already escaped).
            topic: Notification topic used for chat routing.

        Returns:
            NotificationResult describing what happened.
        """
        if not self._config.enabled:
            logger.info("Telegram delivery skipped for topic '%s': disabled", topic)
            return NotificationResult(topic=topic, skipped=True, skip_reason="telegram disabled")

        if not messages:
            return NotificationResult(topic=topic, skipped=True, skip_reason="nothing to send")

        targets = self._config.targets_for(topic)
        if not targets:
            return NotificationResult(
                topic=topic,
                skipped=True,
                skip_reason=f"no chat configured for topic '{topic}'",
            )

        try:
            transport = self._resolve_transport()
        except GhOpsError as e:
            logger.warning("Telegram delivery skipped for topic '%s': %s", topic, e.code.value)
            return NotificationResult(
                topic=topic,
                skipped=True,
                skip_reason="telegram not configured",
                errors=(e.to_dict(),),
            )

        attempts: list[DeliveryAttempt] = []
        errors: list[dict[str, Any]] = []

        for target in targets:
            delivered = 0
            ok = True
            for message in messages:
                try:
                    transport.send_message(target.chat_id, message)
                    delivered += 1
                except GhOpsError as e:
                    # Isolation: stop this chat, keep serving the others.
                    ok = False
                    errors.append(e.to_dict())
                    logger.warning(
                        "Telegram delivery failed for topic '%s' chat %s: %s",
                        topic,
                        target.chat_id,
                        e.code.value,
                    )
                    break
            attempts.append(DeliveryAttempt(chat_id=target.chat_id, delivered=delivered, ok=ok))

        result = NotificationResult(
            topic=topic,
            sent=sum(a.delivered for a in attempts),
            attempts=tuple(attempts),
            errors=tuple(errors),
        )
        logger.info(
            "Telegram delivery for topic '%s': %d message(s) to %d chat(s), %d failed",
            topic,
            result.sent,
            len(result.attempts),
            result.failed_chats,
        )
        return result

    def _resolve_transport(self) -> TelegramTransport:
        """Return the injected transport, or build one from the environment."""
        if self._transport is not None:
            return self._transport

        token = self._token if self._token is not None else resolve_telegram_token()
        return TelegramTransport(
            token,
            timeout=self._config.timeout_seconds,
            max_retries=self._config.retry_attempts,
            retry_delay=self._config.retry_delay_seconds,
            parse_mode=self._config.parse_mode,
        )


# ── Convenience entry points ─────────────────────────────────────


def send_notification(
    messages: Sequence[str],
    topic: str,
    *,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Deliver pre-formatted messages. Never raises for delivery failures."""
    resolved = load_telegram_config(config)
    return TelegramNotifier(resolved, transport=transport).send(messages, topic)


def notify_monitor_alerts(
    results: Sequence[MonitorResult],
    *,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Format and deliver monitor alerts."""
    resolved = load_telegram_config(config)
    notifier = TelegramNotifier(resolved, transport=transport)
    messages = format_monitor_alerts(
        results,
        max_length=resolved.max_length,
        separator=resolved.split_separator,
    )
    return notifier.send(messages, TOPIC_ALERTS)


def notify_oss_opportunities(
    opportunities: Any,
    *,
    limit: int = 10,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Format and deliver OSS opportunities."""
    resolved = load_telegram_config(config)
    notifier = TelegramNotifier(resolved, transport=transport)
    messages = format_oss_opportunities(
        opportunities,
        limit=limit,
        max_length=resolved.max_length,
        separator=resolved.split_separator,
    )
    return notifier.send(messages, TOPIC_OSS_OPPORTUNITIES)


def notify_developer_report(
    report: DeveloperReport,
    *,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Format and deliver a developer report."""
    resolved = load_telegram_config(config)
    notifier = TelegramNotifier(resolved, transport=transport)
    messages = format_developer_report(
        report,
        max_length=resolved.max_length,
        separator=resolved.split_separator,
    )
    return notifier.send(messages, TOPIC_DEVELOPER_REPORT)


def notify_run_summary(summary: RunSummary, *, config: Any = None, transport: TelegramTransport | None = None) -> NotificationResult:
    resolved = load_telegram_config(config)
    if not resolved.run_summary:
        return NotificationResult(topic=TOPIC_RUN_SUMMARY, skipped=True, skip_reason="run summaries disabled")
    messages = format_run_summary(summary, max_length=resolved.max_length, separator=resolved.split_separator)
    return TelegramNotifier(resolved, transport=transport).send(messages, TOPIC_RUN_SUMMARY)


def notify_oss_run_summary(summary: OssRunSummary, *, config: Any = None, transport: TelegramTransport | None = None) -> NotificationResult:
    resolved = load_telegram_config(config)
    if not resolved.oss_summary:
        return NotificationResult(topic=TOPIC_OSS_SUMMARY, skipped=True, skip_reason="OSS summaries disabled")
    messages = format_oss_run_summary(summary, max_length=resolved.max_length, separator=resolved.split_separator)
    return TelegramNotifier(resolved, transport=transport).send(messages, TOPIC_OSS_SUMMARY)


def notify_security_alerts(
    findings: Any,
    *,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Format and deliver new security findings (at-least-once batch)."""
    resolved = load_telegram_config(config)
    notifier = TelegramNotifier(resolved, transport=transport)
    messages = format_security_alerts(
        findings,
        max_length=resolved.max_length,
        separator=resolved.split_separator,
    )
    return notifier.send(messages, TOPIC_SECURITY)


def notify_security_summary(
    report: Any,
    *,
    config: Any = None,
    transport: TelegramTransport | None = None,
) -> NotificationResult:
    """Format and deliver the security intelligence run summary."""
    resolved = load_telegram_config(config)
    if not resolved.security_summary:
        return NotificationResult(
            topic=TOPIC_SECURITY,
            skipped=True,
            skip_reason="security summaries disabled",
        )
    messages = format_security_summary(
        report,
        max_length=resolved.max_length,
        separator=resolved.split_separator,
    )
    return TelegramNotifier(resolved, transport=transport).send(messages, TOPIC_SECURITY)
