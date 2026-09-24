from src.notifications.telegram import (
    TOPIC_RUN_SUMMARY,
    ChatTarget,
    RunSummary,
    TelegramConfig,
    TelegramError,
    TelegramNotifier,
    format_run_summary,
    notify_run_summary,
)
from src.utils.text import escape_markdown_v2 as esc
from tests.notifications._fakes import FakeTransport


def _summary(repositories=6, items=12, alerts=0):
    return RunSummary("monitoring", repositories, items, alerts, True)


def test_formats_actual_counts_deterministically():
    assert format_run_summary(_summary()) == format_run_summary(_summary())
    message = format_run_summary(_summary())[0]
    assert "Repositories checked: 6" in message
    assert "Items collected: 12" in message
    assert "Notifications: 0" in message


def test_title_follows_product_language():
    message = format_run_summary(_summary())[0]
    assert f"*{esc('🔵 GH-OPS · MONITORING')}*" in message
    assert "Monitoring Complete" not in message
    assert "State:" not in message


def test_repository_count_is_not_hardcoded():
    assert "Repositories checked: 10" in format_run_summary(_summary(repositories=10))[0]


def test_toggle_disables_summary_delivery():
    result = notify_run_summary(_summary(), config={"telegram": {"enabled": True}})
    assert result.skipped and result.skip_reason == "run summaries disabled"


def test_delivery_failure_is_isolated():
    transport = FakeTransport(fail_on={"1"}, error=TelegramError("unavailable"))
    notifier = TelegramNotifier(
        TelegramConfig(
            enabled=True,
            run_summary=True,
            targets=(ChatTarget("1", (TOPIC_RUN_SUMMARY,)),),
        ),
        transport=transport,
    )
    result = notifier.send(format_run_summary(_summary(alerts=2)), TOPIC_RUN_SUMMARY)
    assert not result.ok
