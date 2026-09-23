"""Notification delivery for gh-ops Phase 7.

Outbound-only delivery layer. Formats structured results from monitors, the OSS
hunter, and developer intelligence, and delivers them to Telegram.

Public API:
    - TelegramConfig: delivery configuration (contains no secrets)
    - ChatTarget: a delivery destination with optional topic subscription
    - TelegramTransport: the single Telegram HTTP exit point
    - TelegramNotifier: routing + failure-isolated delivery
    - NotificationResult: structured delivery outcome
    - format_monitor_alerts / format_oss_opportunities / format_developer_report
    - notify_monitor_alerts / notify_oss_opportunities / notify_developer_report

No inbound control: this package never polls Telegram, never registers
webhooks, and never accepts commands.
"""
from src.notifications.telegram import (
    # Topics
    TOPIC_ALERTS,
    TOPIC_DEVELOPER_REPORT,
    TOPIC_OSS_OPPORTUNITIES,
    TOPIC_OSS_SUMMARY,
    TOPIC_RUN_SUMMARY,
    # Configuration
    ChatTarget,
    # Delivery
    DeliveryAttempt,
    NotificationResult,
    # Summaries
    OssRunSummary,
    RunSummary,
    TelegramConfig,
    # Errors
    TelegramError,
    TelegramNotifier,
    # Transport
    TelegramTransport,
    # Formatting
    format_developer_report,
    format_monitor_alerts,
    format_oss_opportunities,
    format_oss_run_summary,
    format_run_summary,
    has_telegram_token,
    load_telegram_config,
    notify_developer_report,
    notify_monitor_alerts,
    notify_oss_opportunities,
    notify_oss_run_summary,
    notify_run_summary,
    resolve_telegram_token,
    # Entry points
    send_notification,
)

__all__ = [
    # Configuration
    "ChatTarget",
    "TelegramConfig",
    "has_telegram_token",
    "load_telegram_config",
    "resolve_telegram_token",
    # Errors
    "TelegramError",
    # Transport
    "TelegramTransport",
    # Delivery
    "DeliveryAttempt",
    "NotificationResult",
    "TelegramNotifier",
    # Formatting
    "format_developer_report",
    "format_monitor_alerts",
    "format_oss_opportunities",
    "format_oss_run_summary",
    "format_run_summary",
    # Entry points
    "send_notification",
    "notify_developer_report",
    "notify_monitor_alerts",
    "notify_oss_opportunities",
    "notify_oss_run_summary",
    "notify_run_summary",
    # Summaries
    "OssRunSummary",
    "RunSummary",
    # Topics
    "TOPIC_ALERTS",
    "TOPIC_DEVELOPER_REPORT",
    "TOPIC_OSS_OPPORTUNITIES",
    "TOPIC_OSS_SUMMARY",
    "TOPIC_RUN_SUMMARY",
]
