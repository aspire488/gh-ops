"""Proactive sweep, CI auto-diagnosis, daily brief and runner ownership."""
from datetime import datetime, timedelta, timezone

from src.agent.sweep import Sweeper, compose_brief, diagnose_ci, diagnosis_lines
from src.intelligence import llm
from tests.agent.conftest import FakeClient
from tests.agent.test_bot import ME, make_bot, texts

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=20)).isoformat()


def iso(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


RUN_ROUTES = {
    "/repos/me/app/actions/runs": lambda p: {"workflow_runs": [
        {"id": 9, "name": "CI", "conclusion": "failure", "html_url": "https://github.com/me/app/actions/runs/9",
         "run_attempt": 1, "head_commit": {"message": "bump deps\n\nbody", "author": {"name": "joel"}}}]},
    "/repos/me/app/actions/runs/9/jobs": {"jobs": [
        {"id": 77, "name": "tests", "conclusion": "failure", "steps": [{"name": "pytest", "conclusion": "failure"}]},
        {"id": 78, "name": "lint", "conclusion": "success", "steps": []}]},
    "/repos/me/app/check-runs/77/annotations": [
        {"annotation_level": "failure", "path": "tests/test_x.py", "start_line": 12, "message": "AssertionError: 3 != 4\nmore"}],
}


class TestDiagnosis:
    def test_finds_failing_job_step_error_and_commit(self):
        diag = diagnose_ci(FakeClient(RUN_ROUTES), "me/app", "main", "CI")
        assert diag["failed_jobs"] == [{"job": "tests", "steps": ["pytest"], "errors": ["tests/test_x.py:12 AssertionError: 3 != 4"]}]
        lines = diagnosis_lines(diag)
        assert lines[0] == "Failing: `tests` → step `pytest`" and "AssertionError" in lines[1]
        assert 'Last commit: "bump deps" (joel)' in lines

    def test_no_matching_run_returns_none(self):
        assert diagnose_ci(FakeClient(RUN_ROUTES), "me/app", "main", "Other workflow") is None
        assert diagnosis_lines(None) == []


def sweep_routes(**over):
    routes = {
        "/search/issues": lambda p: {"items": [
            {"number": 4, "title": "Add parser", "html_url": "https://github.com/x/lib/pull/4",
             "repository_url": "https://api.github.com/repos/x/lib", "created_at": iso(6), "updated_at": iso(3)}]}
        if "approved" in p["q"] else {"items": []},
        "/user/repos": [{"full_name": "me/app", "fork": False, "pushed_at": iso(1)},
                        {"full_name": "me/old", "fork": False, "pushed_at": iso(90)},
                        {"full_name": "me/fork", "fork": True, "pushed_at": iso(1)}],
        "/repos/me/app": {"html_url": "https://github.com/me/app", "default_branch": "main", "stargazers_count": 40},
        "/repos/me/app/actions/runs": {"workflow_runs": [{"name": "CI", "conclusion": "failure"}]},
        "/repos/me/app/pulls": [{"number": 2, "title": "old pr", "updated_at": OLD, "html_url": "u2"}],
        "/repos/me/app/releases": [{"tag_name": "v1", "published_at": iso(40)}],
        "/repos/me/app/compare/v1...main": {"ahead_by": 12},
        "/repos/me/app/dependabot/alerts": [{"security_advisory": {"severity": "critical"},
                                             "dependency": {"package": {"name": "urllib3"}}}],
    }
    routes.update(over)
    return routes


class TestSweep:
    def test_finds_problems_and_nudges_ranked(self):
        findings = Sweeper(FakeClient(sweep_routes()), "me", [], star_memory={"me/app": 30}).run(NOW)
        kinds = [f.kind for f in findings]
        assert kinds[0] == "main_red" and set(kinds) == {"main_red", "security", "merge_ready", "stale_pr", "release_due", "stars"}
        merge = next(f for f in findings if f.kind == "merge_ready")
        assert "idle 3d" in merge.detail and merge.priority > 55  # nudge grows with age

    def test_skips_forks_and_inactive_repos_and_survives_a_broken_repo(self):
        client = FakeClient(sweep_routes())
        Sweeper(client, "me", ["me/broken"]).run(NOW)
        touched = {path for path, _ in client.calls}
        assert "/repos/me/fork" not in touched and "/repos/me/old" not in touched
        assert "/repos/me/broken" in touched  # attempted, failed (404), sweep carried on

    def test_star_memory_updates_and_needs_a_real_jump(self):
        memory = {"me/app": 39}
        findings = Sweeper(FakeClient(sweep_routes()), "me", [], star_memory=memory).run(NOW)
        assert not any(f.kind == "stars" for f in findings) and memory["me/app"] == 40


class TestBrief:
    def test_llm_writes_brief_from_facts(self):
        seen = []

        def chat(messages, **_):
            seen.append(messages[0]["content"])
            return llm.ChatResult("fake", "m", "Morning Joel! main is red on me/app.", ())

        findings = Sweeper(FakeClient(sweep_routes()), "me", []).run(NOW)
        text = compose_brief(findings, "Joel", NOW, "Morning", chat_fn=chat)
        assert text.startswith("Morning Joel") and '"main_red"' in seen[0] and "do not invent" in seen[0]

    def test_falls_back_to_deterministic_when_llm_down(self):
        def chat(*a, **k):
            raise llm.LlmUnavailableError("429")

        findings = Sweeper(FakeClient(sweep_routes()), "me", []).run(NOW)
        text = compose_brief(findings, "Joel", NOW, "Morning", chat_fn=chat)
        assert text.startswith("**Morning Joel") and "me/app" in text and "→" in text

    def test_calm_day(self):
        assert "Clean board" in compose_brief([], "Joel", NOW, "Morning")


class TestScheduling:
    def test_brief_due_once_per_local_day(self):
        bot, _ = make_bot()
        bot.cfg.daily_brief_at = "09:00"
        bot.cfg.policy.utc_offset = "+05:30"
        before = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)   # 08:30 IST
        after = datetime(2026, 11, 15, 3, 45, tzinfo=timezone.utc)   # 09:15 IST
        assert not bot.brief_due(before) and bot.brief_due(after)
        bot.last_brief = "2026-11-15"
        assert not bot.brief_due(after)

    def test_brief_command_sends_and_alert_includes_diagnosis(self):
        bot, tg = make_bot(routes=sweep_routes())
        bot.handle_update({"update_id": 1, "message": {"message_id": 1, "text": "/brief", "from": {"id": ME}, "chat": {"id": ME}}})
        assert "me/app" in texts(tg)[-1]

    def test_alert_carries_auto_diagnosis_and_links_the_failing_run(self):
        note = {"id": "1", "reason": "ci_activity", "updated_at": "2026-11-15T06:00:00Z",
                "repository": {"full_name": "me/app"},
                "subject": {"title": "CI workflow run failed for main branch", "type": "CheckSuite", "url": None}}
        bot, tg = make_bot(routes=RUN_ROUTES, notifications=[note])
        bot.cfg.runner = "daemon"
        bot.maybe_watch()
        alert = tg.sent()[0]
        assert "Failing: <code>tests</code>" in alert["text"] and "AssertionError" in alert["text"]
        assert alert["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://github.com/me/app/actions/runs/9"

    def test_actions_runner_silences_the_daemon(self):
        bot, tg = make_bot(notifications=[{"id": "1", "reason": "review_requested", "updated_at": "x",
                                           "repository": {"full_name": "a/b"}, "subject": {"title": "t", "type": "PullRequest"}}])
        bot.cfg.runner = "actions"
        bot.maybe_watch()
        assert tg.sent() == []

    def test_non_interactive_mode_has_no_mute_buttons(self):
        bot, tg = make_bot(notifications=[{"id": "1", "reason": "review_requested", "updated_at": "x",
                                           "repository": {"full_name": "a/b"}, "subject": {"title": "t", "type": "PullRequest"}}])
        bot.interactive = False
        bot.proactive_tick()
        rows = tg.sent()[0]["reply_markup"]["inline_keyboard"]
        assert not any(b.get("callback_data") for row in rows for b in row)
