from src.notifications.telegram import (
    TOPIC_OSS_SUMMARY,
    ChatTarget,
    OssRunSummary,
    TelegramConfig,
    TelegramError,
    TelegramNotifier,
    format_oss_run_summary,
    notify_oss_run_summary,
)
from src.utils.text import escape_markdown_v2 as esc
from tests.notifications._fakes import FakeTransport


def _summary(queries=3, results=12, opportunities=2, duplicates=1):
    return OssRunSummary(queries, results, opportunities, duplicates)


def test_formats_actual_counts_deterministically():
    assert format_oss_run_summary(_summary()) == format_oss_run_summary(_summary())
    message = format_oss_run_summary(_summary())[0]
    assert "Queries: 3" in message
    assert "Results: 12" in message
    assert "Opportunities: 2" in message
    assert "Duplicates: 1" in message


def test_title_follows_product_language():
    message = format_oss_run_summary(_summary())[0]
    assert f"*{esc('🔵 GH-OPS · OSS HUNTER')}*" in message
    assert "OSS Hunter Complete" not in message
    assert "No new qualifying opportunities found." not in message


def test_zero_opportunities_omits_forbidden_empty_note():
    message = format_oss_run_summary(_summary(opportunities=0))[0]
    assert "No new qualifying opportunities found." not in message
    assert "Opportunities: 0" in message


def test_counts_are_not_hardcoded():
    assert "Queries: 7" in format_oss_run_summary(_summary(queries=7))[0]
    assert "Opportunities: 9" in format_oss_run_summary(_summary(opportunities=9))[0]


def test_toggle_disables_summary_delivery():
    result = notify_oss_run_summary(_summary(), config={"telegram": {"enabled": True}})
    assert result.skipped and result.skip_reason == "OSS summaries disabled"


def test_enabled_flag_allows_delivery():
    result = notify_oss_run_summary(
        _summary(),
        config={"telegram": {"enabled": True, "oss_summary": True}},
    )
    # Disabled or missing chat targets still skip safely rather than raise.
    assert result.topic == TOPIC_OSS_SUMMARY


def test_delivery_failure_is_isolated():
    transport = FakeTransport(fail_on={"1"}, error=TelegramError("unavailable"))
    notifier = TelegramNotifier(
        TelegramConfig(
            enabled=True,
            oss_summary=True,
            targets=(ChatTarget("1", (TOPIC_OSS_SUMMARY,)),),
        ),
        transport=transport,
    )
    result = notifier.send(format_oss_run_summary(_summary()), TOPIC_OSS_SUMMARY)
    assert not result.ok


def test_routes_to_oss_summary_topic():
    transport = FakeTransport()
    notifier = TelegramNotifier(
        TelegramConfig(
            enabled=True,
            oss_summary=True,
            targets=(ChatTarget("9", (TOPIC_OSS_SUMMARY,)),),
        ),
        transport=transport,
    )
    result = notifier.send(format_oss_run_summary(_summary()), TOPIC_OSS_SUMMARY)
    assert result.ok
    assert transport.sent[0][0] == "9"
