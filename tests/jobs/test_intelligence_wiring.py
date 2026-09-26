# ruff: noqa: F401,F811 -- `pipeline` must be imported at module level so
# pytest can resolve it as a cross-module fixture; F811 hits are parameters.
"""Behavioral tests wiring the intelligence layer into the jobs.

Covers: brief focus/headline fail-soft rendering, quiet-day silence, and the
OSS new/changed split with the radar report. Everything mocks the pipeline
boundary; no test contacts GitHub, Telegram, Laya, or any LLM.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.models import CurrentState
from src.core.monitor import MonitorCategory, MonitorResult, MonitorStatus
from src.jobs import jobs as jobs_module
from src.notifications import telegram as telegram_module
from tests.jobs.test_jobs import _result_ok, pipeline


def _ci_alert() -> MonitorResult:
    """One IMPORTANT CI alert (non-main branch), stamped with now."""
    return MonitorResult(
        monitor="ci",
        resource="owner/repo/run/1",
        status=MonitorStatus.ALERT,
        summary="CI failed",
        category=MonitorCategory.CI,
        metadata={"event_type": "failure", "branch": "develop", "url": "https://x"},
    )


@pytest.fixture()
def brief_env(monkeypatch: pytest.MonkeyPatch, pipeline):
    """Daily-brief environment: alerts mocked, report deliveries recorded."""
    record = {"briefs": [], "alerts": []}

    def fake_notify_report(title, blocks, **kw):
        record["briefs"].append((title, list(blocks), kw.get("topic")))
        return _result_ok(topic=kw.get("topic", "run_summary"))

    monkeypatch.setattr(jobs_module, "evaluate_events", lambda *a, **k: [])
    monkeypatch.setattr(jobs_module, "summarize_results", lambda results: {"total": len(results)})
    monkeypatch.setattr(
        jobs_module,
        "notify_monitor_alerts",
        lambda results, **kw: (record["alerts"].append(list(results)), _result_ok())[1],
    )
    monkeypatch.setattr(jobs_module, "notify_report", fake_notify_report)
    monkeypatch.setattr(
        telegram_module,
        "load_telegram_config",
        lambda cfg: SimpleNamespace(run_summary=True),
    )
    return record


class TestDailyBriefIntelligence:
    def test_focus_block_renders_and_headline_stays_silent(
        self, pipeline, brief_env, monkeypatch
    ):
        """An urgent pending event gets the deterministic focus block; the
        headline path is disabled in tests, so no 🧠 line may appear."""
        monkeypatch.setattr(jobs_module, "run_monitors", lambda s, c: [_ci_alert()])

        result = jobs_module.job_daily()

        assert result.success is True, result.errors.summary()
        assert len(brief_env["briefs"]) == 1
        title, blocks, topic = brief_env["briefs"][0]
        assert "DAILY BRIEF" in title
        assert topic == telegram_module.TOPIC_RUN_SUMMARY
        assert "🎯 Focus" in blocks
        assert "🔴 1 items need attention" in blocks
        assert not any(line.startswith("🧠") for line in blocks)
        assert "brief_notification" in result.data

    def test_headline_failure_is_fail_soft(self, pipeline, brief_env, monkeypatch):
        """A raising interpretation must not break the deterministic brief."""
        monkeypatch.setattr(jobs_module, "run_monitors", lambda s, c: [_ci_alert()])

        def boom(*a, **k):
            raise RuntimeError("interpretation exploded")

        import src.intelligence.interpret as interpret_module

        monkeypatch.setattr(interpret_module, "interpret_context", boom)

        result = jobs_module.job_daily()

        assert result.success is True
        assert len(brief_env["briefs"]) == 1
        _title, blocks, _topic = brief_env["briefs"][0]
        assert "🎯 Focus" in blocks
        assert not any(line.startswith("🧠") for line in blocks)

    def test_quiet_day_delivers_no_brief(self, pipeline, brief_env, monkeypatch):
        """No events and healthy collectors: silence, not an empty brief."""
        monkeypatch.setattr(jobs_module, "run_monitors", lambda s, c: [])

        result = jobs_module.job_daily()

        assert result.success is True
        assert brief_env["briefs"] == []
        assert "brief_notification" not in result.data


class TestOssRadar:
    @pytest.fixture()
    def oss_radar_env(self, monkeypatch: pytest.MonkeyPatch, pipeline):
        """Hunter + delivery fakes with radar reports recorded."""
        from src.intelligence.oss.hunter import HunterResult
        from tests.notifications._fakes import make_opportunity

        record = {
            "opportunity": make_opportunity(),
            "hunter": None,
            "opp_batches": [],
            "radar": [],
            "state": None,
        }

        def fake_run_hunter(client, config):
            return record["hunter"]

        def fake_notify_opps(opps, **kw):
            record["opp_batches"].append(list(opps))
            return _result_ok(topic="oss_opportunities")

        def fake_notify_report(title, blocks, **kw):
            record["radar"].append((title, list(blocks), kw.get("topic")))
            return _result_ok(topic=kw.get("topic", "oss_opportunities"))

        monkeypatch.setattr(jobs_module, "run_hunter", fake_run_hunter)
        monkeypatch.setattr(jobs_module, "notify_oss_opportunities", fake_notify_opps)
        monkeypatch.setattr(jobs_module, "notify_report", fake_notify_report)
        monkeypatch.setattr(
            jobs_module,
            "load_previous_state",
            lambda: record["state"] or CurrentState(),
        )
        record["hunter_factory"] = lambda opps: HunterResult(
            opportunities=opps, queries_run=1, queries_failed=0
        )
        return record

    def test_changed_opportunity_goes_to_radar_not_new_batch(
        self, pipeline, oss_radar_env
    ):
        from tests.notifications._fakes import make_opportunity

        # Run 1: brand-new opportunity, delivered on the pinned path.
        oss_radar_env["hunter"] = oss_radar_env["hunter_factory"](
            [oss_radar_env["opportunity"]]
        )
        first = jobs_module.job_oss_hunt()
        assert first.success is True
        assert first.data["new_opportunities"] == 1
        assert first.data["changed_opportunities"] == 0
        assert oss_radar_env["radar"] == []
        assert len(oss_radar_env["opp_batches"][0]) == 1

        # Run 2: same identity, comments changed -> radar only.
        oss_radar_env["state"] = pipeline["persisted"]
        oss_radar_env["hunter"] = oss_radar_env["hunter_factory"](
            [make_opportunity(comments=3)]
        )
        second = jobs_module.job_oss_hunt()
        assert second.success is True
        assert second.data["new_opportunities"] == 0
        assert second.data["changed_opportunities"] == 1
        assert oss_radar_env["opp_batches"][1] == []
        assert len(oss_radar_env["radar"]) == 1
        title, blocks, topic = oss_radar_env["radar"][0]
        assert title == "🧭 GH-OPS · OSS RADAR"
        assert topic == telegram_module.TOPIC_OSS_OPPORTUNITIES
        assert blocks[0] == "Changed opportunities"
        assert blocks[1].startswith("🔄 octo/example#42")

        # Run 3: unchanged after the radar baseline -> fully silent.
        oss_radar_env["state"] = pipeline["persisted"]
        oss_radar_env["hunter"] = oss_radar_env["hunter_factory"](
            [make_opportunity(comments=3)]
        )
        third = jobs_module.job_oss_hunt()
        assert third.success is True
        assert third.data["changed_opportunities"] == 0
        assert len(oss_radar_env["radar"]) == 1

    def test_legacy_record_is_adopted_without_reannouncing(
        self, pipeline, oss_radar_env
    ):
        """A record from before change-detection baselines silently adopts
        current values: no radar, no re-announce, future changes detectable."""
        from src.core.models import CurrentState as State

        previous = State(
            resources={
                "oss_opportunities": {
                    "octo/example#42": {"notified": True}
                }
            }
        )
        oss_radar_env["state"] = previous
        oss_radar_env["hunter"] = oss_radar_env["hunter_factory"](
            [oss_radar_env["opportunity"]]
        )

        result = jobs_module.job_oss_hunt()

        assert result.success is True
        assert result.data["new_opportunities"] == 0
        assert result.data["changed_opportunities"] == 0
        assert oss_radar_env["radar"] == []
        assert oss_radar_env["opp_batches"] == [[]]
        adopted = pipeline["persisted"].resources["oss_opportunities"]
        assert adopted["octo/example#42"]["comments"] == 0
