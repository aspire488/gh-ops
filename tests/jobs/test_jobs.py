"""Tests for the Phase 8 job orchestration layer.

Every test mocks the pipeline boundary (config, client, state, collectors,
delivery). No test contacts GitHub, Telegram, or the filesystem state store.
"""
from __future__ import annotations

import json

import pytest

from src.core.dispatcher import Dispatcher, JobResult
from src.core.models import (
    CollectorResult,
    CollectorStatus,
    CurrentState,
    Snapshot,
)
from src.jobs import jobs as jobs_module
from src.jobs.jobs import (
    ALL_JOB_NAMES,
    JOB_DAILY,
    JOB_MONITORING,
    JOB_OSS_HUNT,
    JOB_SECURITY,
    JOB_STATUS,
    JOB_WEEKLY_REPORT,
    register_all_jobs,
)

# ── Fixtures and doubles ─────────────────────────────────────────


class FakeConfig:
    """Minimal Config stand-in exposing only what the jobs use."""

    def __init__(self, **blocks):
        self._blocks = blocks

    def get(self, *keys, default=None):
        value = self._blocks
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    @property
    def repositories(self):
        return []


class UnusedClient:
    """Client placeholder: the pipeline is mocked, so this is never called."""


def _empty_state() -> CurrentState:
    return CurrentState()


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch):
    """Replace every pipeline boundary with a deterministic fake.

    Returns a mutable record of what the job called.
    """
    # ``items`` lets a test make the snapshot realistic: jobs read the state
    # produced by applying the snapshot, not the previous state.
    record: dict = {"calls": [], "snapshot": None, "persisted": None, "items": {}}

    def fake_load_runtime_config(config_dir: str | None = None):
        record["calls"].append("load_runtime_config")
        return FakeConfig(monitoring={"monitors": {}}, oss_hunter={"enabled": True})

    def fake_build_snapshot(client, config, resources=()):
        record["calls"].append(("build_snapshot", tuple(resources)))
        items_for = record["items"]
        snapshot = Snapshot(results={
            name: CollectorResult(
                collector=name,
                status=CollectorStatus.SUCCESS,
                items=dict(items_for.get(name, {})),
                item_count=len(items_for.get(name, {})),
            )
            for name in resources
        })
        record["snapshot"] = snapshot
        return snapshot

    def fake_persist_state(state):
        record["calls"].append("persist_state")
        record["persisted"] = state

    monkeypatch.setattr(jobs_module, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(jobs_module, "GitHubClient", lambda *a, **k: UnusedClient())
    monkeypatch.setattr(jobs_module, "load_previous_state", _empty_state)
    monkeypatch.setattr(jobs_module, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(jobs_module, "persist_state", fake_persist_state)
    monkeypatch.setattr(jobs_module, "monitored_repository_names", lambda config: [])
    monkeypatch.setattr(jobs_module, "monitor_config", lambda config: {})
    monkeypatch.setattr(jobs_module, "hunter_config", lambda config: {"enabled": True})
    return record


@pytest.fixture()
def monitors(monkeypatch: pytest.MonkeyPatch):
    """Fake monitor execution and alert delivery; records delivery calls."""
    delivered: list = []

    def fake_run_monitors(state, config):
        return []

    def fake_summarize(results):
        return {"total": 0}

    def fake_notify_monitor_alerts(results, *, config=None, transport=None):
        delivered.append("alerts")
        return _result_ok()

    monkeypatch.setattr(jobs_module, "run_monitors", fake_run_monitors)
    monkeypatch.setattr(jobs_module, "summarize_results", fake_summarize)
    monkeypatch.setattr(jobs_module, "notify_monitor_alerts", fake_notify_monitor_alerts)
    return delivered


def _result_ok(**overrides):
    """Build a NotificationResult reporting a successful delivery."""
    from src.notifications.telegram import DeliveryAttempt, NotificationResult

    params = {
        "topic": "alerts",
        "sent": 1,
        "skipped": False,
        "attempts": (DeliveryAttempt(chat_id="424242", delivered=1, ok=True),),
        "errors": (),
    }
    params.update(overrides)
    return NotificationResult(**params)


def _result_failed():
    """Build a NotificationResult where delivery was attempted and failed."""
    from src.notifications.telegram import DeliveryAttempt, NotificationResult

    return NotificationResult(
        topic="alerts",
        sent=0,
        skipped=False,
        attempts=(DeliveryAttempt(chat_id="424242", delivered=0, ok=False),),
        errors=(({"error": "boom"}),),
    )


# ── Registration and dispatch ────────────────────────────────────


class TestRegistration:
    def test_registers_exactly_the_six_jobs(self):
        dispatcher = register_all_jobs(Dispatcher())
        assert dispatcher.list_jobs() == sorted(ALL_JOB_NAMES)

    def test_job_names_match_workflow_files(self):
        """Job names are the contract with .github/workflows/*.yml."""
        assert JOB_DAILY == "daily"
        assert JOB_MONITORING == "monitoring"
        assert JOB_WEEKLY_REPORT == "weekly-report"
        assert JOB_OSS_HUNT == "oss-hunt"
        assert JOB_SECURITY == "security"
        assert JOB_STATUS == "status"

    @pytest.mark.parametrize("name", ALL_JOB_NAMES)
    def test_each_job_is_dispatchable(self, name, pipeline, monitors, monkeypatch):
        """Every registered job must actually execute, not silently no-op."""
        monkeypatch.setattr(jobs_module, "hunter_config", lambda config: {"enabled": False})
        dispatcher = register_all_jobs(Dispatcher())
        result = dispatcher.run(name)
        assert isinstance(result, JobResult)
        assert result.job == name
        assert result.success is True, result.errors.summary()


# ── Job behaviour ────────────────────────────────────────────────


class TestDailyJob:
    def test_collects_daily_resource_set(self, pipeline, monitors):
        jobs_module.job_daily()
        collected = [c for c in pipeline["calls"] if isinstance(c, tuple)]
        assert collected == [
            ("build_snapshot", ("repos", "issues", "pulls", "releases", "workflows"))
        ]

    def test_persists_state(self, pipeline, monitors):
        jobs_module.job_daily()
        assert pipeline["persisted"] is not None
        assert "persist_state" in pipeline["calls"]

    def test_reports_monitor_summary(self, pipeline, monitors):
        result = jobs_module.job_daily()
        assert result.data["monitors"] == {"total": 0}
        assert "state" in result.data

    def test_fatal_when_config_invalid(self, pipeline, monitors, monkeypatch):
        def boom(config_dir=None):
            raise RuntimeError("bad yaml")

        monkeypatch.setattr(jobs_module, "load_runtime_config", boom)
        result = jobs_module.job_daily()
        assert result.success is False
        assert result.errors.count == 1

    def test_fatal_when_collection_raises(self, pipeline, monitors, monkeypatch):
        def boom(client, config, resources=()):
            raise RuntimeError("network down")

        monkeypatch.setattr(jobs_module, "build_snapshot", boom)
        result = jobs_module.job_daily()
        assert result.success is False
        assert not pipeline["calls"] or "persist_state" not in pipeline["calls"]

    def test_fatal_when_state_load_fails(self, pipeline, monitors, monkeypatch):
        def boom():
            raise RuntimeError("corrupt state")

        monkeypatch.setattr(jobs_module, "load_previous_state", boom)
        result = jobs_module.job_daily()
        assert result.success is False

    def test_fatal_when_state_save_fails(self, pipeline, monitors, monkeypatch):
        def boom(state):
            raise RuntimeError("disk full")

        monkeypatch.setattr(jobs_module, "persist_state", boom)
        result = jobs_module.job_daily()
        assert result.success is False


class TestPartialCollection:
    def test_failed_collector_does_not_fail_the_job(self, pipeline, monitors, monkeypatch):
        """A failed collector is partial, not fatal: other data is still kept."""

        def partial(client, config, resources=()):
            return Snapshot(results={
                "repos": CollectorResult(
                    collector="repos",
                    status=CollectorStatus.SUCCESS,
                    items={"octo/r": {"full_name": "octo/r"}},
                    item_count=1,
                ),
                "issues": CollectorResult(
                    collector="issues",
                    status=CollectorStatus.FAILURE,
                    items=None,
                    error="403 rate limited",
                    item_count=0,
                ),
            })

        monkeypatch.setattr(jobs_module, "build_snapshot", partial)
        result = jobs_module.job_daily()
        assert result.success is True
        # State was still persisted despite the partial failure.
        assert "persist_state" in pipeline["calls"]


class TestMonitoringJob:
    def test_collects_workflows_and_security_only(self, pipeline, monitors):
        jobs_module.job_monitoring()
        collected = [c for c in pipeline["calls"] if isinstance(c, tuple)]
        assert collected == [("build_snapshot", ("workflows", "security"))]


class TestWeeklyReportJob:
    @pytest.fixture()
    def report_env(self, monkeypatch: pytest.MonkeyPatch, pipeline, monitors):
        delivered = []
        pipeline["items"] = {"user": {"joel": {"login": "joel"}}}

        def fake_extract(state, events, username):
            return [], []

        def fake_generate(records, quality, **kwargs):
            class Report:
                to_dict = staticmethod(lambda: {"username": kwargs.get("username")})

            return Report()

        def fake_notify(report, *, config=None, transport=None):
            delivered.append(report)
            return _result_ok(topic="developer-report")  # noqa: E501

        monkeypatch.setattr(jobs_module, "extract_activity", fake_extract)
        monkeypatch.setattr(jobs_module, "generate_report", fake_generate)
        monkeypatch.setattr(jobs_module, "notify_developer_report", fake_notify)
        return delivered

    def test_uses_authenticated_username_from_state(self, report_env):
        result = jobs_module.job_weekly_report()
        assert result.success is True
        assert result.data["username"] == "joel"

    def test_delivers_report(self, report_env):
        jobs_module.job_weekly_report()
        assert len(report_env) == 1

    def test_collects_user_issues_and_pulls(self, pipeline, report_env):
        jobs_module.job_weekly_report()
        collected = [c for c in pipeline["calls"] if isinstance(c, tuple)]
        assert collected == [("build_snapshot", ("user", "issues", "pulls"))]


class TestOssHuntJob:
    def test_disabled_hunter_is_a_successful_no_op(self, pipeline, monkeypatch):
        monkeypatch.setattr(jobs_module, "hunter_config", lambda config: {"enabled": False})
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert result.data["disabled"] is True
        assert pipeline["persisted"] is None

    def test_delivers_opportunities(self, pipeline, monkeypatch):
        from src.intelligence.oss.hunter import HunterResult

        monkeypatch.setattr(
            jobs_module,
            "run_hunter",
            lambda client, config: HunterResult(
                opportunities=[], queries_run=2, queries_failed=0
            ),
        )
        delivered = []
        monkeypatch.setattr(
            jobs_module,
            "notify_oss_opportunities",
            lambda opps, **kw: (delivered.append(list(opps)), _result_ok())[1],
        )
        # Sanity: the fake result really is a success, otherwise this test
        # would pass vacuously.
        assert _result_ok().ok is True
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert result.data["queries_run"] == 2
        assert len(delivered) == 1

    def test_fatal_when_hunter_raises(self, pipeline, monkeypatch):
        def boom(client, config):
            raise RuntimeError("search failed")

        monkeypatch.setattr(jobs_module, "run_hunter", boom)
        result = jobs_module.job_oss_hunt()
        assert result.success is False

    def test_disabled_hunter_does_not_write_state(self, pipeline, monkeypatch):
        monkeypatch.setattr(jobs_module, "hunter_config", lambda config: {"enabled": False})
        jobs_module.job_oss_hunt()
        assert pipeline["persisted"] is None

    @pytest.fixture()
    def oss_env(self, monkeypatch: pytest.MonkeyPatch, pipeline):
        """Hunter + delivery fakes for an enabled run with one opportunity."""
        from src.intelligence.oss.hunter import HunterResult
        from tests.notifications._fakes import make_opportunity

        opp = make_opportunity()
        opp_two = make_opportunity(
            repo_full_name="other/repo",
            issue_number=7,
            issue_id=10007,
            html_url="https://github.com/other/repo/issues/7",
        )
        record = {
            "hunter": HunterResult(
                opportunities=[opp, opp_two],
                queries_run=1,
                queries_failed=0,
                total_issues_found=5,
                total_duplicates=3,
            ),
            "deliveries": [],
            "summaries": [],
            "result_ok": True,
        }

        def fake_run_hunter(client, config):
            return record["hunter"]

        def fake_notify_opps(opps, **kw):
            record["deliveries"].append(list(opps))
            if record["result_ok"]:
                return _result_ok(topic="oss_opportunities")
            return _result_failed()

        def fake_notify_summary(summary, **kw):
            record["summaries"].append(summary)
            return _result_ok(topic="oss_summary")

        monkeypatch.setattr(jobs_module, "run_hunter", fake_run_hunter)
        monkeypatch.setattr(jobs_module, "notify_oss_opportunities", fake_notify_opps)
        monkeypatch.setattr(jobs_module, "notify_oss_run_summary", fake_notify_summary)
        record["opportunity"] = opp
        record["opportunity_two"] = opp_two
        return record

    def test_persists_notified_identities_on_success(self, pipeline, oss_env):
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert "persist_state" in pipeline["calls"]
        notified = pipeline["persisted"].resources["oss_opportunities"]
        assert notified == {
            "octo/example#42": {"notified": True},
            "other/repo#7": {"notified": True},
        }
        assert result.data["new_opportunities"] == 2

    def test_second_run_filters_already_notified(self, pipeline, oss_env):
        jobs_module.job_oss_hunt()

        def load_state_with_previous():
            return pipeline["persisted"]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(jobs_module, "load_previous_state", load_state_with_previous)
        try:
            result = jobs_module.job_oss_hunt()
        finally:
            monkeypatch.undo()

        assert result.success is True
        assert result.data["new_opportunities"] == 0
        assert result.data["opportunities"] == 2
        # Exactly one batch call per run, even when nothing is new.
        assert len(oss_env["deliveries"]) == 2
        assert oss_env["deliveries"][1] == []
        # Previously notified identities are preserved.
        notified = pipeline["persisted"].resources["oss_opportunities"]
        assert set(notified) == {"octo/example#42", "other/repo#7"}

    def test_failed_delivery_does_not_mark_identities(self, pipeline, oss_env):
        oss_env["result_ok"] = False
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        notified = pipeline["persisted"].resources["oss_opportunities"]
        assert notified == {}
        assert result.data["notification"]["ok"] is False

    def test_delivery_exception_does_not_mark_identities(self, pipeline, oss_env, monkeypatch):
        def boom(opps, **kw):
            raise RuntimeError("telegram exploded")

        monkeypatch.setattr(jobs_module, "notify_oss_opportunities", boom)
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert pipeline["persisted"].resources["oss_opportunities"] == {}
        assert result.data["notification"] == {
            "error": "delivery raised an unexpected exception"
        }

    def test_preserves_other_resources_in_state(self, pipeline, oss_env):
        def previous_state():
            base = _empty_state()
            base.resources["repos"] = {"octo/r": {"full_name": "octo/r"}}
            return base

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(jobs_module, "load_previous_state", previous_state)
        try:
            jobs_module.job_oss_hunt()
        finally:
            monkeypatch.undo()

        resources = pipeline["persisted"].resources
        assert resources["repos"] == {"octo/r": {"full_name": "octo/r"}}
        assert "oss_opportunities" in resources

    def test_fatal_when_state_load_fails(self, pipeline, oss_env, monkeypatch):
        def boom():
            raise RuntimeError("corrupt state")

        monkeypatch.setattr(jobs_module, "load_previous_state", boom)
        result = jobs_module.job_oss_hunt()
        assert result.success is False

    def test_fatal_when_state_save_fails(self, pipeline, oss_env, monkeypatch):
        def boom(state):
            raise RuntimeError("disk full")

        monkeypatch.setattr(jobs_module, "persist_state", boom)
        result = jobs_module.job_oss_hunt()
        assert result.success is False

    def test_sends_oss_summary_once_per_run(self, pipeline, oss_env):
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert len(oss_env["summaries"]) == 1
        summary = oss_env["summaries"][0]
        assert summary.queries_run == 1
        assert summary.total_issues_found == 5
        assert summary.opportunities == 2
        assert summary.duplicates == 3
        assert "oss_summary_notification" in result.data

    def test_oss_summary_failure_does_not_fail_job(self, pipeline, oss_env, monkeypatch):
        def boom(summary, **kw):
            raise RuntimeError("telegram exploded")

        monkeypatch.setattr(jobs_module, "notify_oss_run_summary", boom)
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert "persist_state" in pipeline["calls"]
        assert result.data["oss_summary_notification"] == {
            "error": "delivery raised an unexpected exception"
        }

    def test_empty_opportunity_list_still_notifies_once(self, pipeline, monkeypatch):
        from src.intelligence.oss.hunter import HunterResult

        monkeypatch.setattr(
            jobs_module,
            "run_hunter",
            lambda client, config: HunterResult(opportunities=[], queries_run=1),
        )
        delivered = []
        summaries = []
        monkeypatch.setattr(
            jobs_module,
            "notify_oss_opportunities",
            lambda opps, **kw: (delivered.append(list(opps)), _result_ok())[1],
        )
        monkeypatch.setattr(
            jobs_module,
            "notify_oss_run_summary",
            lambda summary, **kw: (summaries.append(summary), _result_ok())[1],
        )
        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert len(delivered) == 1
        assert delivered[0] == []
        assert len(summaries) == 1
        assert pipeline["persisted"] is not None


class TestSecurityJob:
    @pytest.fixture(autouse=True)
    def _mock_security_notify(self, monkeypatch: pytest.MonkeyPatch):
        """Record security deliveries; never touch Telegram."""
        deliveries: list = []
        summaries: list = []
        monkeypatch.setattr(
            jobs_module,
            "notify_security_alerts",
            lambda findings, **kw: (deliveries.append(list(findings)), _result_ok())[1],
        )
        monkeypatch.setattr(
            jobs_module,
            "notify_security_summary",
            lambda report, **kw: (summaries.append(report), _result_ok())[1],
        )
        self.deliveries = deliveries
        self.summaries = summaries

    def test_counts_alerts_from_state(self, pipeline):
        pipeline["items"] = {
            "security": {"octo/repo/dependabot/1": {"id": 1, "state": "open"}}
        }
        result = jobs_module.job_security()
        assert result.success is True
        assert result.data["alerts"] == 1

    def test_collects_security_only(self, pipeline):
        jobs_module.job_security()
        collected = [c for c in pipeline["calls"] if isinstance(c, tuple)]
        assert collected == [("build_snapshot", ("security",))]

    def test_delivers_new_actionable_and_marks_notified(self, pipeline):
        pipeline["items"] = {
            "security": {
                "octo/repo/dependabot/1": {
                    "severity": "critical",
                    "state": "open",
                    "package_name": "lodash",
                    "summary": "bad",
                }
            }
        }
        result = jobs_module.job_security()
        assert result.success is True
        assert result.data["actionable"] == 1
        assert result.data["new_actionable"] == 1
        assert len(self.deliveries) == 1
        assert [f.identity for f in self.deliveries[0]] == ["octo/repo/dependabot/1"]
        assert pipeline["persisted"] is not None
        assert "octo/repo/dependabot/1" in pipeline["persisted"].resources["security_notified"]

    def test_new_actionable_skipped_when_previously_notified(self, pipeline, monkeypatch):
        from src.core.models import CurrentState

        previous = CurrentState(resources={
            "security": {},
            "security_notified": {"octo/repo/dependabot/1": {"notified": True}},
        })
        monkeypatch.setattr(jobs_module, "load_previous_state", lambda: previous)
        pipeline["items"] = {
            "security": {
                "octo/repo/dependabot/1": {"severity": "critical", "state": "open"}
            }
        }
        result = jobs_module.job_security()
        assert result.success is True
        assert result.data["actionable"] == 1
        assert result.data["new_actionable"] == 0
        assert self.deliveries == [[]]

    def test_failed_delivery_does_not_mark_notified(self, pipeline, monkeypatch):
        monkeypatch.setattr(
            jobs_module, "notify_security_alerts", lambda findings, **kw: _result_failed()
        )
        pipeline["items"] = {
            "security": {
                "octo/repo/dependabot/1": {"severity": "critical", "state": "open"}
            }
        }
        result = jobs_module.job_security()
        assert result.success is True
        notified = pipeline["persisted"].resources.get("security_notified", {})
        assert "octo/repo/dependabot/1" not in notified

    def test_raising_delivery_does_not_fail_job(self, pipeline, monkeypatch):
        def boom(findings, **kw):
            raise RuntimeError("telegram exploded")

        monkeypatch.setattr(jobs_module, "notify_security_alerts", boom)
        pipeline["items"] = {
            "security": {
                "octo/repo/dependabot/1": {"severity": "critical", "state": "open"}
            }
        }
        result = jobs_module.job_security()
        assert result.success is True
        assert "persist_state" in pipeline["calls"]

    def test_summary_delivery_is_reported(self, pipeline):
        pipeline["items"] = {
            "security": {"octo/repo/dependabot/1": {"severity": "low", "state": "open"}}
        }
        result = jobs_module.job_security()
        assert result.success is True
        assert len(self.summaries) == 1
        assert "security_summary_notification" in result.data
        assert result.data["by_severity"] == {"low": 1}

    def test_fatal_when_state_unreadable(self, pipeline, monkeypatch):
        def boom():
            raise RuntimeError("unreadable")

        monkeypatch.setattr(jobs_module, "load_previous_state", boom)
        result = jobs_module.job_security()
        assert result.success is False


class TestStatusJob:
    def test_is_read_only(self, pipeline):
        """status must collect nothing and write nothing."""
        result = jobs_module.job_status()
        assert result.success is True
        assert not [c for c in pipeline["calls"] if isinstance(c, tuple)]
        assert pipeline["persisted"] is None

    def test_reports_job_list(self, pipeline):
        result = jobs_module.job_status()
        assert result.data["jobs"] == list(ALL_JOB_NAMES)

    def test_fatal_when_state_unreadable(self, pipeline, monkeypatch):
        def boom():
            raise RuntimeError("unreadable")

        monkeypatch.setattr(jobs_module, "load_previous_state", boom)
        result = jobs_module.job_status()
        assert result.success is False


# ── Failure isolation ────────────────────────────────────────────


class TestNotificationFailureIsolation:
    def test_alert_delivery_failure_does_not_fail_the_job(self, pipeline, monkeypatch):
        """A delivery problem must never discard collected state."""
        monkeypatch.setattr(
            jobs_module, "notify_monitor_alerts", lambda results, **kw: _result_failed()
        )
        monkeypatch.setattr(jobs_module, "run_monitors", lambda state, config: [])
        monkeypatch.setattr(jobs_module, "summarize_results", lambda results: {"total": 0})

        result = jobs_module.job_daily()
        assert result.success is True
        assert "persist_state" in pipeline["calls"]

    def test_alert_delivery_exception_is_contained(self, pipeline, monkeypatch):
        """A raising delivery must not fail the job nor lose collected state."""

        def boom(results, **kw):
            raise RuntimeError("telegram exploded")

        monkeypatch.setattr(jobs_module, "notify_monitor_alerts", boom)
        monkeypatch.setattr(jobs_module, "run_monitors", lambda state, config: [])
        monkeypatch.setattr(jobs_module, "summarize_results", lambda results: {"total": 0})

        dispatcher = register_all_jobs(Dispatcher())
        result = dispatcher.run(JOB_DAILY)
        assert result.success is True
        assert "persist_state" in pipeline["calls"]
        assert result.data["notification"] == {
            "error": "delivery raised an unexpected exception"
        }

    def test_report_delivery_exception_is_contained(self, pipeline, monkeypatch):
        def boom(report, **kw):
            raise RuntimeError("telegram exploded")

        class FakeReport:
            def to_dict(self):
                return {"username": "joel"}

        monkeypatch.setattr(jobs_module, "extract_activity", lambda *a, **k: ([], []))
        monkeypatch.setattr(jobs_module, "generate_report", lambda *a, **k: FakeReport())
        monkeypatch.setattr(jobs_module, "notify_developer_report", boom)

        result = jobs_module.job_weekly_report()
        assert result.success is True
        assert "report" in result.data

    def test_delivery_failure_logs_no_token(self, pipeline, monkeypatch):
        """A delivery exception must not put the bot token in the logs.

        The token lives in the request URL, so an exception message can embed it.
        Only the exception type is logged, which is why this holds.
        """
        from tests.notifications._fakes import TEST_TOKEN, assert_no_token, captured_logs

        def boom(results, **kw):
            # Simulate a transport error whose text embeds the request URL.
            raise RuntimeError(
                f"connection failed: https://api.telegram.org/bot{TEST_TOKEN}/sendMessage"
            )

        monkeypatch.setattr(jobs_module, "notify_monitor_alerts", boom)
        monkeypatch.setattr(jobs_module, "run_monitors", lambda state, config: [])
        monkeypatch.setattr(jobs_module, "summarize_results", lambda results: {"total": 0})

        with captured_logs("jobs") as messages:
            result = jobs_module.job_daily()

        assert result.success is True
        for message in messages:
            assert_no_token(message)
            assert "api.telegram.org" not in message, message
        assert_no_token(json.dumps(result.data, default=str))

    def test_oss_delivery_exception_is_contained(self, pipeline, monkeypatch):
        from src.intelligence.oss.hunter import HunterResult

        def boom(opps, **kw):
            raise RuntimeError("telegram exploded")

        monkeypatch.setattr(
            jobs_module,
            "run_hunter",
            lambda client, config: HunterResult(opportunities=[], queries_run=1),
        )
        monkeypatch.setattr(jobs_module, "notify_oss_opportunities", boom)

        result = jobs_module.job_oss_hunt()
        assert result.success is True
        assert result.data["queries_run"] == 1
        assert "persist_state" in pipeline["calls"]
